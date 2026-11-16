"""Band-power estimates for BROADMIND EEG data, using NumPy only.

What this module does
---------------------
* Computes a one-sided power spectral density with a Hann-windowed
  periodogram (``power_spectrum``).
* Integrates that PSD into the five conventional bands
  (delta / theta / alpha / beta / gamma), absolutely or relative to the total
  band-limited power (``band_power``, ``relative_band_power``).
* Reports the dominant band per channel (``dominant_band``).

What this module deliberately does NOT do
-----------------------------------------
* No artefact rejection, no referencing, no filtering, no ICA. Artefact
  *labels* come from :mod:`broadmind.artifacts`; this module measures whatever
  it is handed, warts and all.
* No validated scientific pipeline and no clinical or cognitive claims. The
  default bands and the periodogram estimator are conventions, not
  biomarkers. Quantitative EEG needs task design, referencing decisions,
  artefact handling and validation that are out of scope for an SDK.
* No SciPy/Welch multitaper machinery: the mathematical core is deliberately
  NumPy-only so the numbers are reproducible and auditable.

Units
-----
Input is assumed to be microvolts. ``power_spectrum`` returns uV^2/Hz and
``band_power`` returns uV^2. A pure sine of amplitude ``A`` uV integrates to
``A**2 / 2`` uV^2 across the band that contains it.
"""
from __future__ import annotations

from typing import Mapping

import numpy as np

__all__ = [
    "BANDS_HZ",
    "power_spectrum",
    "band_power",
    "relative_band_power",
    "dominant_band",
    "integrate_band",
]

#: Conventional EEG bands in Hz (inclusive low, exclusive high).
#:
#: ``gamma`` stops at 45 Hz: the device passes up to 125 Hz, but above ~45 Hz
#: mains harmonics, EMG and cable noise dominate, so the band is deliberately
#: conservative. Override with the ``bands`` argument to change it.
BANDS_HZ: dict[str, tuple[float, float]] = {
    "delta": (0.5, 4.0),
    "theta": (4.0, 8.0),
    "alpha": (8.0, 13.0),
    "beta": (13.0, 30.0),
    "gamma": (30.0, 45.0),
}


def _as_2d(data: np.ndarray) -> np.ndarray:
    """Return ``data`` as float64 (channels, samples), accepting 1-D input."""
    arr = np.asarray(data, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[np.newaxis, :]
    if arr.ndim != 2:
        raise ValueError(f"data must be 1-D or 2-D, got shape {arr.shape}")
    if arr.shape[1] < 8:
        raise ValueError(
            f"need at least 8 samples for a spectral estimate, got {arr.shape[1]}"
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError("data contains NaN or infinite values")
    return arr


def _check_fs(fs: float) -> float:
    fs = float(fs)
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError(f"sampling rate must be positive, got {fs}")
    return fs


def power_spectrum(
    data: np.ndarray,
    fs: float,
    detrend: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """One-sided periodogram with a Hann window.

    Parameters
    ----------
    data:
        ``(channels, samples)`` or ``(samples,)`` array in microvolts.
    fs:
        Sampling rate in Hz.
    detrend:
        Remove the per-channel mean before windowing (default True).

    Returns
    -------
    freqs, psd
        ``freqs`` has shape ``(n_bins,)``, ``psd`` has shape
        ``(channels, n_bins)`` in uV^2/Hz.

    Notes
    -----
    Resolution is ``fs / n_samples``; a 4 s window gives 0.25 Hz bins. The
    estimator is a plain periodogram, so variance is high - average several
    windows (or accept noisy numbers) if you need stable estimates.
    """
    arr = _as_2d(data)
    fs = _check_fs(fs)
    n = arr.shape[1]
    if detrend:
        arr = arr - arr.mean(axis=1, keepdims=True)

    window = np.hanning(n)
    window_power = float(np.sum(window**2))
    if window_power == 0:  # pragma: no cover - n >= 8 guarantees this
        raise ValueError("zero-power window; data too short")

    spec = np.fft.rfft(arr * window, axis=1)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)

    # One-sided PSD: doubling of positive frequencies (except DC and Nyquist,
    # when present) makes the integral equal the signal variance.
    psd = (2.0 / (fs * window_power)) * np.abs(spec) ** 2
    psd[:, 0] *= 0.5
    if n % 2 == 0:
        psd[:, -1] *= 0.5
    return freqs, psd


def integrate_band(freqs: np.ndarray, psd: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Integrate a PSD over ``[lo, hi)`` Hz and return one value per channel."""
    if hi <= lo:
        raise ValueError(f"invalid band ({lo}, {hi}): high edge must exceed low edge")
    if freqs.size < 2:
        return np.zeros(psd.shape[0], dtype=np.float64)
    df = float(freqs[1] - freqs[0])
    mask = (freqs >= lo) & (freqs < hi)
    if not mask.any():
        # Band narrower than one bin (or above Nyquist): no power measurable.
        return np.zeros(psd.shape[0], dtype=np.float64)
    return psd[:, mask].sum(axis=1) * df


def band_power(
    data: np.ndarray,
    fs: float,
    bands: Mapping[str, tuple[float, float]] | None = None,
) -> dict[str, np.ndarray]:
    """Absolute band power per channel in uV^2.

    Parameters
    ----------
    data:
        ``(channels, samples)`` or ``(samples,)`` array in microvolts.
    fs:
        Sampling rate in Hz.
    bands:
        Mapping ``name -> (low_hz, high_hz)``; defaults to :data:`BANDS_HZ`.

    Returns
    -------
    dict
        ``name -> array of shape (channels,)``. A 1-D input yields arrays of
        length 1.
    """
    bands = dict(BANDS_HZ if bands is None else bands)
    freqs, psd = power_spectrum(data, fs)
    return {
        name: integrate_band(freqs, psd, float(lo), float(hi))
        for name, (lo, hi) in bands.items()
    }


def relative_band_power(
    data: np.ndarray,
    fs: float,
    bands: Mapping[str, tuple[float, float]] | None = None,
) -> dict[str, np.ndarray]:
    """Band power as a fraction of total power across the supplied bands.

    The denominator is the sum of the bands passed in, *not* the full 0-125 Hz
    spectrum, so the fractions sum to 1 (or to 0 where total power is 0).
    """
    absolute = band_power(data, fs, bands=bands)
    total = np.zeros_like(next(iter(absolute.values())))
    for values in absolute.values():
        total = total + values
    out: dict[str, np.ndarray] = {}
    for name, values in absolute.items():
        out[name] = np.divide(
            values,
            total,
            out=np.zeros_like(values),
            where=total > 0,
        )
    return out


def dominant_band(
    data: np.ndarray,
    fs: float,
    bands: Mapping[str, tuple[float, float]] | None = None,
) -> list[str]:
    """Return the name of the strongest band for each channel."""
    absolute = band_power(data, fs, bands=bands)
    names = list(absolute)
    stacked = np.stack([absolute[name] for name in names], axis=0)  # (bands, ch)
    best = np.argmax(stacked, axis=0)
    return [names[i] for i in best]
