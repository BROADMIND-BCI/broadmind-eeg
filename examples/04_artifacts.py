"""Example 04 - artefact labels that never alter the signal.

Builds a *synthetic demo recording* (not a person) containing one blink-like
excursion and one jaw-clench-like EMG burst, runs the detectors and prints the
labels. Also demonstrates the marker codes used in the stream and in exports.

The important property: detection is read-only. The samples are bit-identical
before and after, and the artefacts are only ever *marked*.

    python examples/04_artifacts.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

# Allow running this file straight from a checkout without installing the
# package first (``pip install .`` makes this block unnecessary).
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from broadmind import CHANNEL_NAMES, Recording
from broadmind.artifacts import (
    ARTIFACT_MARKER_CODES,
    describe,
    label_artifacts,
    summarize,
)
from broadmind.export import to_csv, to_edf

FS = 250.0
SECONDS = 6.0


def blink(n: int, centre_s: float, width_s: float, amplitude_uv: float) -> np.ndarray:
    """A Hann-shaped positive excursion, the shape a blink makes on EEG."""
    y = np.zeros(n)
    i0 = int(round((centre_s - width_s / 2) * FS))
    i1 = int(round((centre_s + width_s / 2) * FS))
    y[i0:i1] = amplitude_uv * np.hanning(i1 - i0)
    return y


def clench(n: int, start_s: float, duration_s: float) -> np.ndarray:
    """A 25-45 Hz EMG-like burst."""
    y = np.zeros(n)
    i0 = int(round(start_s * FS))
    i1 = int(round((start_s + duration_s) * FS))
    t = np.arange(i1 - i0) / FS
    burst = (
        70.0 * np.sin(2 * np.pi * 25.0 * t)
        + 60.0 * np.sin(2 * np.pi * 32.0 * t)
        + 50.0 * np.sin(2 * np.pi * 40.0 * t)
    )
    y[i0:i1] = burst * np.hanning(i1 - i0)
    return y


def main() -> int:
    n = int(SECONDS * FS)
    rng = np.random.default_rng(2024)
    data = rng.normal(0.0, 8.0, size=(len(CHANNEL_NAMES), n))

    # Inject on FT7/FT8, the most anterior channels in this montage.
    frontal = [CHANNEL_NAMES.index("FT7"), CHANNEL_NAMES.index("FT8")]
    data[frontal, :] += blink(n, centre_s=1.5, width_s=0.35, amplitude_uv=250.0)
    data[frontal, :] += clench(n, start_s=4.0, duration_s=0.5)

    before = data.copy()
    artifacts = label_artifacts(data, FS, channel_names=CHANNEL_NAMES)

    print(f"synthetic demo: {SECONDS:g} s, {len(CHANNEL_NAMES)} channels at {FS:g} Hz")
    print("labels found:")
    print(describe(artifacts))
    print()
    print("summary:", summarize(artifacts))
    print("marker codes:", ARTIFACT_MARKER_CODES)
    print(
        "signal unchanged by detection:",
        np.array_equal(data, before),
    )
    print()

    # Labels ride along with the samples in exports; the samples do not move.
    recording = Recording(
        data=data,
        sampling_rate=FS,
        channel_names=list(CHANNEL_NAMES),
        metadata={"source": "example-04 synthetic demo"},
        artifacts=artifacts,
    )
    out_dir = Path(tempfile.mkdtemp())
    csv_path = to_csv(recording, out_dir / "demo.csv")
    edf_info = to_edf(recording, out_dir / "demo.edf")

    markers_present = int(np.count_nonzero(recording.marker_column()))
    print(f"CSV  -> {csv_path} (marker column has {markers_present} labelled samples)")
    print(f"EDF  -> {edf_info['path']} ({edf_info['annotations']} EDF+ annotations)")
    print(
        "\nnote: artefacts are marked, not removed. Deleting data silently would "
        "make results unreproducible; filtering is a separate, explicit step "
        "this SDK deliberately does not take for you."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
