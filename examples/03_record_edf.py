"""Example 03 - record a session and export it as EDF, CSV and JSON.

Records 3 s from the synthetic board, runs artefact labelling (labels only),
writes all three formats and reads each one back to prove it round trips.

    python examples/03_record_edf.py                 # writes to a temp directory
    python examples/03_record_edf.py --out session.edf

EDF output follows the EDF+ standard so it opens in EEGLAB / MNE; artefacts are
written as EDF+ annotations, never by deleting or editing data.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

# Allow running this file straight from a checkout without installing the
# package first (``pip install .`` makes this block unnecessary).
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from broadmind import load, open_stream
from broadmind.artifacts import describe, summarize
from broadmind.export import to_csv, to_edf, to_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="EDF path (default: a temp dir)")
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args()

    with open_stream() as stream:
        # detect_artifacts=True labels blinks/clenches; samples are untouched.
        recording = stream.record(args.seconds, detect_artifacts=True)

    print(
        f"recorded {recording.duration_s:.3f} s "
        f"({recording.n_samples} samples x {recording.n_channels} channels)"
    )
    print(f"transport: {recording.metadata['transport']} [{recording.metadata['transport_status']}]")
    print(f"artefact labels: {summarize(recording.artifacts)}")
    print(describe(recording.artifacts))
    print()

    out_dir = Path(args.out).parent if args.out else Path(tempfile.mkdtemp())
    out_dir.mkdir(parents=True, exist_ok=True)
    base = Path(args.out).stem if args.out else "broadmind_session"

    edf_info = to_edf(recording, out_dir / f"{base}.edf")
    csv_path = to_csv(recording, out_dir / f"{base}.csv")
    json_path = to_json(recording, out_dir / f"{base}.json")

    print("EDF info:")
    for key in ("path", "n_samples", "samples_padded", "duration_s", "annotations"):
        print(f"  {key:<15}: {edf_info[key]}")
    print(f"  physical ranges: {edf_info['physical_ranges_uv'][0]} (channel 0)")

    print("\nround trip:")
    for path in (Path(edf_info["path"]), csv_path, json_path):
        loaded = load(path)
        kind = path.suffix.lstrip(".").upper()
        print(
            f"  {kind:<4} {loaded.n_samples:>5} samples, "
            f"{loaded.n_samples / loaded.sampling_rate:>5.2f} s, "
            f"{loaded.n_channels} channels, "
            f"{path.stat().st_size:>7} bytes, "
            f"labels={[a.label for a in loaded.artifacts]}"
        )
    print(
        "\nnote: EDF is 16-bit quantised per channel; CSV/JSON carry the "
        "float64 values. None of the writers modify or drop samples."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
