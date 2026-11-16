# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the version is `0.x`, breaking changes may land in minor releases and the
public API should be treated as unstable.

## [Unreleased]

Nothing yet. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add an entry.

## [0.1.0] - 2026-11-16

First public release. This is a working, tested software scaffold, **not** a
hardware-validated SDK. Everything below was verified against BrainFlow's
`SYNTHETIC_BOARD`; no BroadMind headset was available during development.

### Added

- Python package `broadmind` (`requires-python >= 3.10`) with a `broadmind`
  console script.
- `broadmind.board` - vendor-stated device specification (8 channels, 250 Hz,
  24-bit), BrainFlow board/transport mapping (`SYNTHETIC_BOARD`,
  OpenBCI-compatible `CYTON_BOARD` serial path), `BrainFlowInputParams`
  construction, serial-port enumeration and macOS known-Bluetooth listing.
- `broadmind.montage` - authoritative channel order (`CP1, CP2, C3, C4, CP5,
  CP6, FT7, FT8`), `Cz` ground / `CPz` reference roles, plotting layout,
  validation and reorder helpers.
- `broadmind.stream` - session lifecycle, ring buffer, blocking `collect()`,
  callback-based streaming and marker support.
- `broadmind.features` - Hann-windowed periodogram and delta/theta/alpha/beta/
  gamma band power in pure NumPy (no SciPy).
- `broadmind.artifacts` - blink and jaw-clench detectors that return labels and
  never modify the signal (marker codes `1` and `2`), with a stable
  `Artifact` model.
- `broadmind.export` - CSV, EDF+ and JSON writers plus matching readers;
  artefact labels are preserved as a CSV marker column, EDF+ annotations and a
  JSON list.
- `broadmind.protocol` - clean-room OpenBCI-compatible 33-byte packet codec and
  a resynchronising stream parser.
- `broadmind.cli` - `broadmind info` (with `--json`) and `broadmind record
  --seconds N --out FILE` (CSV/EDF/JSON, optional artefact labelling).
- 14 pytest tests covering the montage, band-power maths, both artefact
  detectors, the packet codec and parser resynchronisation, ring-buffer
  semantics, CSV/JSON/EDF round trips, synthetic-board acquisition and the CLI
  JSON contract. All pass on Python 3.11.3 with BrainFlow 5.24.0.
- Four hardware-free examples (`examples/01_stream.py` through
  `examples/04_artifacts.py`).
- Documentation: `README.md` (including the "verified vs written-but-untested"
  table and the real bindings status table), `docs/montage.md`,
  `docs/protocol.md`, and `PROVENANCE.md` (attribution and licence chain).
- Packaging: `pyproject.toml` with setuptools, runtime dependencies `numpy`,
  `brainflow`, `edfio`, `dev` extras (`pytest`, `ruff`) and a ruff
  configuration (line length 100, target Python 3.10).
- Repository hygiene for public release: MIT `LICENSE`, `.gitignore`,
  `CONTRIBUTING.md` (DCO sign-off), `SECURITY.md`, `CODE_OF_CONDUCT.md`, this
  changelog, a `Makefile` (`install`, `test`, `lint`, `build`, `clean`), a
  GitHub Actions CI workflow (Python 3.10-3.13 matrix plus a ruff lint job),
  issue forms and a pull-request template.

### Security

- No telemetry, no cloud component and no upload path exist; recordings are
  written only where the caller asks. EDF patient identification defaults to
  the anonymised value `X` unless the caller explicitly supplies a subject ID.
- Vulnerability reporting and the health-data privacy policy are documented in
  [SECURITY.md](SECURITY.md).

### Known gaps

These are deliberate, documented limitations of `0.1.0` - not a backlog of
promises:

- **No hardware testing.** The SDK has never been run against a BroadMind
  headset. The serial transport is written but unverified; protocol
  compatibility is an assumption.
- **Bluetooth LE is not implemented.** The BroadMind BLE protocol is not
  public. `--transport ble` fails with an explicit error instead of pretending.
- **Seven of eight bindings are not authored.** Only Python exists
  BroadMind-side. C++, MATLAB, R, Julia, Java, C#, Rust and TypeScript users
  can reach the data path through BrainFlow directly, but they get none of the
  BroadMind montage, artefact-label or export helpers.
- **Artefact thresholds are synthetic-tuned heuristics** and are not validated
  on real, expert-labelled recordings.
- **The gain-5 uV conversion is an unverified assumption.** It matches the
  published OpenBCI gain-24 scale factor at gain 24; gain 5 assumes a 4.5 V,
  24-bit ADC reference.
- **EEGLAB/MATLAB import is untested**; EDF+ loading was verified in MNE
  1.13.2 only.
- **No real EEG data is included.** The repository ships synthetic signals
  only, and no participant recordings.
- CI has been added but has not yet run on GitHub (the repository is not
  published).

[Unreleased]: https://github.com/BROADMIND-BCI/broadmind-eeg/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/BROADMIND-BCI/broadmind-eeg/releases/tag/v0.1.0
