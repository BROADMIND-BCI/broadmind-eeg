"""Smoke tests for the BROADMIND EEG SDK.

These tests are the contract for what this repository can actually do. They run
without hardware: the BrainFlow ``SYNTHETIC_BOARD`` supplies fake samples and
all signal-processing checks use signals generated in the test itself.

Coverage
--------
* montage order/length and the reorder helper
* band power on a 10 Hz sine (alpha must dominate) and Parseval consistency
* blink and jaw-clench detectors on synthetic artefacts, including the
  guarantee that detection never mutates the input
* OpenBCI-compatible 33-byte packet codec and stream resynchronisation
* ring buffer semantics
* CSV / EDF / JSON round trips (including EDF's whole-second padding)
* a live synthetic-board session through BrainFlow (collect + callback)
* the ``broadmind info --json`` CLI contract

Run with:
    cd sdk/broadmind-eeg && python -m pytest tests/test_smoke.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

import broadmind
from broadmind import (
    BANDS_HZ,
    CHANNEL_NAMES,
    GROUND_ELECTRODE,
    REFERENCE_ELECTRODE,
    Recording,
    RingBuffer,
    band_power,
    dominant_band,
    label_artifacts,
    open_stream,
    relative_band_power,
    to_csv,
    to_edf,
    to_json,
)
from broadmind.artifacts import (
    ARTIFACT_MARKER_CODES,
    Artifact,
    detect_blinks,
    detect_clenches,
    summarize,
)
from broadmind.board import (
    DEVICE,
    UnsupportedTransportError,
    make_board_config,
    microvolts_per_count,
)
from broadmind.export import ExportError, load, read_csv, read_edf, read_json
from broadmind.montage import (
    MontageError,
    POSITIONS_2D,
    reorder,
    validate_channel_order,
)
from broadmind.protocol import (
    PACKET_LENGTH,
    START_BYTE,
    STOP_BYTE,
    PacketParser,
    ProtocolError,
    decode_packet,
    encode_packet,
)

FS = 250.0
N_CHANNELS = 8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _recording(seconds: float = 2.0, seed: int = 0) -> Recording:
    """A deterministic 8-channel recording: 5 uV noise + 12 uV alpha."""
    n = int(round(seconds * FS))
    t = np.arange(n) / FS
    rng = np.random.default_rng(seed)
    data = rng.standard_normal((N_CHANNELS, n)) * 5.0
    data += 12.0 * np.sin(2 * np.pi * 10.0 * t)[None, :]
    return Recording(
        data=data,
        sampling_rate=FS,
        channel_names=list(CHANNEL_NAMES),
        metadata={"transport": "synthetic", "test": "test_smoke"},
        artifacts=[Artifact("blink", 0.5, 0.3, 234.5, ("FT7", "FT8"), 75.0)],
    )


def _bump(n: int, center_s: float, width_s: float, amplitude_uv: float) -> np.ndarray:
    """A Hann-shaped positive excursion (a stand-in for a blink)."""
    y = np.zeros(n)
    i0 = max(0, int(round((center_s - width_s / 2) * FS)))
    i1 = min(n, int(round((center_s + width_s / 2) * FS)))
    if i1 > i0:
        y[i0:i1] = amplitude_uv * np.hanning(i1 - i0)
    return y


def _clean_noise(n: int, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, 8.0, size=(N_CHANNELS, n))


def _clench_burst(n: int, start_s: float, duration_s: float) -> np.ndarray:
    """A high-frequency EMG-like burst placed into a zero buffer."""
    y = np.zeros(n)
    i0 = int(round(start_s * FS))
    i1 = min(n, int(round((start_s + duration_s) * FS)))
    if i1 <= i0:
        return y
    t = np.arange(i1 - i0) / FS
    burst = (
        70.0 * np.sin(2 * np.pi * 25.0 * t)
        + 60.0 * np.sin(2 * np.pi * 32.0 * t)
        + 50.0 * np.sin(2 * np.pi * 40.0 * t)
    )
    y[i0:i1] = burst * np.hanning(i1 - i0)
    return y


# ---------------------------------------------------------------------------
# Montage
# ---------------------------------------------------------------------------


def test_montage_order_and_length():
    assert len(CHANNEL_NAMES) == N_CHANNELS == 8
    assert CHANNEL_NAMES == ("CP1", "CP2", "C3", "C4", "CP5", "CP6", "FT7", "FT8")
    assert CHANNEL_NAMES == tuple(DEVICE.channel_names)
    assert GROUND_ELECTRODE == "Cz"
    assert REFERENCE_ELECTRODE == "CPz"
    assert validate_channel_order(CHANNEL_NAMES) == CHANNEL_NAMES

    # Plotting layout: one finite position per channel, all distinct.
    assert set(POSITIONS_2D) == set(CHANNEL_NAMES)
    positions = np.asarray(list(POSITIONS_2D.values()), dtype=float)
    assert positions.shape == (8, 2)
    assert np.all(np.isfinite(positions))
    assert len({tuple(p) for p in positions}) == 8
    assert np.all(np.abs(positions) <= 1.0)

    # Wrong order is rejected, and reorder() puts it back.
    with pytest.raises(MontageError):
        validate_channel_order(list(reversed(CHANNEL_NAMES)))
    data = np.arange(16, dtype=float).reshape(8, 2)
    back = reorder(data[::-1, :], list(reversed(CHANNEL_NAMES)))
    assert np.array_equal(back, data)

    # A recording with the right names in the wrong order is refused.
    with pytest.raises(MontageError):
        Recording(
            data=np.zeros((8, 4)),
            sampling_rate=FS,
            channel_names=list(reversed(CHANNEL_NAMES)),
        )


# ---------------------------------------------------------------------------
# Band power
# ---------------------------------------------------------------------------


def test_band_power_alpha_dominates_on_10hz_sine():
    n = int(4 * FS)
    t = np.arange(n) / FS
    amplitude = 10.0
    sine = amplitude * np.sin(2 * np.pi * 10.0 * t)

    powers = band_power(sine, FS)
    assert set(powers) == set(BANDS_HZ)
    assert all(values.shape == (1,) for values in powers.values())

    alpha = float(powers["alpha"][0])
    # A pure sine of amplitude A integrates to A**2 / 2 = 50 uV^2.
    assert 40.0 < alpha < 60.0, f"alpha band power {alpha} not near A^2/2"
    for name in ("delta", "theta", "beta", "gamma"):
        assert float(powers[name][0]) < alpha / 100.0, name

    # Parseval: integrating the whole PSD reproduces the signal variance.
    freqs, psd = broadmind.power_spectrum(sine, FS)
    total = float(psd.sum(axis=1)[0] * (freqs[1] - freqs[0]))
    assert abs(total - float(np.var(sine))) < 0.5

    assert dominant_band(sine, FS) == ["alpha"]
    relative = relative_band_power(sine, FS)
    assert float(relative["alpha"][0]) > 0.99
    assert abs(sum(float(v[0]) for v in relative.values()) - 1.0) < 1e-9

    # Two channels, second one twice as loud -> four times the power.
    stereo = np.stack([sine, 2.0 * sine])
    stereo_powers = band_power(stereo, FS)
    assert stereo_powers["alpha"].shape == (2,)
    ratio = float(stereo_powers["alpha"][1] / stereo_powers["alpha"][0])
    assert 3.9 < ratio < 4.1


# ---------------------------------------------------------------------------
# Artefacts
# ---------------------------------------------------------------------------


def test_blink_detector_labels_without_touching_the_signal():
    n = int(4 * FS)
    data = _clean_noise(n)
    data[[6, 7], :] += _bump(n, center_s=2.0, width_s=0.35, amplitude_uv=250.0)
    original = data.copy()

    found = detect_blinks(data, FS)
    assert len(found) == 1, f"expected exactly one blink, got {found}"
    blink = found[0]
    assert blink.label == "blink"
    assert blink.marker_code == ARTIFACT_MARKER_CODES["blink"] == 1
    assert 1.6 <= blink.onset_s <= 2.3, blink.onset_s
    assert 0.05 <= blink.duration_s <= 0.8, blink.duration_s
    assert blink.peak_uv > 100.0, blink.peak_uv
    assert {"FT7", "FT8"}.issubset(set(blink.channels)), blink.channels

    # Detection must not modify the caller's array, not even one sample.
    assert np.array_equal(data, original), "detector mutated the input signal"

    # Clean, artefact-free data must not produce labels.
    clean = _clean_noise(n, seed=11)
    assert detect_blinks(clean, FS) == []


def test_clench_detector_and_combined_labels():
    n = int(4 * FS)
    data = _clean_noise(n)
    data += _clench_burst(n, start_s=3.0, duration_s=0.5)[None, :]
    data[[6, 7], :] += _bump(n, center_s=1.0, width_s=0.35, amplitude_uv=250.0)
    original = data.copy()

    clenches = detect_clenches(data, FS)
    assert len(clenches) == 1, clenches
    assert clenches[0].label == "clench"
    assert 2.8 <= clenches[0].onset_s <= 3.3
    assert clenches[0].peak_uv > 20.0

    # The two detectors do not fire on each other's artefact class.
    assert detect_clenches(_clean_noise(n) + _bump(n, 2.0, 0.35, 250.0)[None, :], FS) == []

    labels = label_artifacts(data, FS)
    assert [a.label for a in labels] == ["blink", "clench"]
    assert labels[0].onset_s < labels[1].onset_s
    counts = summarize(labels)
    assert counts["blink"] == 1 and counts["clench"] == 1 and counts["total"] == 2
    assert np.array_equal(data, original)

    with pytest.raises(ValueError):
        label_artifacts(data, FS, labels=["blink", "not-a-label"])

    # EDF/CSV marker contract is stable and documented.
    assert ARTIFACT_MARKER_CODES == {"blink": 1, "clench": 2}


# ---------------------------------------------------------------------------
# Protocol codec
# ---------------------------------------------------------------------------


def test_packet_codec_roundtrip_and_parser_resync():
    counts = [-8388608, 8388607, 0, -1, 1, 123456, -123456, 42]
    packet = encode_packet(7, counts, aux=bytes(range(6)))
    assert len(packet) == PACKET_LENGTH == 33
    assert packet[0] == START_BYTE and packet[-1] == STOP_BYTE

    sample = decode_packet(packet)
    assert sample.sample_number == 7
    assert sample.channels == tuple(counts)
    assert sample.aux == bytes(range(6))

    # ADC counts -> microvolts reproduces OpenBCI's published gain-24 factor.
    assert abs(microvolts_per_count(24.0) - 0.0223517413) < 1e-9
    uv = sample.to_uv(gain=24.0)
    assert abs(uv[4] - 0.0223517413) < 1e-9  # one count at gain 24

    with pytest.raises(ProtocolError):
        decode_packet(packet[:-1])
    with pytest.raises(ProtocolError):
        encode_packet(0, [0] * 7)

    # Stream parser: garbage, a whole packet, then a split packet.
    parser = PacketParser()
    first = parser.feed(b"\x01\x02junk" + packet + packet[:10])
    assert len(first) == 1
    assert parser.bytes_discarded == 6
    assert parser.buffered_bytes == 10
    second = parser.feed(packet[10:])
    assert len(second) == 1
    assert parser.buffered_bytes == 0
    assert parser.packets_parsed == 2


# ---------------------------------------------------------------------------
# Ring buffer
# ---------------------------------------------------------------------------


def test_ring_buffer_keeps_newest_samples_in_order():
    buffer = RingBuffer(n_channels=2, capacity=5)
    buffer.append(np.arange(6, dtype=float).reshape(2, 3))          # 0,1,2
    buffer.append(np.arange(6, 14, dtype=float).reshape(2, 4))      # 6,7,8,9
    assert buffer.total_appended == 7
    assert len(buffer) == 5
    stored = buffer.all()
    # Channel 0 saw 0,1,2,6,7,8,9 -> the newest five are 2,6,7,8,9.
    assert stored[0].tolist() == [2.0, 6.0, 7.0, 8.0, 9.0]
    assert stored[1].tolist() == [5.0, 10.0, 11.0, 12.0, 13.0]
    assert buffer.newest(2).tolist() == [[8.0, 9.0], [12.0, 13.0]]

    buffer.clear()
    assert len(buffer) == 0
    assert buffer.all().shape == (2, 0)


# ---------------------------------------------------------------------------
# Export round trips
# ---------------------------------------------------------------------------


def test_csv_roundtrip(tmp_path: Path):
    recording = _recording()
    path = to_csv(recording, tmp_path / "rec.csv")
    loaded = read_csv(path)

    assert loaded.channel_names == recording.channel_names
    assert loaded.sampling_rate == pytest.approx(recording.sampling_rate)
    assert loaded.data.shape == recording.data.shape
    assert np.max(np.abs(loaded.data - recording.data)) < 1e-6
    assert loaded.marker_column().tolist().count(1) == 1  # blink marker present
    assert len(loaded.artifacts) == 1
    assert loaded.artifacts[0].label == "blink"
    assert loaded.artifacts[0].onset_s == pytest.approx(0.5, abs=1e-6)


def test_json_roundtrip(tmp_path: Path):
    recording = _recording()
    path = to_json(recording, tmp_path / "rec.json")
    loaded = read_json(path)

    assert loaded.channel_names == recording.channel_names
    assert loaded.sampling_rate == pytest.approx(recording.sampling_rate)
    assert np.array_equal(loaded.data, recording.data)  # exact float round trip
    assert loaded.metadata.get("transport") == "synthetic"
    assert [a.label for a in loaded.artifacts] == ["blink"]
    assert loaded.artifacts[0].peak_uv == pytest.approx(234.5)


def test_edf_roundtrip(tmp_path: Path):
    recording = _recording()
    info = to_edf(recording, tmp_path / "rec.edf")
    assert info["samples_padded"] == 0
    assert info["duration_s"] == pytest.approx(2.0)

    loaded = read_edf(tmp_path / "rec.edf")
    assert loaded.channel_names == recording.channel_names
    assert loaded.sampling_rate == pytest.approx(250.0)
    assert loaded.n_samples == recording.n_samples
    # EDF is 16-bit quantised over the channel's physical range.
    assert np.max(np.abs(loaded.data - recording.data)) < 0.02
    assert [a.label for a in loaded.artifacts] == ["blink"]
    assert loaded.artifacts[0].onset_s == pytest.approx(0.5, abs=1e-3)
    assert loaded.artifacts[0].duration_s == pytest.approx(0.3, abs=1e-3)


def test_edf_pads_non_integer_seconds_and_rejects_unsupported_formats(tmp_path: Path):
    # 3.5 s is not a whole number of 1 s EDF data records.
    n = 875
    recording = Recording(
        data=np.zeros((N_CHANNELS, n), dtype=float),
        sampling_rate=FS,
        channel_names=list(CHANNEL_NAMES),
    )
    out = tmp_path / "padded.edf"
    info = to_edf(recording, out)
    assert info["samples_padded"] == 125
    assert info["n_samples"] == 1000
    assert read_edf(out).n_samples == 1000

    with pytest.raises(ExportError):
        to_edf(recording, tmp_path / "nopad.edf", pad_to_whole_seconds=False)
    with pytest.raises(ExportError):
        broadmind.export_recording(recording, tmp_path / "bad.xyz")


def test_load_dispatch_and_recording_validation(tmp_path: Path):
    recording = _recording(seconds=1.0)
    for name in ("a.csv", "a.edf", "a.json"):
        broadmind.export_recording(recording, tmp_path / name)
        loaded = load(tmp_path / name)
        assert loaded.n_channels == N_CHANNELS
        assert loaded.duration_s == pytest.approx(1.0)
    with pytest.raises(ValueError):
        Recording(data=np.zeros((7, 10)), sampling_rate=FS, channel_names=list(CHANNEL_NAMES))


# ---------------------------------------------------------------------------
# Board configuration / transport honesty
# ---------------------------------------------------------------------------


def test_board_config_and_transport_status():
    synthetic = make_board_config("synthetic")
    assert synthetic.board_id == -1  # BoardIds.SYNTHETIC_BOARD
    assert synthetic.sampling_rate() == 250
    assert synthetic.eeg_rows()[:8] == (1, 2, 3, 4, 5, 6, 7, 8)
    assert synthetic.channel_names() == CHANNEL_NAMES
    assert "verified" in synthetic.status

    serial = make_board_config("serial", serial_port="/dev/cu.usbserial-TEST")
    assert serial.board_id == 0  # BoardIds.CYTON_BOARD
    params = serial.input_params()
    assert params.serial_port == "/dev/cu.usbserial-TEST"
    # The vendor states the kit is OpenBCI-Cyton compatible; this SDK has not
    # confirmed it on hardware, and the status string must keep saying so.
    assert "not yet verified" in serial.status

    with pytest.raises(UnsupportedTransportError):
        make_board_config("ble")
    with pytest.raises(ValueError):
        make_board_config("carrier-pigeon")


# ---------------------------------------------------------------------------
# Live synthetic session (BrainFlow integration; no hardware)
# ---------------------------------------------------------------------------


def test_synthetic_board_collect_and_callback():
    received: list[int] = []
    with open_stream() as stream:
        assert stream.sampling_rate == 250.0
        block = stream.collect(seconds=1.0)
        assert block.data.shape == (N_CHANNELS, 250)
        assert block.duration_s == pytest.approx(1.0)
        assert np.all(np.diff(block.timestamps) > 0)
        assert np.all(np.isfinite(block.data))
        assert len(stream.buffer) >= 250

        stream.subscribe(lambda b: received.append(b.n_samples))
        time.sleep(0.8)
        stream.unsubscribe()
    assert len(received) > 0
    assert sum(received) >= 100
    assert stream.is_streaming is False and stream.is_prepared is False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_info_json():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "broadmind.cli", "info", "--json"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["montage"]["channels"] == list(CHANNEL_NAMES)
    assert payload["montage"]["ground"] == "Cz"
    assert payload["montage"]["reference"] == "CPz"
    assert payload["device"]["sampling_rate_hz"] == 250
    assert payload["device"]["n_channels"] == 8
    assert payload["transports"]["ble"].startswith("not implemented")
    assert "brainflow" in payload["versions"]


# ---------------------------------------------------------------------------
# OpenBCI-compatible transports: serial (Cyton) and WiFi (ESP32 bridge)
# ---------------------------------------------------------------------------


def test_serial_transport_maps_to_cyton_board():
    """The kit speaks the OpenBCI Cyton protocol, so the serial path is board 0."""
    from brainflow.board_shim import BoardIds

    from broadmind.board import make_board_config

    config = make_board_config("serial", serial_port="/dev/cu.usbserial-TEST")
    assert config.board_id == int(BoardIds.CYTON_BOARD)
    assert config.serial_port == "/dev/cu.usbserial-TEST"
    params = config.input_params()
    assert params.serial_port == "/dev/cu.usbserial-TEST"


def test_wifi_transport_maps_to_cyton_wifi_board_and_carries_endpoint():
    """An ESP32 bridge speaking the OpenBCI WiFi-shield protocol is board 5."""
    from brainflow.board_shim import BoardIds

    from broadmind.board import (
        DEFAULT_WIFI_PORT,
        DEFAULT_WIFI_PROTOCOL,
        make_board_config,
    )

    config = make_board_config("wifi", ip_address="192.168.4.1")
    assert config.board_id == int(BoardIds.CYTON_WIFI_BOARD)
    assert config.ip_port == DEFAULT_WIFI_PORT  # 9000, BrainFlow's expectation
    assert config.ip_protocol == DEFAULT_WIFI_PROTOCOL

    params = config.input_params()
    assert params.ip_address == "192.168.4.1"
    assert params.ip_port == DEFAULT_WIFI_PORT
    assert params.ip_protocol == DEFAULT_WIFI_PROTOCOL

    overridden = make_board_config("wifi", ip_address="10.0.0.7", ip_port=9100)
    assert overridden.ip_port == 9100
    assert overridden.input_params().ip_port == 9100


def test_wifi_transport_requires_an_address():
    """Failing loudly beats opening a board that can never connect."""
    import pytest

    from broadmind.board import BoardConfigError, make_board_config

    with pytest.raises(BoardConfigError) as excinfo:
        make_board_config("wifi")
    assert "ip_address" in str(excinfo.value)


def test_transport_status_is_explicit_about_what_is_unverified():
    """No transport may claim hardware verification it does not have."""
    from broadmind.board import TRANSPORT_STATUS

    assert TRANSPORT_STATUS["synthetic"].startswith("verified")
    for name in ("serial", "wifi", "ble"):
        status = TRANSPORT_STATUS[name].lower()
        assert "verified" not in status or "not yet verified" in status, name
