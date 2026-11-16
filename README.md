# broadmind-eeg

**Python SDK for the BROADMIND 8-channel dry-electrode EEG headset** - live
acquisition, ring-buffered streaming, band power, artefact *labelling* and
CSV/EDF/JSON export.

| | |
|---|---|
| Version | 0.1.0 (experimental, pre-release) |
| Python | 3.10+ (tested on 3.11.3) |
| Licence | MIT - see [LICENSE](LICENSE) and [PROVENANCE.md](PROVENANCE.md) |
| Transport | [BrainFlow](https://brainflow.org) (OpenBCI-compatible open-source core) |
| Hardware tested | **None.** Verified against BrainFlow's `SYNTHETIC_BOARD` only |
| Clinical use | **No.** Not a medical device, no diagnostic claims, no interpretation |

---

## Why this repository exists

BROADMIND's public site claims **"Eight language bindings on an open-source
core"**. At the time this SDK was written, the company's GitHub
(`BROADMIND-BCI`) contained three forks of OpenBCI repositories and no original
code, so that sentence described an intention, not a product.

This repository makes the claim true in the narrow, defensible sense and refuses
the broad one:

* it ships **one real binding - Python** - that is original code, tested, and
  honest about its limits;
* the other seven languages are reachable today through **BrainFlow**, the MIT
  open-source data-acquisition core this SDK depends on (see
  [Bindings and status](#bindings-and-status));
* nothing here claims hardware-level verification that has not happened.

The whole point is that a claim should not run ahead of the code. Where the code
stops, this README says so.

---

## Quick start

```bash
# 1. Dependencies
pip install brainflow numpy edfio

# 2. The SDK (from a checkout)
cd sdk/broadmind-eeg
pip install -e .

# 3. No hardware needed - use BrainFlow's synthetic board
broadmind info
```

```python
from broadmind import open_stream

with open_stream() as stream:          # synthetic board: verified, no hardware
    block = stream.collect(seconds=2)
    print(block.data.shape)            # (8, 500) = 8 channels x 2 s @ 250 Hz
    print(block.duration_s)            # 2.0
    print(stream.channel_names)        # ('CP1', 'CP2', 'C3', 'C4', 'CP5', 'CP6', 'FT7', 'FT8')
```

Record, label artefacts and export in one go:

```python
from broadmind import open_stream
from broadmind.export import export

with open_stream() as stream:
    recording = stream.record(seconds=10, detect_artifacts=True)   # labels only

export(recording, "session.edf")     # or .csv / .json, by extension
```

Band power (NumPy only, no SciPy):

```python
from broadmind import band_power, open_stream

with open_stream() as stream:
    recording = stream.record(seconds=10)

powers = band_power(recording.data, recording.sampling_rate)
print(powers["alpha"])               # array of alpha power (uV^2) per channel
```

Run the examples (they work without hardware):

```bash
python examples/01_stream.py
python examples/02_band_power.py
python examples/03_record_edf.py
python examples/04_artifacts.py
```

---

## Command line

```bash
$ broadmind info                 # device spec, montage, transports, versions, serial ports
$ broadmind info --json          # machine-readable
$ broadmind record --seconds 10 --out session.edf --detect-artifacts
$ broadmind record --seconds 5  --out session.csv --format csv
```

Transports are printed with their real status:

| `--transport` | Status |
|---|---|
| `synthetic` (default) | **Verified.** BrainFlow synthetic board, no hardware |
| `serial` | **Written, unverified.** OpenBCI-compatible 8-channel 250 Hz serial path |
| `ble` | **Not implemented.** BroadMind's BLE protocol is not public |

`--transport ble` fails with an explicit message rather than pretending.

---

## Device specification (vendor-stated)

Reproduced from BROADMIND's site so SDK and marketing agree. **These values are
the vendor's claims; this repository does not measure them.**

| Property | Value |
|---|---|
| Channels | 8 dry electrodes (Full Kit: **active**; Starter Kit: passive) |
| Montage | CP1, CP2, C3, C4, CP5, CP6, FT7, FT8 (10-20 sensorimotor) |
| Ground / reference | **Cz = GND**, **CPz = DRL** |
| Sampling / resolution | 250 Hz, 24-bit |
| Bandwidth | 0.02-125 Hz |
| Pre-amplification | x5 at the electrode |
| Input-referred noise | 0.16 µVrms (Full Kit) / 0.8 µVrms (Starter Kit) |
| Connectivity | USB-C, magnetic 360 connector, Bluetooth LE |
| Exports (site) | CSV, EDF, JSON; loadable in EEGLAB / MNE |
| Artefacts (site) | blink and clench labels marked in the stream, never deleted |

The SDK implements the montage, the sampling assumptions, the export formats and
the "labels, never deletion" rule. It does **not** implement Bluetooth and has not
verified the noise or bandwidth figures. See [docs/montage.md](docs/montage.md)
and [docs/protocol.md](docs/protocol.md).

---

## What's in the box

| Module | What it does | What it deliberately does not do |
|---|---|---|
| `broadmind.board` | Device spec, BrainFlow board ids/transports, serial + Bluetooth enumeration | Implement the device wire protocol or claim protocol compatibility |
| `broadmind.montage` | Channel order, Cz/CPz roles, plotting layout, reorder helper | Measure scalp positions or re-reference data |
| `broadmind.stream` | Sessions, ring buffer, blocking `collect`, callback API, markers | Filter, drop or interpret samples |
| `broadmind.features` | Hann-windowed periodogram, band power (delta/theta/alpha/beta/gamma) | Artefact rejection, referencing, clinical interpretation |
| `broadmind.artifacts` | Blink + jaw-clench detection returning **labels** | Mutate, filter, delete or "correct" the signal |
| `broadmind.export` | CSV, EDF+, JSON writers and readers | Upload, de-identify beyond the EDF patient field, resample |
| `broadmind.protocol` | OpenBCI-compatible 33-byte packet codec + resyncing parser | Speak the BroadMind protocol, WiFi/Daisy/dongle framing |
| `broadmind.cli` | `broadmind info`, `broadmind record` | Open Bluetooth, or upload anything |

Every module docstring starts with "What this module does" and "What this module
deliberately does NOT do".

---

## Exports

| Format | Content | Round trip | Caveats |
|---|---|---|---|
| **CSV** | `time_s`, 8 channels, `marker`; JSON metadata header | ~1e-9 µV | Marker column carries artefact codes |
| **EDF+** | 8 signals in µV, artefact labels as EDF+ annotations | ~0.001-0.01 µV (16-bit quantisation) | Data records are 1 s, so lengths are zero-padded to whole seconds (reported in `to_edf()`'s return value) |
| **JSON** | Full float64 samples + metadata + labels | exact | Verbose for long sessions |

EDF files were verified to load in **MNE 1.13.2**
(`mne.io.read_raw_edf`, correct channel names, 250 Hz, annotations preserved,
data within 0.0006 µV after unit conversion). **EEGLAB was not tested** - there
is no MATLAB on the build machine - so the site's EEGLAB claim remains
unverified by this repository.

Artefact labels travel as data, not edits: CSV gets a marker code at the event
onset sample, EDF+ gets an annotation, JSON gets an `artifacts` list. No exporter
removes or rewrites a sample.

---

## Artefacts: labels, never deletions

```python
from broadmind import label_artifacts

artifacts = label_artifacts(data, fs=250, channel_names=("CP1", "CP2", "C3", "C4", "CP5", "CP6", "FT7", "FT8"))
# [Artifact(label='blink', onset_s=1.88, duration_s=0.24, peak_uv=221.6, channels=('FT7', 'FT8')),
#  Artifact(label='clench', onset_s=4.09, duration_s=0.33, peak_uv=159.8, channels=('FT7', 'FT8'))]
```

* `blink` - low-frequency excursion; marker code **1**
* `clench` - 20-45 Hz EMG burst; marker code **2**

Detection is a pure function: the test suite asserts the input array is
bit-identical before and after. Thresholds are heuristics tuned on synthetic
data and **not validated on real recordings**. Treat the labels as an audit
trail, not as cleaned data.

---

## Bindings and status

The site claims eight language bindings. Here is the real state of the world.
"BroadMind-authored" means code in a BROADMIND repository; "BrainFlow upstream"
means the MIT dependency this SDK is built on.

| Language | BroadMind-authored binding | BrainFlow upstream binding | What a user can do today | Status |
|---|:---:|:---:|---|---|
| **Python** | **Yes - this repo** | Yes (`pip install brainflow`) | Acquire, label, analyse, export | **Shipped and tested here** |
| C++ | No | Yes (native core) | Use BrainFlow directly; no BroadMind montage/label/export helpers | Roadmap |
| MATLAB | No | Yes | Use BrainFlow directly; no BroadMind helpers | Roadmap |
| R | No | Yes | Use BrainFlow directly; no BroadMind helpers | Roadmap |
| Julia | No | Yes | Use BrainFlow directly; no BroadMind helpers | Roadmap |
| Java | No | Yes | Use BrainFlow directly; no BroadMind helpers | Roadmap |
| C# | No | Yes | Use BrainFlow directly; no BroadMind helpers | Roadmap |
| Rust | No | Yes (BrainFlow >= 4.8) | Use BrainFlow directly; no BroadMind helpers | Roadmap |

BrainFlow itself documents bindings for Python, C++, Java, C#, Julia, MATLAB, R,
Rust and TypeScript ([brainflow.org](https://brainflow.org),
[repository](https://github.com/brainflow-dev/brainflow)); so **all eight
languages in the site claim are reachable through the open-source core**. That
makes the claim defensible as *"our data path is reachable from eight languages
via an MIT-licensed core"*, and false as *"BROADMIND ships eight SDKs"*. This
repository closes the Python gap and adds the BROADMIND-specific pieces
(montage contract, artefact labels, exporters) that exist in **no** language
yet except Python.

Nothing in this table is "coming next quarter" language: seven BroadMind-authored
bindings do not exist, and this SDK does not generate them.

---

## Verified vs written-but-untested

Honesty is the deliverable, so here is the audit:

| Area | Status | Evidence |
|---|---|---|
| BrainFlow install + synthetic-board acquisition | **Verified** | brainflow 5.24.0 installed; `SYNTHETIC_BOARD` opened, started, data pulled (32x501) before any SDK code was written |
| Synthetic stream, `collect`, callback API, ring buffer | **Verified** | `test_synthetic_board_collect_and_callback`, `test_ring_buffer_keeps_newest_samples_in_order` |
| Montage order (CP1..FT8), Cz GND / CPz DRL | **Verified** | `test_montage_order_and_length` (and enforced by `Recording`) |
| Band power maths | **Verified** | 10 Hz sine, 10 µV amplitude -> alpha = 50.000 µV^2 (`A^2/2`), all other bands < 1 %; Parseval check |
| Blink / clench detection | **Verified on synthetic signals only** | injected 250 µV blink and 25-45 Hz burst: exactly 1 label each, 0 false positives on noise, input unmodified. **Real-data thresholds unvalidated** |
| CSV round trip | **Verified** | max error 5e-10 µV |
| JSON round trip | **Verified** | bit-exact float64 |
| EDF round trip + annotations | **Verified** | max error ~0.001 µV; label/onset/duration preserved; whole-second padding covered |
| EDF loads in MNE | **Verified** | MNE 1.13.2: correct `ch_names`, 250 Hz, annotations `('blink', 0.5, 0.3)` |
| EDF loads in EEGLAB | **Untested** | no MATLAB available |
| CLI `info` / `record` | **Verified** | `test_cli_info_json` + manual runs writing CSV/EDF/JSON |
| Wheel build + `broadmind` console script | **Verified** | `pip wheel .` produced `broadmind_eeg-0.1.0-py3-none-any.whl` containing `entry_points.txt` -> `broadmind = broadmind.cli:main` |
| Serial transport against a real headset | **Written, untested** | no BroadMind hardware; protocol compatibility is an assumption |
| Bluetooth LE transport | **Not implemented** | protocol not public; `--transport ble` raises a clear error |
| OpenBCI 33-byte codec | **Verified on synthetic frames** | full 24-bit range round trip, garbage/resync and split-chunk parsing tested; never observed from BroadMind hardware |
| µV conversion at gain 5 | **Unverified assumption** | matches OpenBCI's published gain-24 constant at gain 24; gain-5 assumes 4.5 V / 24-bit ADC |
| Real EEG data | **None** | the repository contains no human recordings |

---

## Privacy

* Local by default: the SDK writes files where you tell it to and **never**
  uploads, phones home, collects telemetry or trains on your data.
* No cloud component exists to disable.
* EDF patient identification is written as anonymised `X` unless you explicitly
  pass `metadata["subject_id"]`; that choice is yours, not the SDK's.
* Consent, IRB/ethics approval, retention and de-identification are the
  integrator's responsibility. This is a data-acquisition library, not a
  compliance system.

---

## Compatibility with the OpenBCI ecosystem

This SDK does not re-invent device plumbing. It uses
[BrainFlow](https://github.com/brainflow-dev/brainflow) (MIT) as the transport
and data-acquisition layer, and it treats OpenBCI's public documentation as the
reference for the 33-byte packet frame and the ADC scaling constants. Historical
OpenBCI projects are cited as design references, not vendored:

* [`openbci-archive/OpenBCI_Python`](https://github.com/openbci-archive/OpenBCI_Python) - MIT - historical Cyton protocol reference
* [`OpenBCI/OpenBCI_GUI`](https://github.com/OpenBCI/OpenBCI_GUI) - MIT
* [`OpenBCI/OpenBCI_Cyton_Library`](https://github.com/OpenBCI/OpenBCI_Cyton_Library) - MIT
* [`brainflow-dev/brainflow`](https://github.com/brainflow-dev/brainflow) - MIT

See [PROVENANCE.md](PROVENANCE.md) for exactly what is derived, what is
original, and the licence chain. BROADMIND is not affiliated with or endorsed by
OpenBCI or BrainFlow; those names identify the open-source ecosystem this SDK
builds on.

---

## Tests

```bash
cd sdk/broadmind-eeg
python -m pytest tests/test_smoke.py -v
```

Real output on the build machine (Python 3.11.3, macOS, BrainFlow 5.24.0):

```
============================= test session starts ==============================
platform darwin -- Python 3.11.3, pytest-9.2.0, pluggy-1.7.0 -- /Users/admin/Downloads/DeepSeek work/.venv/bin/python
cachedir: .pytest_cache
rootdir: .../sdk/broadmind-eeg
configfile: pyproject.toml
collecting ... collected 14 items

tests/test_smoke.py::test_montage_order_and_length PASSED                [  7%]
tests/test_smoke.py::test_band_power_alpha_dominates_on_10hz_sine PASSED [ 14%]
tests/test_smoke.py::test_blink_detector_labels_without_touching_the_signal PASSED [ 21%]
tests/test_smoke.py::test_clench_detector_and_combined_labels PASSED     [ 28%]
tests/test_smoke.py::test_packet_codec_roundtrip_and_parser_resync PASSED [ 35%]
tests/test_smoke.py::test_ring_buffer_keeps_newest_samples_in_order PASSED [ 42%]
tests/test_smoke.py::test_csv_roundtrip PASSED                           [ 50%]
tests/test_smoke.py::test_json_roundtrip PASSED                          [ 57%]
tests/test_smoke.py::test_edf_roundtrip PASSED                           [ 64%]
tests/test_smoke.py::test_edf_pads_non_integer_seconds_and_rejects_unsupported_formats PASSED [ 71%]
tests/test_smoke.py::test_load_dispatch_and_recording_validation PASSED  [ 78%]
tests/test_smoke.py::test_board_config_and_transport_status PASSED       [ 85%]
tests/test_smoke.py::test_synthetic_board_collect_and_callback PASSED    [ 92%]
tests/test_smoke.py::test_cli_info_json PASSED                           [100%]

======================== 14 passed, 1 warning in 2.34s =========================
```

The warning is from `pkg_resources` inside pytest's tooling, not from this SDK.

---

## What a human engineer must still do before publishing

This is a working scaffold with tested software behaviour, **not** a
hardware-validated SDK. The gates below map to phases P0-P2 of
[`../RND-ROADMAP.md`](../RND-ROADMAP.md); that document carries the dates,
acceptance criteria and the reasoning. Gates, in order:

- [ ] **Hardware-in-the-loop, serial.** Connect a real headset, confirm the
      OpenBCI-compatible path actually works: 8 channels, 250 Hz, channel order
      CP1..FT8, gain/µV scaling. Capture raw bytes and run them through
      `broadmind.protocol.PacketParser`; publish the capture as a fixture.
- [ ] **Establish the real BroadMind wire protocol** (USB and BLE) with
      firmware, then either implement a `bleak`-based BLE transport or scope the
      docs to USB only. Until then `--transport ble` must keep failing loudly.
- [ ] **Validate the artefact detectors on real, expert-labelled recordings**
      per kit (Full vs Starter). Publish sensitivity/specificity and freeze
      calibrated defaults; current defaults are synthetic-tuned heuristics.
- [ ] **Verify EEGLAB/MATLAB import** of the EDF output (MNE 1.13.2 is verified;
      EEGLAB is not).
- [ ] **Confirm the µV conversion assumption** (pre-amp x5, ADC reference,
      24-bit) against the schematic, and correct
      `broadmind.board.microvolts_per_count` / `docs/protocol.md` if it differs.
- [x] **Repository hygiene.** CI matrix (3.10-3.13), ruff, CHANGELOG,
      SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md and issue forms are in
      place. `ruff` is pinned and runs a conservative baseline; tightening it is
      a separate PR, not a release gate.
- [ ] **Release:** run CI once on GitHub, reconcile the site claim, then tag
      `v0.1.0` and configure PyPI trusted publishing. Sequenced as **P0** in
      [`../RND-ROADMAP.md`](../RND-ROADMAP.md).
- [ ] **Fix the site claim** or ship the remaining seven bindings. Recommended
      wording: *"Python SDK with CSV/EDF/JSON export; other languages via
      BrainFlow."*
- [ ] **Privacy/legal review:** consent and retention policy, GDPR/IRB wording
      for EEG data, trademark review (OpenBCI/BrainFlow names), and a final
      dependency-licence audit at release time.

---

## Licence

MIT - see [LICENSE](LICENSE). Design references and dependency licences are
documented in [PROVENANCE.md](PROVENANCE.md). The BROADMIND device's own
firmware, hardware and Bluetooth protocol are proprietary to BROADMIND and are
not part of this repository.
