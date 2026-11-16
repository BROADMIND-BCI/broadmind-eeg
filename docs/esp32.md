# ESP32 wireless bridge — integration design

*Decision record and firmware brief. Updated 16 November 2026 after the vendor confirmed that the kit's firmware protocol is the same as an OpenBCI board.*

---

## 1. The decision

**Do not invent a wireless protocol. Make the ESP32 bridge present the device to BrainFlow as an OpenBCI WiFi shield.**

Concretely: firmware reads the same byte stream the kit already produces, and puts it on UDP. On the host side the SDK opens:

```
board_id = BoardIds.CYTON_WIFI_BOARD          # 5
params.ip_address  = "192.168.4.1"            # or a LAN address in STA mode
params.ip_port     = 9000                     # BrainFlow's expectation
params.ip_protocol = 0                        # UDP
```

Already implemented and tested in `broadmind/board.py`:

```python
from broadmind.board import make_board_config
from broadmind.stream import open_stream            # or Stream(config=...)

config = make_board_config("wifi", ip_address="192.168.4.1")
with open_stream(config) as stream:
    block = stream.collect(seconds=2)
```

and on the command line:

```bash
broadmind record --seconds 30 --transport wifi --ip 192.168.4.1 --out session.edf
```

**Why this and not a proprietary protocol:** every BrainFlow binding — Python, C++, Java, C#, Julia, MATLAB, R, Rust — gains support at the same instant, with no BROADMIND client code in any of them. There are 67 board IDs in the installed BrainFlow and `CYTON_WIFI_BOARD` is one of them. Inventing a protocol would mean re-implementing the client eight times for no benefit.

**Verification status:** the configuration is written and unit-tested; the firmware does not exist yet, and nothing here has been run against a BroadMind board. The transport status string says exactly that, and a unit test enforces that it keeps saying it.

---

## 2. What the firmware has to do

Five responsibilities, in dependency order.

### 2.1 Preserve the existing byte stream
The kit already emits OpenBCI-compatible 33-byte sample frames (`broadmind/protocol.py` decodes them; `docs/protocol.md` documents the layout). The bridge must **forward those bytes unmodified**. No re-framing, no re-scaling, no re-numbering.

### 2.2 Send over UDP, one frame per datagram
250 Hz × 33 bytes ≈ **8.25 kB/s**. Bandwidth is not the problem and never will be. Send one packet per datagram; do not batch to "save bandwidth". Batching makes burst coalescing worse, and a receiver that has to split datagrams after the fact is a receiver that has given up on jitter.

### 2.3 Keep the device sample counter as the only clock
BrainFlow derives timestamps from the packet sample number, not from arrival time. So:

- **Never re-stamp** frames in firmware.
- **Never buffer-and-replay.** A replay of stale samples looks like a network burst and silently destroys timestamp alignment — the data *looks* valid and is wrong. This is the single most dangerous failure mode in the whole design.
- On overflow, **drop the newest frames and increment a counter**. Dropped data that is reported beats perfect-looking data that is a lie.

### 2.4 Expose the command channel BrainFlow expects
`CYTON_WIFI_BOARD` is not a one-way stream: BrainFlow configures the shield, then starts and stops streaming. That command surface must be replicated.

> **Before firmware work starts:** read BrainFlow's `WifiBoard` source and confirm the exact command protocol, default port and start/stop semantics. The SDK defaults to port 9000 and UDP, which is BrainFlow's documented expectation, but **confirm it from BrainFlow's code rather than from this document** — a wrong command channel presents as "the board opens then returns no data", which is one of the most expensive bugs to debug from the other end.

### 2.5 Choose a WiFi mode on purpose

