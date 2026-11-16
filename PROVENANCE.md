# Provenance, attribution and licence chain

This file says exactly what in this repository was derived from other projects,
what was written from scratch, and what licence applies. If a claim here and a
claim in the README ever disagree, the README's "Verified vs written-but-untested"
table is the stricter one.

**Summary:** this repository contains MIT-licensed original code. It *depends
on* BrainFlow (MIT), edfio (Apache-2.0) and NumPy (BSD-3-Clause) but vendors
none of them. It uses OpenBCI's public protocol documentation and published
constants as references, and re-implements the parts it needs; no OpenBCI source
code is copied into this repository. The BROADMIND device firmware, hardware
design and Bluetooth protocol are proprietary and are **not** part of this
repository.

---

## 1. Runtime dependencies

| Dependency | Version verified here | Licence | How this repo uses it |
|---|---|---|---|
| [BrainFlow](https://github.com/brainflow-dev/brainflow) | 5.24.0 | MIT | Transport and data acquisition. `BoardShim`, `BoardIds`, `BrainFlowInputParams`, board descriptors. |
| [edfio](https://pypi.org/project/edfio/) | 0.4.18 | Apache-2.0 | EDF/EDF+ container writing and reading. Used as an installed dependency, not vendored or modified. |
| [NumPy](https://numpy.org/) | 2.4.6 | BSD-3-Clause | All array maths, ring buffer, periodogram, detectors. |
| [pytest](https://pytest.org/) | 9.2.0 | MIT | Test runner only (`[project.optional-dependencies].test`). |
| [MNE-Python](https://mne.tools/) | 1.13.2 | BSD-3-Clause | **Not a dependency.** Used once during development to verify that exported EDF files load correctly. |

`pyproject.toml` declares `numpy`, `brainflow` and `edfio` as the only runtime
requirements. There are no transitive proprietary requirements.

---

## 2. Design references (not vendored)

The SDK deliberately builds on the OpenBCI ecosystem rather than inventing a
device stack. The following projects were read as *references*. None of their
code is copied into this repository.

### 2.1 `openbci-archive/OpenBCI_Python`

* URL: <https://github.com/openbci-archive/OpenBCI_Python>
* Licence: MIT ("Copyright (c) 2015 OpenBCI")
* Used for: the historical Cyton packet parser is the documented reference for
  the 33-byte sample frame; its constants file documents the gain-24
  scale factor `0.0223517413 µV/count` that this repo reproduces and checks
  numerically.
* What this repo takes: the *frame layout* and the *published constant* (facts
  about the protocol), not source code.

### 2.2 `OpenBCI/OpenBCI_GUI`

* URL: <https://github.com/OpenBCI/OpenBCI_GUI>
* Licence: MIT
* Used for: confirming how a mainstream OpenBCI consumer reads the same stream
  (board buffer rows, timestamps, marker channel) so that this SDK's buffer
  assumptions are conventional rather than invented.

### 2.3 `OpenBCI/OpenBCI_Cyton_Library`

* URL: <https://github.com/OpenBCI/OpenBCI_Cyton_Library>
* Licence: MIT
* Used for: firmware-side framing of the same 33-byte packet, as a second
  independent confirmation of the layout.

### 2.4 BrainFlow

* URL: <https://github.com/brainflow-dev/brainflow>
* Licence: MIT
* Used as a **runtime dependency**, not merely a reference: board ids,
  channel-row helpers, timestamp/marker rows, session lifecycle, synthetic board
  for testing.

### 2.5 OpenBCI public documentation

* URL: <https://docs.openbci.com/>
* Used for: published ADS1299 scaling relationship (`VREF / 2^23 / gain`), the
  33-byte frame description, and marker semantics. Documentation of a protocol is
  a description, not a licence-granted asset; the implementation here is
  original.

---

## 3. What is original to this repository

Everything below was written for this project:

* Package structure and all public APIs (`board.py`, `montage.py`, `stream.py`,
  `features.py`, `artifacts.py`, `export.py`, `protocol.py`, `cli.py`).
* The `RingBuffer`, `Block`, `Recording`, `Artifact` data model and the
  callback-based streaming design.
* Montage contract (CP1..FT8 order, Cz GND / CPz DRL), validation and reorder
  helpers, and the approximate plotting layout (hand-derived from the 10-20
  lattice for this project).
* Hann-windowed periodogram and band-power integration implemented in NumPy.
* Blink and jaw-clench detectors (robust threshold model, envelope logic,
  duration gating) and the stable marker-code contract.
* CSV/JSON formats, the metadata header, the marker column, the EDF export
  policy (whole-second padding, per-channel physical ranges, annotations, EDF+
  start-time handling), and the reader/round-trip implementations.
* The 33-byte frame encoder/decoder and resynchronising stream parser - written
  from the documented layout, not adapted from OpenBCI code.
* CLI, tests, documentation, examples, and this provenance file.

The SDK's *design decisions* (labels instead of deletions; no cloud; honesty
tables; no re-referencing) are original editorial choices.

---

## 4. What is NOT in this repository

* **No OpenBCI source code.** The packet codec is a clean-room re-implementation
  from the documented byte layout. Consequently the MIT notices of
  `OpenBCI_Python`, `OpenBCI_GUI` and `OpenBCI_Cyton_Library` are not legally
  required in this repository; they are reproduced below anyway, because
  attribution is the honest thing to do.
* **No BrainFlow source.** BrainFlow is imported as a dependency.
* **No BROADMIND firmware, hardware files, BLE protocol, firmware images or
  proprietary protocol captures.** The device's own wire protocol is unknown to
  this repository, and the README/SDK do not claim to speak it.
* **No human EEG recordings.** The repository ships no participant data.
* **No third-party assets** (no images, fonts, audio or model weights).

---

## 5. Licence chain

```
BROADMIND-authored code in this repository       MIT  (this repository)
  ├── depends on BrainFlow                       MIT
  ├── depends on edfio                           Apache-2.0
  └── depends on NumPy                           BSD-3-Clause

Design references (no code copied):
  ├── openbci-archive/OpenBCI_Python             MIT
  ├── OpenBCI/OpenBCI_GUI                        MIT
  ├── OpenBCI/OpenBCI_Cyton_Library              MIT
  └── OpenBCI public documentation               (factual reference)
```

Compatibility reasoning:

* **MIT out, MIT in:** this repository is MIT; BrainFlow is MIT; the OpenBCI
  projects referenced are MIT. Compatible in every direction.
* **Apache-2.0 (edfio) as a dependency:** Apache-2.0 software can be used by
  MIT-licensed projects without changing their licence. Apache-2.0's NOTICE
  obligations apply when Apache-2.0 *code* is redistributed or modified; this
  repository neither vendors nor modifies edfio, so no NOTICE aggregation is
  required. If edfio is ever vendored, add its NOTICE file.
* **BSD-3-Clause (NumPy / MNE):** attribution-only; compatible with MIT.
* **Nothing copyleft** (no GPL/LGPL/AGPL) is imported, linked or copied.
* The BroadMind hardware/firmware remains proprietary and is outside this
  licence chain. Publishing this SDK does not open-source the device.

### Attribution notices reproduced for good practice

```
OpenBCI_Python - The MIT License (MIT) - Copyright (c) 2015 OpenBCI
OpenBCI_GUI / OpenBCI_Cyton_Library - The MIT License (MIT) - Copyright OpenBCI
BrainFlow - The MIT License (MIT) - Copyright BrainFlow contributors
```

---

## 6. Suggested attribution wording

For the README, site or press kit:

> **broadmind-eeg** is BROADMIND's Python SDK for its 8-channel dry-electrode EEG
> headset. Data acquisition is built on [BrainFlow](https://brainflow.org), the
> MIT-licensed open-source acquisition core for OpenBCI-compatible hardware. The
> packet format follows OpenBCI's public documentation. BROADMIND is not
> affiliated with or endorsed by OpenBCI or BrainFlow.

Trademark note: "OpenBCI" is a trademark of OpenBCI Inc.; "BrainFlow" identifies
the BrainFlow project. Neither organisation has endorsed this SDK. Use the names
descriptively, not as branding.

---

## 7. Verification performed for this provenance file

| Check | Result |
|---|---|
| `pip install brainflow` in the project venv | Succeeded, 5.24.0 |
| `SYNTHETIC_BOARD` session + data pull before SDK code | Succeeded (32x501 float64) |
| `brainflow` licence metadata | MIT per [PyPI](https://pypi.org/project/brainflow/) and repository `LICENSE` |
| `edfio` licence metadata | `License: Apache-2.0` from installed package metadata (0.4.18) |
| OpenBCI repo licences via GitHub API | `OpenBCI_Python` MIT, `OpenBCI_GUI` MIT, `OpenBCI_Cyton_Library` MIT |
| Gain-24 scale factor reproduction | `4.5/2**23/24*1e6 = 0.0223517418` vs published `0.0223517413` (< 1e-9 relative) |
| Wheel contains no vendored third-party code | `broadmind_eeg-0.1.0-py3-none-any.whl` contains only `broadmind/*.py` + metadata |
| No OpenBCI source copied | Manual review of every module; codec authored from the documented layout |

---

## 8. Changes to this file

Any future vendoring, code reuse from another project, or change of licence must
be recorded here with a date and a reason. Silence is not consent.
