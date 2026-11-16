"""Example 01 - stream data from the headset.

Runs against BrainFlow's synthetic board by default, so it needs no hardware:

    python examples/01_stream.py

To try the OpenBCI-compatible serial path (UNVERIFIED against BroadMind
hardware), see ``--help``:

    python examples/01_stream.py --transport serial --serial-port /dev/cu.usbserial-XXXX

What this shows:
  * opening a session and reading the device configuration
  * the callback API (background polling thread delivering small blocks)
  * the blocking ``collect`` API for a fixed window

What it does not do: filter, interpret or upload anything.
"""
from __future__ import annotations

import argparse
import time

# Allow running this file straight from a checkout without installing the
# package first (``pip install .`` makes this block unnecessary).
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from broadmind import open_stream


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", default="synthetic", choices=["synthetic", "serial"])
    parser.add_argument("--serial-port", default=None)
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args()

    with open_stream(transport=args.transport, serial_port=args.serial_port) as stream:
        print(stream.describe())
        print()

        # --- callback API -------------------------------------------------
        seen: list[int] = []

        def on_block(block) -> None:
            seen.append(block.n_samples)
            print(f"  callback: {block.n_samples:3d} samples, t0={block.timestamps[0]:.3f}")

        print(f"subscribing for {args.seconds:g} s ...")
        stream.subscribe(on_block)
        time.sleep(args.seconds)
        stream.unsubscribe()
        print(
            f"received {sum(seen)} samples in {len(seen)} blocks, "
            f"callback errors: {len(stream.callback_errors)}"
        )

        # --- blocking collect --------------------------------------------
        print("\ncollecting 1.0 s ...")
        block = stream.collect(seconds=1.0)
        print(f"shape={block.data.shape} (channels, samples) duration={block.duration_s:.3f}s")
        print("first sample per channel (uV, synthetic data):")
        for name, value in zip(stream.channel_names, block.data[:, 0]):
            print(f"  {name:<4} {value:9.2f}")

        if stream.config.transport.value != "synthetic":
            print(
                "\nwarning: serial-path compatibility with BroadMind hardware is "
                "UNVERIFIED; confirm with a hardware-in-the-loop test."
            )
    print("\ndone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
