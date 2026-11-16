# Protocol and data layout

This document describes **exactly** the byte layouts this SDK implements, and
states plainly where each one comes from and what has been verified. If
something is not implemented, it is called out here rather than implied
elsewhere.

Three layers are involved:

1. **BrainFlow board buffer** - what the SDK actually consumes at runtime.
   Implemented and verified (with the synthetic board).
2. **OpenBCI-compatible 33-byte sample frame** - implemented as a codec in
   `broadmind/protocol.py`, unit-tested against synthetic packets, *not*
   observed from BroadMind hardware.
3. **BroadMind's own transport** - **vendor-confirmed OpenBCI-compatible.**
   The vendor states that the kit's firmware protocol is the same protocol an
   OpenBCI board speaks, which is why the serial path maps to BrainFlow's
   `CYTON_BOARD`. This SDK has not yet observed that on hardware; until a real
   capture is committed as a fixture, treat layer 2 as *the expected format*
   and not as a measurement.

   The wireless path is a purpose-built **BLE 5** transport carrying the same
   byte stream — not the legacy OpenBCI WiFi-shield protocol. It is not
   implemented yet; see [`wireless.md`](wireless.md) for the service shape and
   `examples/05_frame_loopback.py` for the framing fixture.

---

## 1. Live path: BrainFlow board buffer

