"""Artefact detection that returns *labels*, never rewritten signals.

What this module does
---------------------
* Detects two artefact classes on raw EEG: **blinks** (large, low-frequency
  excursions) and **jaw clenches** (bursts of high-frequency EMG).
* Returns plain :class:`Artifact` records (label, onset, duration, peak
  amplitude, channels) plus marker codes that can be inserted into the live
  stream or written next to exported data.
* Uses NumPy only: a low-pass / band-pass envelope, a robust (median/MAD)
  threshold and a duration gate.

What this module deliberately does NOT do
-----------------------------------------
* It never mutates, filters, interpolates or deletes the signal. Detection is
  a pure function; the samples you pass in come out untouched (asserted in the
  test suite). Artefacts are marked, not removed, exactly because deleting
  data silently is bad science.
* It is not a validated artefact-correction pipeline and gives no clinical or
  cognitive interpretation. Thresholds are heuristics with defaults tuned on
  *synthetic* data; on real recordings they must be calibrated per subject,
  kit (Full vs Starter) and session. Nothing here has been validated against
  expert annotation yet - that is roadmap item one.
* It does not detect eye movements other than blinks, swallowing, cardiac,
  muscle other than clench, electrode pops, cable noise, or line-noise
  interference.
* It does not infer causality or make claims about attention, stress, sleep
  or any other state.

Threshold model
---------------
``threshold = max(absolute_uv, median(envelope) + k * 1.4826 * MAD(envelope))``

The absolute floor keeps detector noise from firing on an unusually quiet
recording; the robust term adapts to the recording's own noise level. An
event is reported only if its supra-threshold run lasts between
``min_duration_s`` and ``max_duration_s``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from . import montage

__all__ = [
    "BLINK",
    "CLENCH",
    "ARTIFACT_LABELS",
    "ARTIFACT_MARKER_CODES",
    "MARKER_CODE_LABELS",
    "Artifact",
    "BlinkConfig",
    "ClenchConfig",
    "detect_blinks",
    "detect_clenches",
    "label_artifacts",
    "artifact_markers",
    "summarize",
    "describe",
]

#: Label for blink artefacts.
BLINK = "blink"

#: Label for jaw-clench / bruxism artefacts.
CLENCH = "clench"

#: All labels this SDK emits, in documentation order.
ARTIFACT_LABELS: tuple[str, ...] = (BLINK, CLENCH)

#: Marker code written into the stream / exported files for each label.
#: Codes are deliberately stable: they are part of the on-disk contract.
ARTIFACT_MARKER_CODES: dict[str, int] = {BLINK: 1, CLENCH: 2}

#: Reverse mapping, for reading markers back.
MARKER_CODE_LABELS: dict[int, str] = {v: k for k, v in ARTIFACT_MARKER_CODES.items()}


@dataclass(frozen=True)
class Artifact:
    """One labelled event.

    Attributes
    ----------
    label:
        ``"blink"`` or ``"clench"``.
    onset_s, duration_s:
        Event position and length in seconds from the start of the data.
    peak_uv:
        Peak amplitude of the *filtered* signal that triggered detection, in
        microvolts. Comparable between events with the same label; not a
        calibrated biological amplitude.
    channels:
        Channels implicated in the event, in device channel order.
    threshold_uv:
        Threshold that produced the event (useful when tuning).
    """

    label: str
    onset_s: float
    duration_s: float
    peak_uv: float
    channels: tuple[str, ...] = ()
    threshold_uv: float = 0.0

    @property
    def offset_s(self) -> float:
        return self.onset_s + self.duration_s

    @property
    def marker_code(self) -> int:
        """Stable marker code for this label."""
        return ARTIFACT_MARKER_CODES[self.label]

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "onset_s": round(float(self.onset_s), 6),
            "duration_s": round(float(self.duration_s), 6),
            "peak_uv": round(float(self.peak_uv), 4),
            "channels": list(self.channels),
            "marker_code": self.marker_code,
            "threshold_uv": round(float(self.threshold_uv), 4),
        }


@dataclass(frozen=True)
class BlinkConfig:
    """Heuristics for blink detection.

    Defaults were tuned on synthetic data (see ``tests/test_smoke.py``). On a
    sensorimotor montage blinks are far from the eyes and can be much smaller
    than on a frontal montage, so ``absolute_uv`` may need lowering for real
    recordings - and everything needs expert validation.
    """

    lowpass_hz: float = 5.0
    envelope_s: float = 0.10
    absolute_uv: float = 75.0
    sigma_k: float = 6.0
    min_duration_s: float = 0.10
    max_duration_s: float = 0.80
    merge_gap_s: float = 0.10


@dataclass(frozen=True)
class ClenchConfig:
    """Heuristics for jaw-clench (EMG burst) detection."""

    band_hz: tuple[float, float] = (20.0, 45.0)
    envelope_s: float = 0.08
    absolute_uv: float = 20.0
    sigma_k: float = 6.0
    min_duration_s: float = 0.15
    max_duration_s: float = 3.0
    merge_gap_s: float = 0.10


# ---------------------------------------------------------------------------
# Small NumPy signal helpers (deliberately dependency-free)
# ---------------------------------------------------------------------------


def _as_2d(data: np.ndarray) -> np.ndarray:
    """Return a private float64 copy shaped ``(channels, samples)``."""
    arr = np.array(data, dtype=np.float64, copy=True)
    if arr.ndim == 1:
        arr = arr[np.newaxis, :]
    if arr.ndim != 2:
        raise ValueError(f"data must be 1-D or 2-D, got shape {arr.shape}")
    if arr.shape[1] < 2:
        raise ValueError("need at least 2 samples")
    if not np.all(np.isfinite(arr)):
        raise ValueError("data contains NaN or infinite values")
    return arr


def _check_fs(fs: float) -> float:
    fs = float(fs)
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError(f"sampling rate must be positive, got {fs}")
    return fs


def _robust_sigma(x: np.ndarray) -> float:
    """Median absolute deviation scaled to a Gaussian standard deviation."""
    median = float(np.median(x))
    mad = float(np.median(np.abs(x - median)))
    return 1.4826 * mad


def _convolve_rows(x: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """1-D convolution applied to every row ('same' length, zero padded)."""
    out = np.empty_like(x, dtype=np.float64)
    for i in range(x.shape[0]):
        out[i, :] = np.convolve(x[i, :], kernel, mode="same")
    return out


def _moving_average_rows(x: np.ndarray, window: int) -> np.ndarray:
    """Symmetric moving average per row; ``window`` is forced odd and >= 1."""
    window = max(1, int(round(window)))
    if window % 2 == 0:
        window += 1
    if window <= 1:
        return x.astype(np.float64, copy=True)
    return _convolve_rows(x, np.ones(window, dtype=np.float64) / window)


def _lowpass_rows(x: np.ndarray, fs: float, cutoff_hz: float, passes: int = 2) -> np.ndarray:
    """Zero-phase-ish low-pass from a truncated one-pole impulse response.

    This is a deliberately simple NumPy filter (no SciPy), applied forwards and
    backwards to remove most of the phase delay. It is not a designed
    Butterworth; for detection envelopes that is sufficient, and the exact
    response is documented rather than implied.
    """
    cutoff_hz = float(cutoff_hz)
    if cutoff_hz <= 0:
        raise ValueError(f"cutoff must be positive, got {cutoff_hz}")
    a = math.exp(-2.0 * math.pi * cutoff_hz / fs)
    n = x.shape[1]
    length = min(n - 1, int(math.ceil(5.0 / max(1e-9, 1.0 - a))))
    if length < 2:
        return x.astype(np.float64, copy=True)
    k = np.arange(length + 1, dtype=np.float64)
    kernel = (1.0 - a) * a**k
    kernel /= kernel.sum()
    out = _convolve_rows(x, kernel)
    for _ in range(max(0, passes - 1)):
        out = _convolve_rows(out[:, ::-1], kernel)[:, ::-1]
    return out


def _bandpass_rows(x: np.ndarray, fs: float, lo: float, hi: float) -> np.ndarray:
    """Brick-wall band-pass via an FFT mask (simple, deterministic, NumPy-only)."""
    n = x.shape[1]
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    hi = min(float(hi), fs / 2.0)
    if hi <= float(lo):
        raise ValueError(f"empty band ({lo}, {hi}) at fs={fs}")
    spec = np.fft.rfft(x, axis=1)
    mask = (freqs >= float(lo)) & (freqs <= hi)
    if not mask.any():
        raise ValueError(f"band ({lo}, {hi}) contains no FFT bins at fs={fs}, n={n}")
    spec *= mask
    return np.fft.irfft(spec, n=n, axis=1)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return ``(start, stop)`` index pairs for contiguous True runs."""
    if mask.size == 0:
        return []
    padded = np.concatenate([[False], mask.astype(bool), [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(s), int(e)) for s, e in zip(edges[0::2], edges[1::2])]


def _merge_runs(runs: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    """Merge runs separated by fewer than ``max_gap`` samples."""
    if not runs:
        return []
    merged = [runs[0]]
    for start, stop in runs[1:]:
        last_start, last_stop = merged[-1]
        if start - last_stop <= max_gap:
            merged[-1] = (last_start, max(stop, last_stop))
        else:
            merged.append((start, stop))
    return merged


def _channel_labels(
    channel_names: Sequence[str] | None, n_channels: int
) -> tuple[str, ...]:
    """Resolve channel labels, defaulting to the BROADMIND montage order."""
    if channel_names is not None:
        names = tuple(str(n) for n in channel_names)
        if len(names) != n_channels:
            raise ValueError(
                f"{len(names)} channel names for {n_channels} data rows"
            )
        return names
    if n_channels == len(montage.CHANNEL_NAMES):
        return montage.CHANNEL_NAMES
    return tuple(f"ch{i}" for i in range(n_channels))


def _detect(
    envelope: np.ndarray,
    filtered: np.ndarray,
    fs: float,
    label: str,
    channel_names: tuple[str, ...],
    absolute_uv: float,
    sigma_k: float,
    min_duration_s: float,
    max_duration_s: float,
    merge_gap_s: float,
) -> list[Artifact]:
    """Shared threshold/run-length logic for both artefact classes."""
    combined = envelope.max(axis=0)
    baseline = float(np.median(combined))
    sigma = _robust_sigma(combined)
    threshold = max(float(absolute_uv), baseline + float(sigma_k) * sigma)
    if sigma <= 0.0 and float(absolute_uv) <= 0.0:
        return []

    runs = _runs(combined > threshold)
    runs = _merge_runs(runs, max_gap=int(round(merge_gap_s * fs)))
    lo_n = max(1, int(round(min_duration_s * fs)))
    hi_n = max(lo_n, int(round(max_duration_s * fs)))

    events: list[Artifact] = []
    for start, stop in runs:
        length = stop - start
        if length < lo_n or length > hi_n:
            continue
        segment = filtered[:, start:stop]
        amplitudes = np.max(np.abs(segment), axis=1)
        peak = float(amplitudes.max()) if amplitudes.size else 0.0
        active = tuple(
            channel_names[i]
            for i in range(amplitudes.size)
            if amplitudes[i] >= 0.5 * peak and peak > 0
        )
        if not active:  # pragma: no cover - defensive
            active = (channel_names[int(np.argmax(amplitudes))],)
        events.append(
            Artifact(
                label=label,
                onset_s=start / fs,
                duration_s=length / fs,
                peak_uv=peak,
                channels=active,
                threshold_uv=threshold,
            )
        )
    return events


# ---------------------------------------------------------------------------
# Public detectors
# ---------------------------------------------------------------------------


def detect_blinks(
    data: np.ndarray,
    fs: float,
    channel_names: Sequence[str] | None = None,
    config: BlinkConfig | None = None,
) -> list[Artifact]:
    """Detect blink artefacts as large slow excursions.

    The signal is low-passed (5 Hz by default), rectified and smoothed into an
    envelope; supra-threshold runs lasting 0.1-0.8 s are reported as blinks.

    The input is never modified.
    """
    cfg = config or BlinkConfig()
    arr = _as_2d(data)
    fs = _check_fs(fs)
    names = _channel_labels(channel_names, arr.shape[0])

    low = _lowpass_rows(arr, fs, cfg.lowpass_hz, passes=2)
    low = low - np.median(low, axis=1, keepdims=True)
    envelope = _moving_average_rows(np.abs(low), int(round(cfg.envelope_s * fs)))
    return _detect(
        envelope=envelope,
        filtered=low,
        fs=fs,
        label=BLINK,
        channel_names=names,
        absolute_uv=cfg.absolute_uv,
        sigma_k=cfg.sigma_k,
        min_duration_s=cfg.min_duration_s,
        max_duration_s=cfg.max_duration_s,
        merge_gap_s=cfg.merge_gap_s,
    )


def detect_clenches(
    data: np.ndarray,
    fs: float,
    channel_names: Sequence[str] | None = None,
    config: ClenchConfig | None = None,
) -> list[Artifact]:
    """Detect jaw-clench / bruxism artefacts as high-frequency EMG bursts.

    The signal is band-passed (20-45 Hz by default), rectified and smoothed;
    supra-threshold runs lasting 0.15-3 s are reported as clenches.

    The input is never modified.
    """
    cfg = config or ClenchConfig()
    arr = _as_2d(data)
    fs = _check_fs(fs)
    names = _channel_labels(channel_names, arr.shape[0])

    lo, hi = cfg.band_hz
    band = _bandpass_rows(arr, fs, lo, min(hi, fs / 2.0))
    envelope = _moving_average_rows(np.abs(band), int(round(cfg.envelope_s * fs)))
    return _detect(
        envelope=envelope,
        filtered=band,
        fs=fs,
        label=CLENCH,
        channel_names=names,
        absolute_uv=cfg.absolute_uv,
        sigma_k=cfg.sigma_k,
        min_duration_s=cfg.min_duration_s,
        max_duration_s=cfg.max_duration_s,
        merge_gap_s=cfg.merge_gap_s,
    )


def label_artifacts(
    data: np.ndarray,
    fs: float,
    channel_names: Sequence[str] | None = None,
    labels: Sequence[str] | None = None,
    blink_config: BlinkConfig | None = None,
    clench_config: ClenchConfig | None = None,
) -> list[Artifact]:
    """Run the requested detectors and return all labels, sorted by onset.

    Parameters
    ----------
    labels:
        Subset of ``("blink", "clench")``; ``None`` means both. Unknown labels
        raise ``ValueError`` rather than being silently ignored.
    """
    if labels is None:
        wanted = tuple(ARTIFACT_LABELS)
    else:
        wanted = tuple(str(lab).lower() for lab in labels)
        unknown = [lab for lab in wanted if lab not in ARTIFACT_LABELS]
        if unknown:
            raise ValueError(
                f"unknown artefact label(s) {unknown}; known: {ARTIFACT_LABELS}"
            )
    found: list[Artifact] = []
    if BLINK in wanted:
        found.extend(
            detect_blinks(data, fs, channel_names=channel_names, config=blink_config)
        )
    if CLENCH in wanted:
        found.extend(
            detect_clenches(
                data, fs, channel_names=channel_names, config=clench_config
            )
        )
    return sorted(found, key=lambda a: (a.onset_s, a.label))


def artifact_markers(artifacts: Sequence[Artifact], fs: float) -> list[tuple[int, int]]:
    """Return ``(sample_index, marker_code)`` pairs for offline export.

    The sample index is the sample where the event starts, so a marker column
    in CSV or an EDF+ annotation carries the label without touching the signal.
    """
    fs = _check_fs(fs)
    out: list[tuple[int, int]] = []
    for artifact in artifacts:
        index = int(round(float(artifact.onset_s) * fs))
        out.append((max(0, index), ARTIFACT_MARKER_CODES[artifact.label]))
    return out


def summarize(artifacts: Sequence[Artifact]) -> dict[str, int]:
    """Count artefacts by label (plus a total), for reports and CLI output."""
    counts: dict[str, int] = {label: 0 for label in ARTIFACT_LABELS}
    for artifact in artifacts:
        counts[artifact.label] = counts.get(artifact.label, 0) + 1
    counts["total"] = sum(counts.values())
    return counts


def describe(artifacts: Sequence[Artifact]) -> str:
    """Return a plain-text table of artefacts (empty string if none)."""
    if not artifacts:
        return "no artefacts labelled"
    lines = [f"{'label':<8} {'onset_s':>8} {'dur_s':>7} {'peak_uV':>9}  channels"]
    for a in artifacts:
        lines.append(
            f"{a.label:<8} {a.onset_s:>8.3f} {a.duration_s:>7.3f} "
            f"{a.peak_uv:>9.1f}  {','.join(a.channels)}"
        )
    return "\n".join(lines)
