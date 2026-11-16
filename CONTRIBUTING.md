# Contributing to broadmind-eeg

Thanks for considering a contribution. This is a small, deliberately honest SDK:
we would rather ship one well-tested Python binding and say so than claim more
than the code does. Contributions that keep it that way are welcome.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Scope

In scope:

* bug fixes and tests for the existing modules (`broadmind/`),
* documentation corrections - especially anything that overclaims,
* hardware-backed fixes **when accompanied by a fixture** (see below),
* additional analysis that is pure, documented and testable.

Out of scope for this repository:

* clinical or diagnostic features, or any claim that the SDK interprets EEG,
* cloud services, telemetry or uploads,
* the proprietary BroadMind firmware/BLE protocol (not public),
* vendoring third-party code - use a dependency instead.

## Development setup

Requires Python 3.10+.

```bash
git clone https://github.com/BROADMIND-BCI/broadmind-eeg.git
cd broadmind-eeg

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e '.[dev]'
```

`pip install -e '.[dev]'` installs the runtime dependencies (`numpy`,
`brainflow`, `edfio`) plus `pytest` and `ruff`.

If you prefer not to activate the venv, point the Makefile at its interpreter:

```bash
make install PYTHON=.venv/bin/python
make test    PYTHON=.venv/bin/python
```

## Running the tests

```bash
python -m pytest tests/ -q          # or: make test
```

The suite needs **no hardware**: acquisition runs against BrainFlow's
`SYNTHETIC_BOARD` and every signal-processing test generates its own data.
Every test must pass (the initial suite has 14). A pull request that leaves the
suite red will not be merged.

You can also run the examples, which are hardware-free:

```bash
python examples/01_stream.py
python examples/02_band_power.py
python examples/03_record_edf.py
python examples/04_artifacts.py
```

## Coding style

* Formatting and linting: [ruff](https://docs.astral.sh/ruff/), line length
  100, target Python 3.10 (configured in `pyproject.toml`). Run `make lint`.
* Type hints on public functions; `from __future__ import annotations`.
* Every module docstring starts with **"What this module does"** and
  **"What this module deliberately does NOT do"**. Keep both sections true -
  the second one is the point of this project.
* Prefer the standard library and NumPy. Adding a runtime dependency is a
  design decision that needs a justification in the pull request and an entry
  in `PROVENANCE.md`.
* New public API goes in the module's `__all__` and, where it matters, in the
  README's "What's in the box" table.
* No clinical, diagnostic or cognitive claims in code, docs or commit
  messages.

## Tests

* Every behaviour change needs a test. Bug fixes should first add a test that
  fails, then the fix.
* Tests must run offline, without hardware, in a few seconds, and must not
  depend on the wall clock or on ordering between tests.
* Do not weaken an existing assertion to make a change pass; if an assertion
  is wrong, explain why in the pull request.

### Hardware-dependent contributions need a fixture, not a screenshot

If your change depends on a real headset, a Bluetooth adapter or any behaviour
that CI cannot reproduce, include a **checked-in fixture** that exercises it:

* raw packet bytes captured from the device (for `broadmind.protocol`),
* a short EDF/CSV export produced by the device path,
* a recorded BrainFlow session dump.

Place fixtures under `tests/fixtures/`, keep them small and de-identified (no
names, dates of birth, or other participant identifiers), add a test that reads
the fixture, and state in the pull request exactly how the capture was made
(device, firmware, OS, capture command).

Recording formats (`.edf`, `.bdf`, `.fif`) and the `examples/` output patterns
are gitignored by default so a participant recording cannot be committed by
accident. If a de-identified fixture must use one of those formats, add it
explicitly:

```bash
git add -f tests/fixtures/your-capture.edf
```

A screenshot or an unsupported "works on my machine" statement is not
evidence and will be asked to become a fixture. If a capture cannot be shared,
describe the limitation explicitly in the pull request and keep the claim
labelled **unverified** in the docs.

## Commit sign-off (DCO)

This project uses the [Developer Certificate of Origin 1.1](https://developercertificate.org/)
instead of a CLA. Sign off every commit:

```bash
git commit -s -m "fix: ..."
```

which appends:

```text
Signed-off-by: Your Name <you@example.com>
```

By signing off you certify that you wrote the contribution or otherwise have
the right to submit it under this project's MIT licence. Commits without a
sign-off may be asked to amend.

## Pull request checklist

* [ ] `python -m pytest tests/ -q` passes.
* [ ] `ruff check .` passes.
* [ ] New or changed behaviour is covered by a test.
* [ ] Hardware-dependent behaviour is backed by a fixture, or is documented as
      unverified.
* [ ] Docs (`README.md`, `PROVENANCE.md`, `CHANGELOG.md`) are updated when
      behaviour changes.
* [ ] `CHANGELOG.md` has an entry under `[Unreleased]` for user-visible
      changes.
* [ ] No participant data, credentials or proprietary firmware/protocol
      material is included.
* [ ] No clinical or diagnostic claims are introduced.

## Reporting bugs and requesting features

Use the issue forms in `.github/ISSUE_TEMPLATE/`. For anything security
related, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