Data acquisition is delegated to [BrainFlow](https://brainflow.org), which owns
the transport and the board-specific parsing. The SDK asks BrainFlow for the
board buffer and reads named rows.

A BrainFlow board buffer is a `float64` NumPy array of shape
`(num_rows, n_samples)`. Row meanings for the two board ids this SDK uses:

**`SYNTHETIC_BOARD` (id -1), 32 rows**

| Row(s) | Content |
|---|---|
| 0 | package number |
| 1-16 | EEG channels 1-16 |
| 17-19 | accelerometer |
| 20-22 | gyroscope |
| 23 | EDA |
| 24-25 | PPG |
| 26 | temperature |
| 27-28 | resistance |
| 29 | battery |
| 30 | **timestamp** |
| 31 | **marker** |

**`CYTON_BOARD` (id 0), 24 rows**

| Row(s) | Content |
|---|---|
| 0 | package number |
| 1-8 | EEG channels 1-8 |
| 9-11 | accelerometer |
| 12-18 | other / auxiliary |
| 19-21 | analog |
| 22 | **timestamp** |
| 23 | **marker** |

(`broadmind.board.BoardConfig` resolves these indices through
`BoardShim.get_eeg_channels`, `get_timestamp_channel` and `get_marker_channel`
rather than hard-coding them, so an overridden `board_id` still works.)

**How BroadMind's eight channels map onto the buffer.** The device has eight
channels in montage order (CP1, CP2, C3, C4, CP5, CP6, FT7, FT8). The SDK takes
the **first eight EEG rows** of the board buffer and labels them with the
BroadMind montage:

```python
eeg_rows = BoardShim.get_eeg_channels(board_id)[:8]   # (1..8 for both boards)
data = raw[eeg_rows, :]                                # (8, n_samples), microvolts
```

Caveats, stated rather than hidden:

* On the **synthetic board** rows 1-8 contain synthetic signals. BrainFlow
  calls them `Fz, C3, Cz, C4, ...`; those anatomical names are **ignored** - the
  data is artificial, so calling row 0 "CP1" is a *stand-in* whose only purpose
  is exercising the shape of the pipeline. No analysis of synthetic data says
  anything about a person.
* On the **serial path** the same first-eight-rows assumption is exactly what
  has *not* been verified against BroadMind hardware. See
  `PROVENANCE.md` and the README status table.

Timestamps are `float64` UNIX seconds produced by the **host** clock, not the
device, so they can drift relative to acquisition and are not a substitute for
hardware synchronisation. Markers are the raw marker-channel values; see
section 5.

---

## 2. The OpenBCI-compatible 33-byte sample frame

Implemented in `broadmind/protocol.py` as `decode_packet`, `encode_packet` and
the streaming `PacketParser`.

| Offset | Size | Field | Value / meaning |
|---:|---:|---|---|
| 0 | 1 | Start byte | `0xA0` |
| 1 | 1 | Sample number | 0-255, wraps at 256 |
| 2-4 | 3 | Channel 1 | 24-bit signed, **big-endian**, two's complement |
| 5-7 | 3 | Channel 2 | same |
| ... | ... | ... | ... |
| 23-25 | 3 | Channel 8 | same |
| 26-31 | 6 | Auxiliary data | 6 bytes, opaque here (accelerometer or external aux) |
| 32 | 1 | Stop byte | `0xC0` |

Total: **33 bytes per sample**. At 250 Hz that is one packet per sample,
4 ms apart, 8 250 B/s.

### 24-bit decoding

```python
value = (b0 << 16) | (b1 << 8) | b2          # 0 .. 16 777 215
if value & 0x800000:                          # sign bit set
    value -= 1 << 24                          # -> -8 388 608
```

Range and examples:

| Bytes | Decoded |
|---|---|
| `7F FF FF` | 8 388 607 (max) |
| `00 00 01` | 1 |
| `FF FF FF` | -1 |
| `80 00 00` | -8 388 608 (min) |

Encoding is the inverse: reject out-of-range values, mask with `0xFFFFFF`,
emit three big-endian bytes.

### Stream resynchronisation

A serial capture can start mid-packet, so `PacketParser.feed()`:

1. searches for the next `0xA0` start byte, counting skipped bytes;
2. waits until 33 bytes are available;
3. requires byte 32 to be `0xC0`, otherwise drops **one** byte and retries
   (a `0xA0` can legitimately occur inside sample data);
4. decodes and consumes the packet.

`parser.bytes_discarded` counts resynchronisation loss; a healthy stream that
attached mid-packet discards at most 32 bytes. The test suite feeds garbage, a
whole packet and a packet split across chunks and asserts this behaviour.

### Limits of the implemented codec

* Only the 33-byte sample frame. Not implemented: OpenBCI **WiFi** framing, the
  **Daisy** 16-channel variant, the USB dongle's outer framing, command/`config`
  messages, or impedance responses.
* `aux` bytes are exposed verbatim and not interpreted.
* The codec is not used on the live path. It exists to make `docs/protocol.md`
  describe real, tested code, to validate offline captures, and to give
  firmware engineers a reference implementation.

---

## 3. ADC counts to microvolts

```python
microvolts_per_count = VREF / 2**(bits-1) / gain * 1e6
```

with `bits = 24`, `VREF = 4.5 V` and the hardware gain in front of the ADC.

| Gain | Source | µV per count |
|---:|---|---:|
| 24 | OpenBCI Cyton default; reproduces OpenBCI's published `0.0223517413` µV/count | 0.0223517418 |
| 5 | BroadMind pre-amplification at the electrode (vendor-stated) | 0.1072883606 |

The gain-24 value matching OpenBCI's published constant is the reason the
formula is trusted here for that case. The gain-5 value is a **derived
assumption**: it holds if BroadMind's ADC reference and bit depth match the
ADS1299-style 4.5 V / 24-bit assumption. That is unverified and is a
hardware-in-the-loop question.

Implemented in `broadmind.board.microvolts_per_count` and
`broadmind.protocol.Sample.to_uv`. Note that on the **live** BrainFlow path,
BrainFlow already returns EEG channels scaled to microvolts, so this conversion
is only used when parsing raw 24-bit counts (offline packets).

---

## 4. Timing and sample counting

* 250 Hz -> 4 ms per sample; 250 samples per second; 15 000 samples per minute.
* Sample numbers in the 33-byte frame are 8-bit and wrap every 256 samples
  (~1.024 s); they are useful for reordering/loss detection but not as a clock.
* BrainFlow timestamps are host wall-clock seconds. They are monotonic in a
  healthy session (the test suite asserts this) but are not device-synchronised.
* The SDK does not interpolate, resample, or fill gaps. Dropped samples are
  visible as gaps in the timestamps, not silently hidden.

---

## 5. Markers and artefact labels

OpenBCI-compatible boards support inserting a marker into the sample stream
(aux/marker channel) without touching the signal. This SDK uses that mechanism
for artefact labels, with stable codes:

| Code | Label | Meaning |
|---:|---|---|
| 1 | `blink` | large slow excursion, detected by `broadmind.artifacts.detect_blinks` |
| 2 | `clench` | high-frequency EMG burst, detected by `broadmind.artifacts.detect_clenches` |

Labels are additive at every layer:

* live: `Stream.insert_marker(1)` / `Stream.mark_artifact(...)` writes the code
  into the stream - it marks the *current* sample, it cannot retroactively
  annotate a past event;
* offline: `Recording.marker_column()` puts the code at the event's onset
  sample in the CSV `marker` column; EDF export writes standard EDF+
  annotations carrying label, onset and duration;
* detection itself is a pure function and never edits, filters or deletes the
  samples (`tests/test_smoke.py::test_blink_detector_labels_without_touching_the_signal`
  asserts bit-identity).

Removing or correcting artefacts is deliberately **not** implemented. That is a
separate, explicit analysis decision, not something an acquisition SDK should do
silently.

---

## 6. What is verified vs unverified

| Item | Status |
|---|---|
| BrainFlow `SYNTHETIC_BOARD` session, `collect`, callback | **Verified on this machine** (BrainFlow 5.24.0) |
| BrainFlow buffer row mapping for synthetic/Cyton boards | **Verified** via `BoardShim.get_*_channel` helpers |
| 33-byte frame encode/decode, full 24-bit range, resync | **Verified** by unit tests with synthetic packets |
| Gain-24 µV/count constant vs OpenBCI published value | **Verified** numerically (matches to < 1e-9) |
| BroadMind hardware emitting this exact frame over USB-C | **Unverified** - no hardware, protocol not public |
| BroadMind gain-5 µV/count assumption | **Unverified** - depends on ADC reference |
| BLE packet format | **Not implemented** - protocol not public |
| WiFi, Daisy, dongle framing, config commands | **Not implemented** |

---

## 7. Sources

The byte layout above follows OpenBCI's public documentation and the historical
`openbci-archive/OpenBCI_Python` parser (MIT). No OpenBCI source code is copied
into this repository; see `PROVENANCE.md`.

* [openbci-archive/OpenBCI_Python](https://github.com/openbci-archive/OpenBCI_Python) - MIT - historical Cyton packet parser and gain constants
* [OpenBCI public documentation](https://docs.openbci.com/) - hardware and data-format documentation
* [OpenBCI/OpenBCI_Cyton_Library](https://github.com/OpenBCI/OpenBCI_Cyton_Library) - MIT - Cyton firmware-side framing reference
* [OpenBCI/OpenBCI_GUI](https://github.com/OpenBCI/OpenBCI_GUI) - MIT - reference consumer of the same stream
* [brainflow-dev/brainflow](https://github.com/brainflow-dev/brainflow) - MIT - the library this SDK delegates acquisition to
* [BrainFlow documentation](https://brainflow.readthedocs.io) - board ids, channel layout helpers
