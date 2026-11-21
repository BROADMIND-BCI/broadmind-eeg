#!/usr/bin/env python3
"""Generate the golden vectors for ENGINE-CONTRACT v1.

The output is committed and FROZEN. This script is not run in CI: if the reference implementation
changes behaviour, the vectors must change deliberately, in their own commit, with a version bump.

    python tools/make_engine_vectors.py            # rewrite tests/vectors/engine_v1.json
"""
from __future__ import annotations

import json
import pathlib

import numpy as np

import tempfile

from broadmind import export, features, protocol, stream
from broadmind.board import adc_counts_to_uv

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "vectors" / "engine_v1.json"


def packet(sample_number: int, counts: list[int], aux: bytes = bytes(6)) -> str:
    return protocol.encode_packet(
        sample_number=sample_number, channels=counts, aux=aux
    ).hex()


def build() -> dict:
    # --- framing -----------------------------------------------------------------
    decode_cases = [
        {"packet": packet(0, [0] * 8), "sample_number": 0, "counts": [0] * 8},
        {"packet": packet(255, [8388607, -8388608, 1, -1, 123456, -123456, 7, -7]),
         "sample_number": 255,
         "counts": [8388607, -8388608, 1, -1, 123456, -123456, 7, -7]},
    ]

    # --- scaling -----------------------------------------------------------------
    scaling_counts = [1, -1, 1000, -1000, 8388607, -8388608]
    scaling = {
        "counts": scaling_counts,
        "gain_5": [round(v, 12) for v in adc_counts_to_uv(scaling_counts, gain=5)],
        "gain_24": [round(v, 12) for v in adc_counts_to_uv(scaling_counts, gain=24)],
    }

    # --- ring buffer -------------------------------------------------------------
    rb = stream.RingBuffer(n_channels=2, capacity=4)
    first = np.array([[1.0, 2.0, 3.0], [10.0, 20.0, 30.0]])
    second = np.array([[4.0, 5.0, 6.0], [40.0, 50.0, 60.0]])
    rb.append(first)
    rb.append(second)          # 6 samples into capacity 4 -> oldest 2 discarded
    ring = {
        "n_channels": 2,
        "capacity": 4,
        "blocks": [first.tolist(), second.tolist()],
        "expected_all": rb.all().tolist(),
        "expected_newest_2": rb.newest(2).tolist(),
        "expected_newest_99": rb.newest(99).tolist(),
        "expected_total_appended": rb.total_appended,
    }

    # --- band power --------------------------------------------------------------
    fs = 250.0
    seconds = 10.0
    amplitude_uv = 100.0
    t = np.arange(int(fs * seconds)) / fs
    tone = (amplitude_uv * np.sin(2 * np.pi * 10.0 * t))[None, :]   # alpha band, 10 Hz
    bp = features.band_power(tone, fs)
    bands = {
        "fs": fs,
        "seconds": seconds,
        "tone_hz": 10.0,
        "amplitude_uv": amplitude_uv,
        "expected": {name: round(float(bp[name][0]), 6) for name in sorted(bp)},
        # a pure 10 Hz sine of amplitude A carries A^2/2 in the alpha band
        "alpha_analytic_uv2": amplitude_uv ** 2 / 2.0,
    }

    # --- CSV dialect -------------------------------------------------------------
    rec = export.Recording(
        data=np.array([[1.5, -2.5, 3.5], [0.0, 0.0, 0.0]]),
        sampling_rate=250.0,
        channel_names=["CP1", "CP2"],
        metadata={"transport": "synthetic", "device": "vector"},
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = export.to_csv(rec, pathlib.Path(tmp) / "v.csv")
        lines = path.read_text(encoding="utf-8").splitlines()
    csv_vector = {
        "tag_line": lines[0],
        "header_line": lines[2],
        "first_row": lines[3],
        "row_count": len(lines) - 3,
    }

    return {
        "contract": "ENGINE-CONTRACT v1",
        "generated_by": "tools/make_engine_vectors.py",
        "frozen": True,
        "decode": decode_cases,
        "scaling": scaling,
        "ringbuffer": ring,
        "bands": bands,
        "csv": csv_vector,
    }


def main() -> int:
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