| Mode | Use | Trade-off |
|---|---|---|
| **SoftAP** — the kit creates its own network | Field use, demos, clinical | Always reachable at a fixed IP, e.g. `192.168.4.1`; one device per host; awkward with two kits |
| **STA** — kit joins a lab network | Multi-device recording, LabRecorder on a workstation | Needs credentials; DHCP address discovery is a real UX problem; mDNS helps |
| **USB serial** — the existing path | Bench, CI, anything with a cable | No wireless, and that is fine |

Recommendation: **SoftAP first** (it is predictable and needs no network admin), STA second. Do not build both before the data path is proven.

---

## 3. Hardware and firmware notes

- **Power.** WiFi transmit peaks are the largest current draw on the board. Measure the kit's battery life with the radio on before quoting any number; EEG amplifiers are sensitive to supply noise, so check the noise floor with WiFi active *and* idle. A quiet amp on a noisy rail is a specification that dies in review.
- **Antenna placement.** Keep the antenna clear of the electrode harness; a hand over an ESP32 antenna is a 15 dB problem.
- **Latency budget.** For closed-loop BCI, end-to-end latency should stay well under 100 ms. WiFi adds single-digit milliseconds on a quiet network — but only if the firmware does not batch. Measure it, do not assume it.
- **Fallback.** If the ESP32 cannot reach the timing required, the answer is USB serial, not a more complicated wireless stack.

---

## 4. Development fixture — exists and passes

`examples/05_wifi_loopback.py` is a fake bridge: it streams valid 33-byte frames over UDP at 250 Hz so client and parser work can proceed before firmware is on the bench.

```bash
# terminal 1 — the fake bridge
python examples/05_wifi_loopback.py --port 9000

# terminal 2 — decode it with the SDK codec
python examples/05_wifi_loopback.py --port 9000 --self-test --seconds 2
```

Measured output:

```
decoded 501 samples in 2s (~250 Hz)
first sample in µV: [-789.86, 789.86, -789.86, 789.86, ...]
PASS: frames decode through the SDK codec
```

A bug found while building this is worth carrying into the firmware brief: the first version used a blocking `recv` and delivered **2 frames per second instead of 250**. The socket call that was supposed to detect a client throttled the send loop. Firmware has the same trap available in its accept/registration path.

**Scope limit:** this fixture is the *data plane only*. It does not implement BrainFlow's command channel, so it will not make `CYTON_WIFI_BOARD` open a successful session on its own. Say so when handing it to anyone — an emulator that half-works is worse than none.

---

## 5. Integration checklist

- [ ] Read BrainFlow's `WifiBoard` implementation; record the command protocol and port in `docs/protocol.md`
- [ ] Firmware: forward the untouched 33-byte stream over UDP, one frame per datagram
- [ ] Firmware: sample-counter-only clock, drop counter exposed on the command channel
- [ ] Firmware: SoftAP at a documented address, start/stop commands
- [ ] Loopback swap: point `--self-test` at the real hardware and confirm 250 Hz sustained for 30 minutes
- [ ] `make_board_config("wifi", ...)` opens a real session; record the first successful capture as a fixture
- [ ] Measured: latency, packet loss over 30 min, noise floor with radio on vs off
- [ ] Update `TRANSPORT_STATUS["wifi"]` — only after the capture exists
- [ ] Update the site claim; "wireless" is not a feature until it is in the box

---

## 6. What would change this plan

| If | Then |
|---|---|
| BrainFlow's WiFi board turns out to require a fixed firmware revision | Evaluate a new upstream board ID instead — still better than a bespoke client |
| Power budget rules WiFi out | Use USB serial; make BLE the next candidate via a Ganglion-style GATT |
| Labs need multiple kits on one host | STA + mDNS becomes mandatory; SoftAP-only will not survive a review |
| The kit's timing jitters over UDP | Report it, and keep the serial path as the reference for any published benchmark |

**One thing not to do:** run the publication benchmark (roadmap P2) over WiFi. Validate on USB serial, where timing is not in question, then demonstrate wireless separately. A study that fails because of a dropped UDP packet is a study the company cannot cite.
