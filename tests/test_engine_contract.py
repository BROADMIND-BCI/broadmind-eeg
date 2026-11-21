"""Conformance tests against the frozen ENGINE-CONTRACT v1 vectors.

The vectors in ``tests/vectors/engine_v1.json`` are generated once from the reference
implementation and then frozen. These tests exist so a change in behaviour is a deliberate,
reviewable edit to the vectors — not a silent drift that reaches the Android and iOS ports.

See ``ENGINE-CONTRACT.md`` (in the GUI repository, ``docs/ENGINE-CONTRACT.md``).
"""
from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest

from broadmind import export, features, protocol, stream
from broadmind.board import adc_counts_to_uv

VECTORS = pathlib.Path(__file__).parent / "vectors" / "engine_v1.json"


@pytest.fixture(scope="module")
def vectors() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


# --- §2 framing -----------------------------------------------------------------

def test_vectors_declare_the_contract(vectors: dict) -> None:
    assert vectors["contract"] == "ENGINE-CONTRACT v1"
    assert vectors["frozen"] is True


def test_decode_matches_vectors(vectors: dict) -> None:
    for case in vectors["decode"]:
        sample = protocol.decode_packet(bytes.fromhex(case["packet"]))
        assert sample.sample_number == case["sample_number"]
        assert list(sample.channels) == case["counts"]
        assert len(sample.aux) == protocol.AUX_BYTES
        assert sample.raw.hex() == case["packet"]


def test_packet_length_is_33_bytes() -> None:
    assert protocol.PACKET_LENGTH == 33
    assert (protocol.START_BYTE, protocol.STOP_BYTE) == (0xA0, 0xC0)
    assert protocol.CHANNELS_PER_PACKET == 8


@pytest.mark.parametrize(
    "bad",
    [
        bytes(32),                                    # too short
        bytes([0xA0]) + bytes(32),                    # bad stop byte
        bytes([0x00]) + bytes(32),                    # bad start byte
    ],
)
def test_malformed_packets_are_rejected(bad: bytes) -> None:
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_packet(bad)


# --- §2.2 scaling ---------------------------------------------------------------

def test_scaling_matches_vectors(vectors: dict) -> None:
    case = vectors["scaling"]
    for gain_key, gain in (("gain_5", 5), ("gain_24", 24)):
        got = adc_counts_to_uv(case["counts"], gain=gain)
        np.testing.assert_allclose(got, case[gain_key], rtol=0, atol=1e-9)


def test_scaling_is_linear_in_gain(vectors: dict) -> None:
    case = vectors["scaling"]
    five = np.array(case["gain_5"])
    twenty_four = np.array(case["gain_24"])
    # gain 24 means 5/24 of the µV per count of gain 5
    np.testing.assert_allclose(twenty_four * 24, five * 5, rtol=0, atol=1e-6)


# --- §3 ring buffer -------------------------------------------------------------

def test_ringbuffer_matches_vectors(vectors: dict) -> None:
    case = vectors["ringbuffer"]
    rb = stream.RingBuffer(n_channels=case["n_channels"], capacity=case["capacity"])
    for block in case["blocks"]:
        rb.append(np.array(block))
    np.testing.assert_allclose(rb.all(), case["expected_all"])
    np.testing.assert_allclose(rb.newest(2), case["expected_newest_2"])
    np.testing.assert_allclose(rb.newest(99), case["expected_newest_99"])
    assert rb.total_appended == case["expected_total_appended"]


def test_ringbuffer_discards_oldest_not_newest(vectors: dict) -> None:
    case = vectors["ringbuffer"]
    rb = stream.RingBuffer(n_channels=case["n_channels"], capacity=case["capacity"])
    for block in case["blocks"]:
        rb.append(np.array(block))
    newest = rb.newest(1).ravel().tolist()
    assert newest == [case["blocks"][-1][0][-1], case["blocks"][-1][1][-1]]


# --- §4.3 band power ------------------------------------------------------------

def test_band_power_matches_vectors(vectors: dict) -> None:
    case = vectors["bands"]
    fs = case["fs"]
    t = np.arange(int(fs * case["seconds"])) / fs
    tone = (case["amplitude_uv"] * np.sin(2 * np.pi * case["tone_hz"] * t))[None, :]
    got = features.band_power(tone, fs)
    for band, expected in case["expected"].items():
        assert got[band][0] == pytest.approx(expected, rel=1e-6, abs=1e-6)


def test_a_pure_tone_carries_a_squared_over_two(vectors: dict) -> None:
    """The physics check that makes the band-power vector meaningful rather than circular."""
    case = vectors["bands"]
    assert case["expected"]["alpha"] == pytest.approx(case["alpha_analytic_uv2"], rel=1e-6)
    # every other band must be empty for a clean 10 Hz tone
    for band in ("delta", "theta", "beta", "gamma"):
        assert case["expected"][band] == pytest.approx(0.0, abs=1e-6)


def test_band_edges_are_half_open(vectors: dict) -> None:
    assert features.BANDS_HZ["delta"] == (0.5, 4.0)
    assert features.BANDS_HZ["theta"] == (4.0, 8.0)
    assert features.BANDS_HZ["alpha"] == (8.0, 13.0)
    assert features.BANDS_HZ["beta"] == (13.0, 30.0)
    assert features.BANDS_HZ["gamma"] == (30.0, 45.0)


# --- §6.1 CSV dialect ------------------------------------------------------------

def test_csv_dialect_matches_vectors(vectors: dict, tmp_path: pathlib.Path) -> None:
    case = vectors["csv"]
    recording = export.Recording(
        data=np.array([[1.5, -2.5, 3.5], [0.0, 0.0, 0.0]]),
        sampling_rate=250.0,
        channel_names=["CP1", "CP2"],
        metadata={"transport": "synthetic", "device": "vector"},
    )
    path = export.to_csv(recording, tmp_path / "v.csv")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == case["tag_line"]
    assert lines[2] == case["header_line"]
    assert lines[3] == case["first_row"]
    assert len(lines) - 3 == case["row_count"]


def test_csv_round_trips(vectors: dict, tmp_path: pathlib.Path) -> None:
    data = np.array([[1.5, -2.5, 3.5], [0.25, 0.5, 0.75]])
    recording = export.Recording(
        data=data, sampling_rate=250.0, channel_names=["CP1", "CP2"]
    )
    path = export.to_csv(recording, tmp_path / "rt.csv")
    back = export.read_csv(path)
    np.testing.assert_allclose(back.data, data, atol=1e-6)
    assert back.sampling_rate == 250.0
