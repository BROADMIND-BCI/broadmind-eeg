# Safety, isolation and the questions this repository cannot answer

*Added 16 November 2026 after reviewing PiEEG's documentation, which carries a safety warning this project does not yet have.*

---

## Why this file exists

Every EEG device puts electrodes on someone's head. If any part of the signal path is connected to the mains — a laptop charging brick, a desktop, a monitor, a bench supply — then a fault in that supply has a path through the electrodes.

The industry answer is **galvanic isolation**: a barrier in the power or data path so that no mains-referenced circuit is continuous with the electrodes.

PiEEG's README says plainly that their board **has no isolation** and the host must run from a battery. That is honest, and it is the standard the BROADMIND kit should be held to.

---

## What is known and what is not

| Question | Status |
|---|---|
| Is the BROADMIND Research Kit galvanically isolated? | **Unknown — must be answered by the hardware team.** |
| If isolated, to what voltage, and by what method (DC-DC / optical / digital isolator)? | **Unknown.** |
| Can it be worn safely while the USB cable is connected to a mains-powered host? | **Unknown.** |
| Does the kit ship with any power source of its own? | **Unknown.** The published specification lists USB-C and BLE connectivity, not a battery. |
| Has anyone written a safety statement for this device? | **No.** Nothing on the site, in the press kit, or in this repository. |

That last row is the whole problem: the SDK is happy to acquire data from a device worn on a head, and nobody has documented whether that is safe in the configuration people will actually use.

---

## What must happen before units ship

### 1. Answer the isolation question
Get it from the schematic and the hardware engineer, in writing:

- Is there an isolation barrier between the electrode front-end and the USB/BLE interface?
- If yes: what component, what isolation voltage, and is it rated for the working voltage of a laptop supply?
- If no: the device is a **battery-only** device, and the product must ship and document accordingly.

### 2. Pick a documented operating mode

| Mode | What it requires |
|---|---|
| **Isolated** | Publish the isolation rating. This becomes a genuine advantage over cheap boards. |
| **Battery only** | No mains-powered host in the recording path, ever. Ship a power bank, warn the user, and say so before the product page sells anything. |
| **Neither** | Do not sell to anyone who will wear it. |

### 3. Put it in front of the user, not in a footer
PiEEG does this well and the pattern is worth copying:

> **⚠ Battery only.** The Raspberry Pi, the shield and everything cabled to them must run from a 5 V power bank while electrodes are on a person. Never use a wall charger or a mains-powered monitor or computer. **The board has no isolation.**

The equivalent text belongs at the top of the README and on the product page — not in a legal footer where nobody reads it.

### 4. Extend it to the software
The SDK can help rather than merely tolerate:

- Record the power source in session metadata (user-declared: `battery` / `mains` / `unknown`).
- Warn once, loudly, when a session starts with `mains` declared and no isolation is on record.
- Never claim in any doc that a session was electrically safe.

---

## What this repository will and will not say

**Will:** that the SDK records what the hardware sends, that it is not a medical device, that it makes no diagnostic claim, and that it cannot verify the electrical safety of the device it is talking to.

**Will not:** that the device is safe to wear, that it is isolated, that it is safe with a particular power supply, or that a recording is fit for clinical use. Those are hardware and regulatory statements, and the SDK is not the right place to make them.

---

## The uncomfortable comparison

A low-cost competitor states plainly that they have no isolation and that running from a battery is mandatory. That is an uncomfortable sentence to write and it is the reason a lab will trust them.

A better-specified, more expensive device that says nothing about isolation does not look safer. It looks undecided.

---

## Open tasks

- [ ] Hardware team: state isolation status in writing (roadmap **P0**, blocking)
- [ ] Add the safety blockquote to the top of `README.md` once the status is known
- [ ] Add a `SAFETY.md` at the repository root, or link this file from the README prominently
- [ ] Product page: publish the same statement before taking orders
- [ ] Press kit: one sentence so journalists get the answer from us, not from a forum
- [ ] If battery-only: confirm which power banks are acceptable and state a minimum rating
