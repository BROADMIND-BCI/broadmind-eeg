"""Export and reload EEG recordings: CSV, EDF(+) and JSON.

What this module does
---------------------
* Defines :class:`Recording`, the in-memory container handed between the
  stream layer, the CLI and the file writers (channels x samples in
  microvolts, plus channel names, timestamps, metadata and artefact labels).
* Writes CSV (human-inspectable, with a machine-readable metadata header),
  EDF/EDF+ (with artefact labels as standard EDF+ annotations) and JSON.
* Reads each format back, so a recording survives a write/read round trip.
* Dispatches on file extension via :func:`export` / :func:`load`.

What this module deliberately does NOT do
-----------------------------------------
* No signal processing on export: samples are written as given. No filtering,
  resampling, re-referencing or artefact removal happens here. Artefact labels
  are *added* (CSV marker column, EDF+ annotations, JSON field); data is never
  dropped.
* No proprietary container formats, no cloud upload, no de-identification
  beyond leaving the EDF patient field anonymised ("X") unless you supply an ID
  yourself. Participants' identifiers are the caller's responsibility.
* No MNE/EEGLAB *export code*: it writes standards-compliant files that those
  tools import. Compatibility is verified for MNE by the project maintainers
  (see README); EEGLAB is untested (no MATLAB on the build machine).
* No codec for the device's raw binary stream - that is out of scope.

Format notes
------------
* **CSV** - first two lines are ``#`` comments, the second is ``# meta: {json}``;
  then a header row ``time_s,<channels>,marker``. Floats are written with 9
  decimals. Round trip is accurate to ~1e-9 uV.
* **EDF** - 16-bit EDF+ via ``edfio``. EDF data records are one second, so a
  recording whose length is not a whole number of seconds is zero-padded at
  the end (reported in the returned info dict). Quantisation error depends on
  the per-channel physical range and is typically ~0.001-0.01 uV. Artefacts
  become EDF+ annotations carrying label, onset and duration.
* **JSON** - exact float round trip via Python's JSON encoder; convenient for
  small recordings and web interchange, wasteful for long sessions.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

import edfio
import numpy as np

from . import montage
from .artifacts import (
    ARTIFACT_LABELS,
    MARKER_CODE_LABELS,
    Artifact,
    artifact_markers,
)
from .board import DEVICE

__all__ = [
    "Recording",
    "CSV_FORMAT_TAG",
    "JSON_SCHEMA",
    "to_csv",
    "read_csv",
    "to_json",
    "read_json",
    "to_edf",
    "read_edf",
    "export",
    "load",
    "SUPPORTED_FORMATS",
]

#: First line written to CSV exports; used to detect our own format.
CSV_FORMAT_TAG = "# broadmind-eeg csv v1"

#: Schema identifier written to JSON exports.
JSON_SCHEMA = "broadmind-eeg/json-v1"

#: Formats understood by :func:`export` / :func:`load`.
SUPPORTED_FORMATS: tuple[str, ...] = ("csv", "edf", "json")


class ExportError(ValueError):
    """Raised when a recording cannot be written in the requested format."""


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


@dataclass
class Recording:
    """An in-memory EEG recording.

    Attributes
    ----------
    data:
        ``(n_channels, n_samples)`` array in microvolts, device channel order.
    sampling_rate:
        Sampling rate in Hz (the device runs at 250 Hz).
    channel_names:
        Channel labels in the same order as ``data`` rows.
    timestamps_s:
        Optional ``(n_samples,)`` absolute UNIX timestamps in seconds.
    started_at:
        Optional timezone-aware start time.
    metadata:
        Free-form JSON-serialisable dict (transport, device, notes...).
    artifacts:
        Artefact labels attached to this recording. Labels never replace data.
    """

    data: np.ndarray
    sampling_rate: float
    channel_names: Sequence[str]
    timestamps_s: np.ndarray | None = None
    started_at: datetime | None = None
    metadata: dict = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)

    def __post_init__(self) -> None:
        arr = np.asarray(self.data, dtype=np.float64)
        if arr.ndim != 2:
            raise ValueError(
                f"data must be 2-D (channels, samples), got shape {arr.shape}"
            )
        if not np.all(np.isfinite(arr)):
            raise ValueError("data contains NaN or infinite values")
        fs = float(self.sampling_rate)
        if not np.isfinite(fs) or fs <= 0:
            raise ValueError(f"sampling_rate must be positive, got {fs}")
        names = tuple(str(n) for n in self.channel_names)
        if len(names) != arr.shape[0]:
            raise ValueError(
                f"{len(names)} channel names for {arr.shape[0]} data rows"
            )
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate channel names: {names}")
        if set(names) == set(montage.CHANNEL_NAMES) and names != montage.CHANNEL_NAMES:
            raise montage.MontageError(
                "channel names are correct but ordered differently from the "
                f"device montage {montage.CHANNEL_NAMES}; use broadmind.montage.reorder()"
            )
        self.channel_names = names
        self.data = arr
        self.sampling_rate = fs
        if self.timestamps_s is not None:
            ts = np.asarray(self.timestamps_s, dtype=np.float64)
            if ts.ndim != 1 or ts.shape[0] != arr.shape[1]:
                raise ValueError(
                    f"timestamps_s must have {arr.shape[1]} values, got {ts.shape}"
                )
            self.timestamps_s = ts
        self.artifacts = list(self.artifacts)

    # -- derived values -----------------------------------------------------

    @property
    def n_channels(self) -> int:
        return int(self.data.shape[0])

    @property
    def n_samples(self) -> int:
        return int(self.data.shape[1])

    @property
    def duration_s(self) -> float:
        return self.n_samples / self.sampling_rate

    def time_axis(self) -> np.ndarray:
        """Return per-sample times in seconds from the start of the recording."""
        return np.arange(self.n_samples, dtype=np.float64) / self.sampling_rate

    def marker_column(self) -> np.ndarray:
        """Return a per-sample marker column built from the artefact labels.

        A marker code is written at the sample where the event starts and 0
        elsewhere; no samples are modified.
        """
        column = np.zeros(self.n_samples, dtype=np.int16)
        for index, code in artifact_markers(self.artifacts, self.sampling_rate):
            if 0 <= index < self.n_samples:
                column[index] = code
        return column

    def validate(self) -> "Recording":
        """Re-run the constructor invariants on a mutated instance."""
        Recording.__post_init__(self)
        return self

    def to_dict(self, include_data: bool = False) -> dict:
        """Return a JSON-serialisable summary (optionally with the samples)."""
        payload = {
            "schema": JSON_SCHEMA,
            "sampling_rate": self.sampling_rate,
            "channel_names": list(self.channel_names),
            "n_samples": self.n_samples,
            "duration_s": self.duration_s,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "reference": DEVICE.reference,
            "ground": DEVICE.ground,
            "metadata": dict(self.metadata),
            "artifacts": [a.to_dict() for a in self.artifacts],
        }
        if include_data:
            payload["data"] = self.data.tolist()
        return payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _path(path: str | Path) -> Path:
    p = Path(path).expanduser()
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _format_of(path: str | Path) -> str:
    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix == "edf":
        return "edf"
    if suffix == "csv":
        return "csv"
    if suffix == "json":
        return "json"
    raise ExportError(
        f"unsupported output format {suffix!r} for {path}; "
        f"use one of: {', '.join(SUPPORTED_FORMATS)}"
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _meta_payload(recording: Recording) -> dict:
    """Metadata JSON stored in the CSV header (and reused in JSON exports)."""
    return {
        "sampling_rate": recording.sampling_rate,
        "channel_names": list(recording.channel_names),
        "n_samples": recording.n_samples,
        "duration_s": recording.duration_s,
        "started_at": recording.started_at.isoformat() if recording.started_at else None,
        "reference": DEVICE.reference,
        "ground": DEVICE.ground,
        "metadata": dict(recording.metadata),
        "artifacts": [a.to_dict() for a in recording.artifacts],
    }


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def to_csv(recording: Recording, path: str | Path) -> Path:
    """Write ``recording`` as CSV (``time_s``, channels, ``marker``)."""
    out = _path(path)
    meta = _meta_payload(recording)
    channels = list(recording.channel_names)
    markers = recording.marker_column()
    time_axis = recording.time_axis()

    with out.open("w", newline="", encoding="utf-8") as handle:
        handle.write(CSV_FORMAT_TAG + "\n")
        handle.write("# meta: " + json.dumps(meta, separators=(",", ":")) + "\n")
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["time_s", *channels, "marker"])
        for i in range(recording.n_samples):
            writer.writerow(
                [
                    f"{time_axis[i]:.9f}",
                    *(f"{recording.data[ch, i]:.9f}" for ch in range(recording.n_channels)),
                    int(markers[i]),
                ]
            )
    return out


def read_csv(path: str | Path) -> Recording:
    """Read a CSV written by :func:`to_csv` (or one with the same columns).

    The ``# meta:`` line is required to recover channel names precisely; a
    plain CSV with a ``time_s`` column and channel columns still loads, with
    names taken from the header.
    """
    src = Path(path).expanduser()
    meta: dict = {}
    header: list[str] | None = None
    rows: list[list[str]] = []
    with src.open("r", encoding="utf-8", newline="") as handle:
        for line in handle:
            if line.startswith("# meta:"):
                meta = json.loads(line[len("# meta:"):].strip())
                continue
            if line.startswith("#") or not line.strip():
                continue
            if header is None:
                header = next(csv.reader([line]))
                continue
            rows.append(next(csv.reader([line])))
    if header is None:
        raise ExportError(f"{src} contains no CSV header row")

    header = [h.strip() for h in header]
    if header and header[0].lower() in {"time_s", "time", "timestamp"}:
        value_columns = header[1:]
    else:
        value_columns = header
    has_marker = bool(value_columns) and value_columns[-1].lower() == "marker"
    channels = value_columns[:-1] if has_marker else value_columns

    table = np.asarray(rows, dtype=np.float64)
    if table.size == 0:
        data = np.zeros((len(channels), 0), dtype=np.float64)
        markers = np.zeros((0,), dtype=np.int16)
    else:
        data = table[:, 1:1 + len(channels)].T.copy()
        markers = (
            table[:, 1 + len(channels)].astype(np.int16)
            if has_marker
            else np.zeros(table.shape[0], dtype=np.int16)
        )

    fs = float(meta.get("sampling_rate", 0.0))
    if fs <= 0:
        # Fall back to the time column spacing when metadata is absent.
        if table.shape[0] > 1 and table[0, 0] > table[-1, 0]:
            fs = 250.0
        elif table.shape[0] > 1:
            fs = 1.0 / float(np.median(np.diff(table[:, 0])))
        else:
            fs = 250.0

    artifacts = _artifacts_from_meta(meta)
    if not artifacts:
        artifacts = _artifacts_from_markers(markers, fs)

    return Recording(
        data=data,
        sampling_rate=fs,
        channel_names=channels,
        started_at=_parse_iso(meta.get("started_at")),
        metadata=dict(meta.get("metadata") or {}),
        artifacts=artifacts,
    )


def _artifacts_from_meta(meta: dict) -> list[Artifact]:
    out: list[Artifact] = []
    for item in meta.get("artifacts", []) or []:
        try:
            out.append(
                Artifact(
                    label=str(item["label"]),
                    onset_s=float(item.get("onset_s", 0.0)),
                    duration_s=float(item.get("duration_s", 0.0)),
                    peak_uv=float(item.get("peak_uv", 0.0)),
                    channels=tuple(item.get("channels", ())),
                    threshold_uv=float(item.get("threshold_uv", 0.0)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _artifacts_from_markers(markers: np.ndarray, fs: float) -> list[Artifact]:
    """Reconstruct label-only artefacts from a marker column."""
    out: list[Artifact] = []
    for index in np.flatnonzero(markers):
        label = MARKER_CODE_LABELS.get(int(markers[index]))
        if label is None:
            continue
        out.append(
            Artifact(
                label=label,
                onset_s=float(index) / fs,
                duration_s=0.0,
                peak_uv=0.0,
            )
        )
    return out


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def to_json(recording: Recording, path: str | Path) -> Path:
    """Write ``recording`` as a JSON document (channel-major sample lists)."""
    out = _path(path)
    payload = _meta_payload(recording)
    payload.update(
        {
            "schema": JSON_SCHEMA,
            "data_shape": [recording.n_channels, recording.n_samples],
            "timestamps_s": (
                recording.timestamps_s.tolist()
                if recording.timestamps_s is not None
                else None
            ),
            "data": recording.data.tolist(),
        }
    )
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return out


def read_json(path: str | Path) -> Recording:
    """Read a JSON document written by :func:`to_json`."""
    src = Path(path).expanduser()
    payload = json.loads(src.read_text(encoding="utf-8"))
    if "data" not in payload:
        raise ExportError(f"{src} is not a broadmind JSON recording (no 'data')")
    data = np.asarray(payload["data"], dtype=np.float64)
    channels = payload.get("channel_names") or payload.get("channels") or []
    if data.ndim != 2:
        raise ExportError(f"{src}: 'data' must be 2-D, got {data.shape}")
    ts = payload.get("timestamps_s")
    return Recording(
        data=data,
        sampling_rate=float(payload["sampling_rate"]),
        channel_names=channels,
        timestamps_s=np.asarray(ts, dtype=np.float64) if ts is not None else None,
        started_at=_parse_iso(payload.get("started_at")),
        metadata=dict(payload.get("metadata") or {}),
        artifacts=_artifacts_from_meta(payload),
    )


# ---------------------------------------------------------------------------
# EDF
# ---------------------------------------------------------------------------


def to_edf(
    recording: Recording,
    path: str | Path,
    pad_to_whole_seconds: bool = True,
    data_record_duration: float = 1.0,
) -> dict:
    """Write ``recording`` as EDF+ with artefacts as annotations.

    Parameters
    ----------
    pad_to_whole_seconds:
        EDF stores data in fixed records. With the default one-second record a
        length that is not a whole number of seconds is zero-padded at the end;
        set this to False to get an explicit error instead.
    data_record_duration:
        Length of one EDF data record in seconds (default 1.0).

    Returns
    -------
    dict
        Info about what was written: path, duration, samples, padded samples,
        annotation count and the EDF physical ranges used.

    Notes
    -----
    EDF quantises to integers over the per-channel physical range. The range is
    derived from the data with a 1 % margin, so quantisation error is small
    (typically 0.001-0.01 uV) but not zero; declared ``uV`` values are
    therefore close-but-rounded, unlike CSV/JSON which round trip exactly.
    """
    out = _path(path)
    fs = recording.sampling_rate
    if abs(fs - round(fs)) > 1e-9:
        raise ExportError(
            f"EDF needs an integer sampling rate, got {fs}; resample before export"
        )
    fs_int = int(round(fs))
    samples_per_record = int(round(data_record_duration * fs_int))
    if samples_per_record < 1:
        raise ExportError(
            f"data_record_duration={data_record_duration}s gives < 1 sample per record"
        )

    data = np.array(recording.data, dtype=np.float64, copy=True)
    remainder = data.shape[1] % samples_per_record
    padded = 0
    if remainder:
        if not pad_to_whole_seconds:
            raise ExportError(
                f"{data.shape[1]} samples is not a multiple of {samples_per_record} "
                f"(one EDF data record); pass pad_to_whole_seconds=True or trim"
            )
        padded = samples_per_record - remainder
        data = np.concatenate(
            [data, np.zeros((data.shape[0], padded), dtype=np.float64)], axis=1
        )

    signals = []
    ranges: list[list[float]] = []
    for index, name in enumerate(recording.channel_names):
        row = data[index, :]
        lo = float(np.min(row))
        hi = float(np.max(row))
        if hi - lo < 1e-9:  # constant signal: give the codec a tiny range
            lo, hi = lo - 0.5, hi + 0.5
        margin = max(1e-6, 0.01 * (hi - lo))
        physical_range = (lo - margin, hi + margin)
        ranges.append([physical_range[0], physical_range[1]])
        signals.append(
            edfio.EdfSignal(
                row,
                sampling_frequency=float(fs_int),
                label=str(name),
                physical_dimension="uV",
                physical_range=physical_range,
                prefiltering=f"HP:{DEVICE.bandwidth_hz[0]}Hz LP:{DEVICE.bandwidth_hz[1]}Hz",
                transducer_type="Dry active electrode",
            )
        )

    annotations = [
        edfio.EdfAnnotation(
            onset=float(a.onset_s),
            duration=float(a.duration_s) if a.duration_s > 0 else None,
            text=str(a.label),
        )
        for a in recording.artifacts
    ]

    started = recording.started_at
    startdate = started.date() if started is not None else date.today()
    # EDF+ stores the start time in whole seconds. Truncating microseconds keeps
    # the file plain "EDF+" instead of "EDF+C", which is what EEGLAB/MNE expect.
    starttime = started.time().replace(microsecond=0) if started is not None else None
    subject = str(recording.metadata.get("subject_id") or "X")
    edf = edfio.Edf(
        signals,
        patient=edfio.Patient(code=subject) if subject != "X" else None,
        recording=edfio.Recording(
            startdate=startdate,
            equipment_code="BROADMIND-EEG",
        ),
        starttime=starttime,
        annotations=annotations or None,
        data_record_duration=float(data_record_duration),
    )
    edf.write(str(out))
    return {
        "path": str(out),
        "format": "edf",
        "sampling_rate": float(fs_int),
        "n_samples": int(data.shape[1]),
        "samples_padded": int(padded),
        "duration_s": data.shape[1] / float(fs_int),
        "annotations": len(annotations),
        "physical_ranges_uv": ranges,
    }


def read_edf(path: str | Path) -> Recording:
    """Read an EDF/EDF+ file written by :func:`to_edf`.

    Reads every non-annotation signal and turns annotations whose text matches
    an artefact label into :class:`Artifact` records. EDF+ annotations carry
    label, onset and duration only, so ``peak_uv`` and ``channels`` are not
    recoverable from the file (they are ``0.0`` and empty).
    """
    src = Path(path).expanduser()
    edf = edfio.read_edf(str(src))
    signals = list(edf.signals)
    if not signals:
        raise ExportError(f"{src} contains no EDF signals")
    names = tuple(str(signal.label) for signal in signals)
    data = np.stack(
        [np.asarray(signal.data, dtype=np.float64) for signal in signals], axis=0
    )
    fs = float(signals[0].sampling_frequency)

    artifacts: list[Artifact] = []
    for annotation in edf.annotations:
        text = str(annotation.text or "").strip().lower()
        if text not in ARTIFACT_LABELS:
            continue
        artifacts.append(
            Artifact(
                label=text,
                onset_s=float(annotation.onset),
                duration_s=float(annotation.duration or 0.0),
                peak_uv=0.0,
            )
        )

    started_at = None
    try:
        started_at = edf.startdatetime
    except Exception:  # pragma: no cover - malformed header
        started_at = None

    metadata = {
        "source_format": "edf",
        "edf_equipment": str(getattr(edf.recording, "equipment_code", "") or ""),
        "edf_annotations": len(edf.annotations),
    }
    return Recording(
        data=data,
        sampling_rate=fs,
        channel_names=names,
        started_at=started_at,
        metadata=metadata,
        artifacts=artifacts,
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def export(
    recording: Recording,
    path: str | Path,
    fmt: str | None = None,
    **kwargs,
) -> Path:
    """Write ``recording`` to ``path``; format from ``fmt`` or the extension.

    Extra keyword arguments are forwarded to the chosen writer, e.g.
    ``pad_to_whole_seconds=False`` for EDF.
    """
    resolved = (fmt or _format_of(path)).lower().lstrip(".")
    if resolved == "csv":
        return to_csv(recording, path)
    if resolved == "json":
        return to_json(recording, path)
    if resolved == "edf":
        info = to_edf(recording, path, **kwargs)
        return Path(info["path"])
    raise ExportError(
        f"unsupported format {fmt!r}; use one of: {', '.join(SUPPORTED_FORMATS)}"
    )


def load(path: str | Path) -> Recording:
    """Load a recording, dispatching on the file extension."""
    fmt = _format_of(path)
    if fmt == "csv":
        return read_csv(path)
    if fmt == "json":
        return read_json(path)
    if fmt == "edf":
        return read_edf(path)
    raise ExportError(f"cannot load {path!r}")  # pragma: no cover - guarded above

