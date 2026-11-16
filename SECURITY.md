# Security policy

`broadmind-eeg` is a client-side Python SDK for the BROADMIND 8-channel
dry-electrode EEG headset. It talks to a device and it writes health-adjacent
data to disk, so we treat both memory-safety-style bugs and privacy bugs as
security issues.

## Supported versions

| Version | Supported |
|---|---|
| `0.1.x` (current) | Yes |
| `< 0.1.0` | No - pre-release only |

## Reporting a vulnerability privately

**Please do not open a public GitHub issue for a suspected vulnerability.**

Email **security@broadmind.me** with:

* affected version (`python -c "import broadmind; print(broadmind.__version__)"`),
* Python version, operating system, and BrainFlow version,
* transport involved (`synthetic`, `serial` or `ble`), and whether real
  hardware was used,
* a minimal reproduction or proof of concept,
* the impact you believe it has, and whether any recording or participant data
  is exposed.

If you would like to encrypt your report, say so in a first message with no
details and we will arrange a channel. Please do not include live participant
data; a synthetic or de-identified sample is enough to demonstrate a bug.

### What to expect

* We aim to acknowledge reports within **3 business days**.
* We aim to give an initial assessment (valid / invalid, severity, planned fix)
  within **10 business days**.
* We will keep you informed until a fix is released, and credit you in the
  release notes unless you ask us not to.
* We aim to publish a coordinated fix within **90 days** of acknowledgement.
  If a fix takes longer we will say so and agree a disclosure date with you.

## Scope

In scope - code in this repository:

* `broadmind.board` - BrainFlow configuration, serial/Bluetooth enumeration,
  transport status reporting.
* `broadmind.stream` - sessions, ring buffer, callback API, markers.
* `broadmind.protocol` - the OpenBCI-compatible 33-byte codec and parser
  (parser desynchronisation, out-of-bounds reads, pathological input).
* `broadmind.export` - CSV/EDF+/JSON writers and readers (path handling,
  malformed-file parsing, metadata/identifier leakage).
* `broadmind.features`, `broadmind.artifacts`, `broadmind.montage` - numerical
  correctness and resource-exhaustion bugs.
* `broadmind.cli` and the `broadmind` console script - argument handling, file
  overwrite behaviour, unsafe defaults.
* Packaging and supply chain: dependency confusion, malicious releases,
  workflow permissions in this repository.

Out of scope:

* **The headset firmware and hardware.** Firmware, the proprietary USB/BLE
  wire protocol, and the device itself are not part of this repository; report
  those to BROADMIND through the same address and they will be routed
  internally.
* **BrainFlow itself.** Bugs in the acquisition core belong upstream:
  <https://github.com/brainflow-dev/brainflow/security>. Report them there;
  tell us only if this SDK's use of BrainFlow makes them worse.
* **edfio, NumPy and other third-party dependencies.** Report to the upstream
  project. We will still take a report if the way this SDK calls a dependency
  creates the issue.
* Physical safety, electrode/skin safety, and clinical interpretation. This SDK
  is not a medical device (see the README). Clinical or diagnostic misuse is
  not a vulnerability in this codebase.
* Issues that require an already-compromised machine, or that are purely
  social engineering against BROADMIND staff.

## Privacy is security here

EEG is health-adjacent data. The following are treated as security issues in
scope for this repository, not merely privacy nits:

* data written to an unexpected location, or left world-readable,
* identifiers (names, dates of birth, subject IDs, paths embedding a username)
  leaking into EDF/CSV/JSON output or into logs,
* unexpected network activity, telemetry, or any upload path,
* artefact labels or markers being silently dropped or altered during export,
* any feature that would make it harder to delete a recording.

The SDK is local-only by design: it never uploads, phones home, collects
telemetry or trains on user data. A contribution that adds network access
without an explicit, reviewed design change should be treated as suspicious.

## Safe harbour

We will not pursue or support legal action against researchers who act in good
faith under this policy: test only against your own installation and data,
avoid privacy violations and data destruction, report promptly, and give us a
reasonable opportunity to fix the issue before public disclosure.

## Disclosure

Once a fix is available we will publish a GitHub security advisory, credit the
reporter (with permission), and note the fix in `CHANGELOG.md` under
`### Security`. Please give us the 90-day window above before publishing
details yourself.
