# Wireless: which path to a multi-language client?

*Decision note, 16 November 2026. Companion to `wireless.md` (the BLE transport design).*

---

## 1. The question

The product wireless link is our own BLE 5 transport. Every other BrainFlow language therefore does not know how to talk to the device. Two options were proposed:

**A.** Write a driver that emulates **BLE as a USB/serial connection** and upstream it.
**B.** Rewrite BrainFlow in the **two most popular languages** so they can connect over BLE directly.

There is a third, and the evidence points at it.

---

## 2. The fact that decides it

**BrainFlow already ships BLE.** Five board IDs in the installed package are BLE-native:

| Board | ID |
|---|---|
| `BRAINBIT_BLED_BOARD` | 18 |
| `MUSE_S_BLED_BOARD` | 21 |
| `MUSE_2_BLED_BOARD` | 22 |
| `MUSE_2016_BLED_BOARD` | 42 |
| `GANGLION_NATIVE_BOARD` | 46 |

BrainFlow's core is C++ and already contains a cross-platform BLE path — it has to, to support the Ganglion natively. So **option B's premise is wrong**: there is nothing to "rewrite in two languages". Direct BLE support in a new language is not a client you write; it is a *board* you register in the C++ core, after which all eight bindings get it for free.

Rewriting or forking BrainFlow per language would mean maintaining forks against moving upstream to duplicate a capability that already exists. That is the worst option on every axis, and it should be dropped.

---

## 3. The two real options

### Option A — BLE-as-virtual-serial bridge

A small local daemon connects to the device over BLE and exposes the frame stream on a **PTY** (macOS/Linux) or **virtual COM port** (Windows). BrainFlow's existing `CYTON_BOARD` then connects to that serial port, unmodified.

**What it buys:** all eight languages immediately, with zero BrainFlow changes, because the bridge speaks the protocol they already understand.

**What it actually costs** — the part that is easy to miss:

1. **You must implement the Cyton command surface, not just the data stream.** BrainFlow's serial board sends configuration commands and expects responses. A bridge that only forwards samples will connect and then return nothing useful. So option A quietly re-implements the legacy OpenBCI protocol *in the bridge* — exactly the thing we rejected for the firmware, relocated to a daemon.
2. **Windows requires a virtual COM driver.** On macOS/Linux a PTY is a day's work. On Windows there is no supported user-space equivalent — it means a signed kernel driver, an installer, and administrator rights. For a consumer device that is a dead end.
3. **Install burden:** the user must run a background daemon before pressing "record". Labs will tolerate it; consumers will not.
4. **Timing:** a PTY is not a real UART; scheduling jitter is added on both hops. Sample numbers preserve alignment, but it must be measured before anyone claims real-time performance.

### Option C — upstream a BROADMIND board (recommended)

Implement the board once, in C++, against BrainFlow's existing BLE path. Register a `BROADMIND_BOARD` id and open a PR.

**Why it wins:**

- **One implementation, eight languages.** No per-language clients, no forks, no bridge for the user to install.
- **It is how the ecosystem already works.** PiEEG did exactly this (`PIEEG_BOARD` = 56) and got a SupportedBoards listing — free distribution to the audience we want.
- **The work is bounded:** the BLE stack exists upstream; what we add is our GATT service implementation and the board registration.
- **First-party.** `pip install brainflow` reaches a researcher in one line.

**What it costs:** it cannot start until our GATT protocol is stable and hardware exists — realistically after P1. And BrainFlow may have opinions about marrying a proprietary service into their core; the answer is to publish the protocol (already planned) so anyone *could* implement it.

---

## 4. Comparison

| | A · virtual serial bridge | B · rewrite per language | C · upstream board |
|---|---|---|---|
| Languages covered | 8 (via BrainFlow) | 2 | 8 |
| New code | Bridge daemon **+ Cyton command emulation** | 2 clients, forever | One C++ board |
| User installs | A daemon (and a driver on Windows) | The forked library | Nothing |
| Windows | Blocked without a signed driver | Works | Works |
| Maintenance | Ours, against an emulated protocol | Two forks to rebase | Shared with upstream |
| Timing risk | Two added hops | Lowest | Low |
| Reaches the research audience | Indirectly | No | Yes — SupportedBoards |
| Verdict | Useful fallback | **Reject** | **Target state** |

---

## 5. What to do instead, in order

**1. Ship the Python `BleTransport` first** (already the plan). It is the product path and it needs no one's permission.

**2. Add an LSL outlet to it — this is the cheap multi-language unlock.** LSL has client libraries for C++, C#, Java, Python, MATLAB/Octave and more, plus LabRecorder and every lab tool that matters. A researcher on MATLAB does not need a BrainFlow board; they need the stream to appear in LabRecorder. One outlet covers "the two most popular languages" and five others, costs days, and is standard practice in the field.

**3. Offer the virtual-serial bridge as an opt-in for BrainFlow users on macOS/Linux only.** Cheap to build once the BLE reader exists (it is a re-framing shim), and explicitly labelled: *not supported on Windows*. Accept that it drags in Cyton command emulation and budget for it, or scope it to data-only and document the limitation.

**4. Upstream the board (option C) once the protocol freezes.** That is the durable answer and the one that removes the bridge entirely.

### Why not just do A now?

Because A's real cost is not the PTY — it is the protocol emulation plus a Windows story, and it delivers the same coverage that LSL delivers in a fraction of the time. A is a *convenience for BrainFlow users*, not a path to multi-language support.

---

## 6. Spike, if you want the bridge anyway

Two days, timeboxed, answering:

1. Does BrainFlow's `CYTON_BOARD` accept a PTY on macOS without a handshake it cannot get? (Connect it to the `examples/05_frame_loopback.py` frame stream behind a PTY.)
2. How much of the command surface does it actually require before it starts streaming? Record the exact bytes.
3. Measured jitter through two hops versus direct serial.

If (1) fails or (2) is large, the bridge is not worth its maintenance. Ship LSL and wait for the board.

---

## 7. One line

**Do not fork BrainFlow — it already has BLE. Do not build a virtual-serial shim first — its real cost is the legacy protocol it forces you to re-implement. Ship BLE + LSL for immediate multi-language reach, then upstream the board so nobody has to install anything.**
