# Pull request

## Summary

<!-- What does this change and why? Link related issues, e.g. "Closes #12". -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation
- [ ] CI / tooling / packaging
- [ ] Hardware-dependent change (fixture attached - see below)

## How was this tested?

<!--
Commands you ran and what you observed. If hardware was involved, say exactly
which device, firmware and transport, and attach the fixture that CI can replay.
-->

```bash
python -m pytest tests/ -q
ruff check .
```

## Checklist

- [ ] I have read [CONTRIBUTING.md](CONTRIBUTING.md) and agree to the
      [Code of Conduct](CODE_OF_CONDUCT.md).
- [ ] `python -m pytest tests/ -q` passes locally.
- [ ] `ruff check .` passes locally.
- [ ] New or changed behaviour has a test, and the test fails without the change.
- [ ] **No hardware claims are introduced without a fixture.** Any behaviour
      that depends on a real headset is backed by a checked-in, de-identified
      fixture under `tests/fixtures/` (raw capture, EDF/CSV export or session
      dump) and a test that replays it - not by a screenshot or "works on my
      machine".
- [ ] Hardware claims that cannot be backed by a fixture are labelled
      **unverified** in the code, docs and `CHANGELOG.md`.
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]` for user-visible changes.
- [ ] `README.md` / `PROVENANCE.md` / `docs/` are updated where the behaviour
      or scope changed.
- [ ] No participant data, credentials or proprietary firmware/protocol
      material is included.
- [ ] No clinical or diagnostic claims are introduced.
- [ ] Every commit is signed off (`git commit -s`, Developer Certificate of
      Origin 1.1).

## Notes for reviewers

<!-- Anything subtle: API changes, follow-up work, known limitations. -->
