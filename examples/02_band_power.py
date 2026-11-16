"""Example 02 - band power for every channel.

Records a few seconds from the synthetic board and prints absolute and relative
band power per channel. Synthetic-board samples are artificial noise, so the
numbers are not physiology - they demonstrate the API and the units only.

Part 2 runs the same maths on a 10 Hz sine so you can see the expected result:
alpha (8-13 Hz) must dominate, with alpha power ~ A^2 / 2.

    python examples/02_band_power.py
"""
from __future__ import annotations

import numpy as np

# Allow running this file straight from a checkout without installing the
# package first (``pip install .`` makes this block unnecessary).
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from broadmind import band_power, dominant_band, open_stream, relative_band_power
from broadmind.features import BANDS_HZ

FS = 250.0


def print_table(title: str, absolute: dict, relative: dict, channels) -> None:
    print(title)
    header = f"  {'channel':<8}" + "".join(f"{name:>12}" for name in BANDS_HZ)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for index, channel in enumerate(channels):
        cells = "".join(
            f"{absolute[name][index]:>8.1f}/{relative[name][index]:>3.0%}"
            for name in BANDS_HZ
        )
        print(f"  {channel:<8}{cells}")
    print("  (values are uV^2 / fraction of the total across these bands)\n")


def main() -> int:
    # --- part 1: live synthetic recording --------------------------------
    with open_stream() as stream:
        recording = stream.record(seconds=4.0)
    absolute = band_power(recording.data, recording.sampling_rate)
    relative = relative_band_power(recording.data, recording.sampling_rate)
    print(
        f"recorded {recording.duration_s:.1f} s from the synthetic board "
        f"({recording.n_samples} samples, {recording.n_channels} channels)\n"
    )
    print_table("synthetic board (artificial noise, not a person):", absolute, relative,
                recording.channel_names)
    print("dominant band per channel:", dominant_band(recording.data, recording.sampling_rate))

    # --- part 2: known 10 Hz calibration signal --------------------------
    n = int(4 * FS)
    t = np.arange(n) / FS
    alpha_sine = 10.0 * np.sin(2 * np.pi * 10.0 * t)  # 10 uV amplitude
    absolute = band_power(alpha_sine, FS)
    relative = relative_band_power(alpha_sine, FS)
    print("\n10 Hz sine, 10 uV amplitude (expected alpha ~ 10^2/2 = 50 uV^2):")
    print_table("calibration signal:", absolute, relative, ["sine"])
    print(f"sanity check: alpha = {float(absolute['alpha'][0]):.3f} uV^2 (expected 50)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
