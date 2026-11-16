"""Live acquisition for the BROADMIND headset on top of BrainFlow.

What this module does
---------------------
* Opens a board session through BrainFlow (synthetic or OpenBCI-compatible
  serial path), starts/stops the stream and pulls samples.
* Keeps a fixed-size NumPy ring buffer of recent data for cheap look-back.
* Provides a blocking ``collect`` helper and a non-blocking callback API
  (``subscribe``) that runs a background polling thread.
* Lets callers insert *markers* into the running stream (used for artefact
  labels) without touching the recorded samples.
* Assembles a :class:`broadmind.export.Recording` for export.

What this module deliberately does NOT do
-----------------------------------------
* No filtering, referencing, artefact removal, montage inference or
  interpretation. Artefact *labels* are produced by
  :mod:`broadmind.artifacts`; the signal itself is never rewritten here.
* No BLE transport: BroadMind's Bluetooth protocol is not public (see
  :mod:`broadmind.board`).
* No firmware upload, impedance check, gain configuration or battery
  management - those need device-specific commands that are not documented
  for this hardware.
* No clinical claims and no patient-data handling beyond "write a local file".
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Sequence

import numpy as np

from brainflow.board_shim import BoardShim

from .artifacts import ARTIFACT_MARKER_CODES, Artifact, label_artifacts
from .board import BoardConfig, DEVICE, make_board_config

__all__ = [
    "Block",
    "RingBuffer",
    "Stream",
    "open_stream",
]


# ---------------------------------------------------------------------------
# Ring buffer
# ---------------------------------------------------------------------------


class RingBuffer:
    """Fixed-capacity circular buffer of EEG samples.

    Parameters
    ----------
    n_channels:
        Number of rows (channels).
    capacity:
        Capacity in samples (not seconds).

    Notes
    -----
    Data is stored as ``float64`` and copied in and out, so callers cannot
    corrupt the buffer accidentally. Thread-safe for append/read.
    """

    def __init__(self, n_channels: int, capacity: int) -> None:
        n_channels = int(n_channels)
        capacity = int(capacity)
        if n_channels <= 0:
            raise ValueError(f"n_channels must be positive, got {n_channels}")
        if capacity <= 0:
            raise ValueError(f"capacity must be positive, got {capacity}")
        self._n_channels = n_channels
        self._capacity = capacity
        self._buffer = np.zeros((n_channels, capacity), dtype=np.float64)
        self._write = 0
        self._size = 0
        self._total = 0
        self._lock = threading.Lock()

    @property
    def n_channels(self) -> int:
        return self._n_channels

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def total_appended(self) -> int:
        """Total samples ever appended (a monotonic global sample counter)."""
        return self._total

    def __len__(self) -> int:
        return self._size

    def clear(self) -> None:
        """Drop all stored samples (the global counter keeps counting)."""
        with self._lock:
            self._buffer.fill(0.0)
            self._write = 0
            self._size = 0

    def append(self, block: np.ndarray) -> None:
        """Append a ``(n_channels, n_samples)`` block, keeping the newest samples."""
        arr = np.asarray(block, dtype=np.float64)
        if arr.ndim != 2:
            raise ValueError(f"block must be 2-D (channels, samples), got {arr.shape}")
        if arr.shape[0] != self._n_channels:
            raise ValueError(
                f"block has {arr.shape[0]} rows, buffer has {self._n_channels} channels"
            )
        n = arr.shape[1]
        if n == 0:
            return
        with self._lock:
            self._total += n
            if n >= self._capacity:
                self._buffer[:] = arr[:, -self._capacity:]
                self._write = 0
                self._size = self._capacity
                return
            first = min(n, self._capacity - self._write)
            self._buffer[:, self._write:self._write + first] = arr[:, :first]
            rest = n - first
            if rest:
                self._buffer[:, :rest] = arr[:, first:]
            self._write = (self._write + n) % self._capacity
            self._size = min(self._capacity, self._size + n)

    def _ordered(self) -> np.ndarray:
        """Return stored samples in chronological order (internal copy)."""
        if self._size == 0:
            return np.zeros((self._n_channels, 0), dtype=np.float64)
        start = (self._write - self._size) % self._capacity
        idx = (start + np.arange(self._size)) % self._capacity
        return self._buffer[:, idx]

    def all(self) -> np.ndarray:
        """Return every stored sample in chronological order (copy)."""
        with self._lock:
            return self._ordered()

    def newest(self, n_samples: int | None = None) -> np.ndarray:
        """Return the newest ``n_samples`` samples in chronological order (copy)."""
        with self._lock:
            data = self._ordered()
        if n_samples is None:
            return data
        n_samples = int(n_samples)
        if n_samples <= 0:
            return data[:, :0]
        return data[:, -n_samples:]


# ---------------------------------------------------------------------------
# Data block
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Block:
    """A contiguous chunk of acquired data.

    Attributes
    ----------
    data:
        ``(n_channels, n_samples)`` in microvolts, device channel order.
    timestamps:
        ``(n_samples,)`` UNIX timestamps in seconds, as reported by BrainFlow.
    markers:
        ``(n_samples,)`` raw marker-channel values (0 when no marker).
    sampling_rate:
        Sampling rate in Hz.
    start_index:
        Global sample index of the first sample since the stream started.
    """

    data: np.ndarray
    timestamps: np.ndarray
    markers: np.ndarray
    sampling_rate: float
    start_index: int = 0

    @property
    def n_samples(self) -> int:
        return int(self.data.shape[1])

    @property
    def n_channels(self) -> int:
        return int(self.data.shape[0])

    @property
    def duration_s(self) -> float:
        return self.n_samples / float(self.sampling_rate)

    @property
    def start_time(self) -> float | None:
        """UNIX timestamp of the first sample, or None for an empty block."""
        return float(self.timestamps[0]) if self.timestamps.size else None


# ---------------------------------------------------------------------------
# Stream
# ---------------------------------------------------------------------------


class Stream:
    """A BrainFlow session for the BroadMind 8-channel device.

    Typical use::

        with open_stream() as stream:              # synthetic board
            block = stream.collect(seconds=5)
            print(block.data.shape)

    Parameters
    ----------
    config:
        A :class:`broadmind.board.BoardConfig`; defaults to the synthetic board.
    buffer_seconds:
        Size of the look-back ring buffer, in seconds.
    poll_interval:
        How often the polling loop asks BrainFlow for new data, in seconds.
    disable_logger:
        Silence BrainFlow's C++ logger (default True; keeps CLI output clean).
    """

    def __init__(
        self,
        config: BoardConfig | None = None,
        buffer_seconds: float = 300.0,
        poll_interval: float = 0.05,
        disable_logger: bool = True,
    ) -> None:
        self.config = config or make_board_config("synthetic")
        self.poll_interval = float(poll_interval)
        self.disable_logger = bool(disable_logger)
        self._board: BoardShim | None = None
        self._prepared = False
        self._streaming = False
        capacity = int(round(float(buffer_seconds) * self.sampling_rate))
        self.buffer = RingBuffer(DEVICE.n_channels, max(1, capacity))
        self._callback_thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self.callback_errors: list[BaseException] = []

    # -- lifecycle ----------------------------------------------------------

    @property
    def sampling_rate(self) -> float:
        return float(self.config.sampling_rate())

    @property
    def channel_names(self) -> tuple[str, ...]:
        return self.config.channel_names()

    @property
    def is_prepared(self) -> bool:
        return self._prepared

    @property
    def is_streaming(self) -> bool:
        return self._streaming

    def connect(self) -> "Stream":
        """Prepare the BrainFlow session (does not start streaming)."""
        if self._prepared:
            return self
        if self.disable_logger:
            BoardShim.disable_board_logger()
        board = BoardShim(self.config.board_id, self.config.input_params())
        board.prepare_session()
        self._board = board
        self._prepared = True
        return self

    def start(self) -> "Stream":
        """Prepare the session if needed and start the data stream."""
        if self._streaming:
            return self
        self.connect()
        assert self._board is not None
        self._board.start_stream()
        self._streaming = True
        return self

    def stop(self) -> None:
        """Stop streaming but keep the session prepared."""
        if self._board is not None and self._streaming:
            self._board.stop_stream()
        self._streaming = False

    def close(self) -> None:
        """Stop any callback thread, stop streaming and release the session."""
        self.unsubscribe()
        if self._board is not None:
            try:
                if self._streaming:
                    self._board.stop_stream()
            finally:
                if self._prepared:
                    self._board.release_session()
        self._streaming = False
        self._prepared = False
        self._board = None

    def __enter__(self) -> "Stream":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- low level reads ----------------------------------------------------

    def _require_board(self) -> BoardShim:
        if self._board is None:
            raise RuntimeError("stream is not connected; call connect() or start()")
        return self._board

    def _extract(self, raw: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Split a BrainFlow board buffer into (channels, timestamps, markers)."""
        if raw.ndim != 2 or raw.shape[1] == 0:
            n_ch = len(self.config.eeg_rows())
            return (
                np.zeros((n_ch, 0), dtype=np.float64),
                np.zeros((0,), dtype=np.float64),
                np.zeros((0,), dtype=np.float64),
            )
        rows = list(self.config.eeg_rows())
        data = np.asarray(raw[rows, :], dtype=np.float64)
        timestamps = np.asarray(raw[self.config.timestamp_row(), :], dtype=np.float64)
        markers = np.asarray(raw[self.config.marker_row(), :], dtype=np.float64)
        return data, timestamps, markers

    def read_new(self) -> np.ndarray:
        """Return all samples that arrived since the last call (destructive).

        The returned array is the raw BrainFlow board buffer, shape
        ``(n_rows, n_samples)``. Use :meth:`read_eeg` for just the channels.
        """
        board = self._require_board()
        return np.asarray(board.get_board_data(), dtype=np.float64)

    def read_eeg(self) -> Block:
        """Return new samples as a :class:`Block` (destructive read)."""
        start = self.buffer.total_appended
        raw = self.read_new()
        data, ts, markers = self._extract(raw)
        if data.shape[1]:
            self.buffer.append(data)
        return Block(
            data=data,
            timestamps=ts,
            markers=markers,
            sampling_rate=self.sampling_rate,
            start_index=start,
        )

    def read_current(self, n_samples: int | None = None) -> np.ndarray:
        """Peek at up to ``n_samples`` most recent samples *without* consuming them.

        Accepts 1..n; BrainFlow caps the request at its internal buffer size.
        """
        board = self._require_board()
        if n_samples is None:
            n_samples = int(round(self.sampling_rate))
        raw = np.asarray(board.get_current_board_data(int(n_samples)), dtype=np.float64)
        data, _, _ = self._extract(raw)
        return data

    # -- blocking collection ------------------------------------------------

    def collect(
        self,
        seconds: float,
        timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> Block:
        """Collect ``seconds`` of data and return it as a :class:`Block`.

        The result holds exactly ``round(seconds * sampling_rate)`` samples when
        enough data arrives (extras are trimmed from the tail). The ring buffer
        receives everything that arrived.

        Raises
        ------
        RuntimeError
            If the stream is not started, or a callback subscription is running
            (two consumers would race for the same BrainFlow buffer).
        TimeoutError
            If not enough samples arrive before the deadline.
        """
        if not self._streaming:
            raise RuntimeError("stream is not streaming; call start() first")
        if self._callback_thread is not None:
            raise RuntimeError(
                "cannot collect() while a subscription is active; "
                "call unsubscribe() first"
            )
        seconds = float(seconds)
        if seconds <= 0:
            raise ValueError(f"seconds must be positive, got {seconds}")
        target = int(round(seconds * self.sampling_rate))
        deadline = time.monotonic() + (seconds + 10.0 if timeout is None else float(timeout))

        data_parts: list[np.ndarray] = []
        ts_parts: list[np.ndarray] = []
        marker_parts: list[np.ndarray] = []
        collected = 0
        start_index = self.buffer.total_appended

        while collected < target:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"only {collected}/{target} samples arrived within the deadline "
                    f"({seconds:g}s requested)"
                )
            raw = np.asarray(self._require_board().get_board_data(), dtype=np.float64)
            if raw.shape[1]:
                data, ts, markers = self._extract(raw)
                if data.shape[1]:
                    data_parts.append(data)
                    ts_parts.append(ts)
                    marker_parts.append(markers)
                    self.buffer.append(data)
                    collected += data.shape[1]
            if on_progress is not None:
                on_progress(min(collected, target), target)
            if collected < target:
                time.sleep(self.poll_interval)

        data = np.concatenate(data_parts, axis=1)[:, :target]
        timestamps = np.concatenate(ts_parts)[:target]
        markers = np.concatenate(marker_parts)[:target]
        return Block(
            data=data,
            timestamps=timestamps,
            markers=markers,
            sampling_rate=self.sampling_rate,
            start_index=start_index,
        )

    # -- callback API -------------------------------------------------------

    def subscribe(
        self,
        callback: Callable[[Block], None],
        poll_interval: float | None = None,
    ) -> "Stream":
        """Start a background thread that delivers new data to ``callback``.

        The callback receives a :class:`Block` per chunk (usually a few samples
        at 250 Hz). Exceptions raised by the callback are collected in
        :attr:`callback_errors` and the read loop continues. Data is also
        appended to the ring buffer.

        Only one subscription may be active; ``collect`` cannot be used while
        one is running.
        """
        if not callable(callback):
            raise TypeError("callback must be callable")
        if self._callback_thread is not None:
            raise RuntimeError("a subscription is already active")
        if not self._streaming:
            raise RuntimeError("stream is not streaming; call start() first")

        interval = self.poll_interval if poll_interval is None else float(poll_interval)
        stop_event = threading.Event()
        self._stop_event = stop_event
        self.callback_errors = []

        def _loop() -> None:
            while not stop_event.is_set():
                try:
                    raw = np.asarray(
                        self._require_board().get_board_data(), dtype=np.float64
                    )
                except Exception as exc:  # pragma: no cover - hardware failure path
                    self.callback_errors.append(exc)
                    return
                if raw.shape[1]:
                    data, ts, markers = self._extract(raw)
                    if data.shape[1]:
                        self.buffer.append(data)
                        block = Block(
                            data=data,
                            timestamps=ts,
                            markers=markers,
                            sampling_rate=self.sampling_rate,
                            start_index=self.buffer.total_appended - data.shape[1],
                        )
                        try:
                            callback(block)
                        except Exception as exc:  # noqa: BLE001 - reported to caller
                            self.callback_errors.append(exc)
                stop_event.wait(interval)

        thread = threading.Thread(target=_loop, name="broadmind-stream", daemon=True)
        thread.start()
        self._callback_thread = thread
        return self

    def unsubscribe(self, timeout: float = 2.0) -> None:
        """Stop the callback thread, if any."""
        if self._stop_event is not None:
            self._stop_event.set()
        thread = self._callback_thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._callback_thread = None
        self._stop_event = None

    # -- markers ------------------------------------------------------------

    def insert_marker(self, value: int) -> None:
        """Insert a marker into the live stream at the current position.

        Markers are how OpenBCI-compatible boards tag events without touching
        the samples. They are *added*: no data is deleted. Note that a marker
        lands at the stream's current sample, so it cannot retroactively mark
        an event that already passed - use the offline exporters for that.
        """
        board = self._require_board()
        value = int(value)
        if not 0 <= value <= 255:
            raise ValueError(f"marker value must fit one byte (0..255), got {value}")
        board.insert_marker(float(value))

    def mark_artifact(self, artifact: Artifact) -> int:
        """Insert the standard marker code for an artefact label; return the code."""
        try:
            code = ARTIFACT_MARKER_CODES[artifact.label]
        except KeyError as exc:
            known = ", ".join(ARTIFACT_MARKER_CODES)
            raise ValueError(
                f"no marker code for label {artifact.label!r}; known labels: {known}"
            ) from exc
        self.insert_marker(code)
        return code

    def mark_artifacts(self, artifacts: Iterable[Artifact]) -> list[int]:
        """Insert marker codes for a batch of artefacts; return the codes."""
        return [self.mark_artifact(a) for a in artifacts]

    # -- convenience --------------------------------------------------------

    def record(
        self,
        seconds: float,
        detect_artifacts: bool = False,
        labels: Sequence[str] | None = None,
    ):
        """Collect ``seconds`` of data and return a ready-to-export Recording.

        Parameters
        ----------
        seconds:
            Recording duration.
        detect_artifacts:
            Run :func:`broadmind.artifacts.label_artifacts` on the recorded
            data and attach the labels (the samples themselves are untouched).
        labels:
            Limit artefact detection to these labels (default: all).
        """
        from .export import Recording  # local import: avoids an import cycle

        block = self.collect(seconds)
        metadata = {
            "sdk": "broadmind-eeg",
            "transport": self.config.transport.value,
            "transport_status": self.config.status,
            "board_id": self.config.board_id,
            "device": DEVICE.name,
            "ground": DEVICE.ground,
            "reference": DEVICE.reference,
            "note": self.config.note,
            "privacy": "recorded locally; nothing is uploaded",
        }
        started_at = None
        if block.timestamps.size:
            started_at = datetime.fromtimestamp(
                float(block.timestamps[0]), tz=timezone.utc
            )
        recording = Recording(
            data=block.data,
            sampling_rate=self.sampling_rate,
            channel_names=list(self.channel_names),
            timestamps_s=block.timestamps,
            started_at=started_at,
            metadata=metadata,
        )
        if detect_artifacts:
            found = label_artifacts(
                block.data,
                self.sampling_rate,
                channel_names=self.channel_names,
                labels=labels,
            )
            recording.artifacts = list(found)
            metadata["artifacts"] = [a.to_dict() for a in found]
        return recording

    # -- reporting ----------------------------------------------------------

    def describe(self) -> str:
        """Multi-line summary of this stream."""
        return "\n".join(
            [
                self.config.describe(),
                f"buffer        : {len(self.buffer)}/{self.buffer.capacity} samples",
                f"prepared      : {self._prepared}",
                f"streaming     : {self._streaming}",
            ]
        )


# ---------------------------------------------------------------------------
# Convenience opener
# ---------------------------------------------------------------------------


def open_stream(
    transport: str = "synthetic",
    serial_port: str | None = None,
    buffer_seconds: float = 300.0,
    board_id: int | None = None,
    **stream_kwargs,
) -> Stream:
    """Create and start a :class:`Stream` (context-manager friendly).

    Example::

        with open_stream() as stream:          # BrainFlow synthetic board
            block = stream.collect(2)
    """
    config = make_board_config(
        transport, serial_port=serial_port, board_id=board_id
    )
    stream = Stream(config=config, buffer_seconds=buffer_seconds, **stream_kwargs)
    return stream.start()

