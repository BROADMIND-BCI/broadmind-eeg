# Wireless transport — design

*Written 16 November 2026. Supersedes an earlier draft that proposed adopting the legacy OpenBCI WiFi-shield protocol; that approach has been rejected.*

---

## 1. The decision

**Build the wireless link ourselves: a purpose-built BLE transport, battery-powered, designed for fast connection.**

Rejected alternative: present the device to BrainFlow as `CYTON_WIFI_BOARD` and forward the byte stream over UDP. It was the cheaper first suggestion — zero client work, all eight BrainFlow languages — but it is the wrong product decision and it has been dropped:

- It inherits an ESP8266-era protocol design, including its command surface.
- WiFi on a head-worn, battery-powered device costs power the BLE path does not.
- Connection setup on the shield protocol is slow and awkward for a consumer device.
- It couples the product's roadmap to another project's maintenance.

The SDK no longer exposes a `wifi` transport. What remains is:

| Transport | Status |
|---|---|
| `synthetic` | Verified (BrainFlow synthetic board, no hardware) |
| `serial` | OpenBCI-Cyton compatible, vendor-confirmed; the **bench and validation** path |
| `ble` | **Not implemented** — this is the product path and it is being built from scratch |

---

## 2. What the link has to carry

The numbers are small enough that this is a protocol-design problem, not a bandwidth problem:

```
8 channels x 250 Hz x 24 bit  = 6,000 B/s of sample data
+ 33-byte framing overhead    ≈ 8,250 B/s on the wire
```

That is roughly **66 kbit/s**. BLE 5 at 2M PHY with Data Length Extension gives an application throughput in the region of 1 Mbps — so a full-rate stream uses a low single-digit percentage of the link. Even BLE 4.2 would carry it.

**So do not optimise for bandwidth. Optimise for latency stability, loss detection and connection time.** Those are the things that break BCI recordings, and none of them is fixed by more throughput.

---

## 3. Design

### 3.1 Keep the frame, change the pipe

The 33-byte OpenBCI-compatible sample frame already implemented in `broadmind/protocol.py` stays exactly as it is. Only the transport under it changes.

Why this matters: the decoder, the resynchronising parser and the fixture tests all keep working, and the serial and BLE paths produce **byte-identical sample records**. A study can state which transport it used and the data stays comparable.

### 3.2 GATT service shape

Three characteristics — no more until something forces one:

| Characteristic | Direction | Purpose |
|---|---|---|
| **Control** | write + indicate | start/stop streaming, set sample rate and gain, request battery level, read firmware and protocol version |
| **Data** | notify | sample frames, batched by elapsed time |
| **Status** | read + notify | battery, dropped-frame counter, connection parameters in force |

Batching rule: **pack by time, not by count.** At 250 Hz one sample is 4 ms; batch into roughly 10–20 ms windows (3–5 samples) and send. A fixed sample count silently changes latency whenever the rate changes.

Every notification carries a **sequence number** so the host can distinguish "the radio dropped a packet" from "the device skipped samples". Without that, a lossy link and a timing fault look identical in the recording — and only one of them is a software bug.

### 3.3 Connection parameters

| Parameter | Target | Why |
|---|---|---|
| Connection interval | 7.5–15 ms while streaming | Lowest interval the spec allows keeps round-trip predictable; raise when idle to save power |
| Peripheral latency | 0 while streaming | Latency > 0 lets the device skip connection events, which shows up as jitter |
| ATT MTU | 247+ (negotiated) | Fewer, larger notifications |
| Data Length Extension | on | ~251-byte LL payload |
| PHY | 2M preferred, 1M fallback | Throughput and airtime; keep 1M as the compatibility floor |
| Advertising | fast interval at boot only | "Fast connection" is mostly a *discovery* problem, not a link problem |

**BLE 5 coded PHY** (long range) is worth supporting as a fallback for lab environments with interference, at the cost of throughput — the payload needs 66 kbit/s, so coded PHY's reduced rate is still sufficient.

### 3.4 Power

Battery-only operation is a hard constraint, not a preference: the hardware has **no galvanic isolation** (`docs/safety.md`). Consequences for the radio:

- Radio idle when not streaming; disconnect after a short idle timeout rather than holding the link.
- Advertising in a low-duty pattern once the initial fast window closes.
- Measure current with the radio idle, advertising and streaming separately — and **measure the EEG noise floor in each of those states**. A quiet amplifier on a noisy rail is a specification that dies in review.

---

## 4. Client side

Implement `BleTransport` with **`bleak`** (cross-platform: macOS, Windows, Linux, Android), satisfying the same interface as the serial path so `Stream`, `record`, the exporters and the CLI are untouched.

```python
# target shape, not implemented
config = make_board_config("ble", mac_address="AA:BB:CC:DD:EE:FF")
with open_stream(config) as stream:
    block = stream.collect(seconds=10)
```

Keep the transport behind the existing `Transport` enum so nothing else in the package needs to know which pipe is in use.

**Test seam:** a fake transport that replays a recorded frame stream is the only way to test `Stream` without a radio. Building that fake is a prerequisite, not an afterthought — it is what keeps the BLE work from becoming untestable until hardware exists.

---

## 5. What this costs us, stated honestly

Adopting a custom BLE transport **gives up the "eight language bindings for free" argument for the wireless path**. BrainFlow has no board for our device; every other language would need its own BLE client.

Three ways to get it back, in order of preference — the full comparison is in
[`wireless-driver-options.md`](wireless-driver-options.md):

1. **LSL outlet first.** Cheap, days not weeks, and it reaches C++, C#, Java, Python and MATLAB/Octave at once through liblsl plus LabRecorder. This is the practical multi-language unlock and it belongs with the product, not after it.
2. **Upstream a BROADMIND board to BrainFlow** once the GATT protocol is stable. BrainFlow already ships five BLE-native boards, so this is a board registration in its C++ core, not a new client per language. PiEEG did exactly this (`PIEEG_BOARD` = 56) and got a SupportedBoards listing.
3. **Accept Python-first** for wireless and say so on the site.

**Rejected:** rewriting or forking BrainFlow per language. Its BLE capability already exists; a fork would duplicate it and cost maintenance forever. A BLE→virtual-serial bridge is a useful convenience for BrainFlow users on macOS/Linux only — and its real cost is emulating the OpenBCI command surface, so it is not the cheap route it looks like.

The serial path is unaffected: it remains OpenBCI-compatible and keeps the BrainFlow mapping.

---

## 6. Sequencing

The wireless work must not run ahead of the bench work. Order:

1. **Serial capture first** (roadmap R1). Timing on a wire is not in question, so it is where µV verification and the artefact detectors get settled.
2. **Fake transport + framing tests** — the codec is already proven; prove the session layer against a replay.
3. **BLE firmware**: advertising, GATT, control, then data.
4. **Soak test**: 30 minutes continuous at full rate — measure throughput, loss, latency and reconnection.
5. **Only then** characterise EEG quality with the radio active vs idle.
6. Then consider the BrainFlow upstream.

**Do not run the publication benchmark over BLE.** Validate on serial, demonstrate wireless separately. A study that fails because of a dropped notification is a study that cannot be cited.

---

## 7. Open questions for firmware

- Which BLE stack — ESP32 (NimBLE) or a dedicated radio? ESP32 is the current candidate platform; NimBLE is the lighter stack.
- What is the maximum sustained notification rate on the chosen stack, measured rather than quoted?
- Can the device batch in hardware (DMA → notification) or does the application layer do the packing?
- What is the reconnection policy after a link drop mid-recording — resume the sample counter or start a new session? (Recommendation: never resume; start a new session and mark the gap.)
- Is there a physical pairing button, or does the device accept its first bond automatically? For lab use, a button is worth the BOM cost.

---

## 8. Development fixture

`examples/05_frame_loopback.py` streams valid sample frames over a datagram socket. It is **transport-agnostic on purpose**: it exercises the framing and the decoder, and UDP is simply the easiest carrier to run locally. The same frames will be the BLE notification payload.

```bash
python examples/05_frame_loopback.py --port 9000            # serve
python examples/05_frame_loopback.py --port 9000 --self-test # decode
```

Measured: **501 samples in 2 s (~250 Hz)**, decoding through the SDK codec. A bug found while building it is worth carrying into firmware: a blocking `recv` in the registration path throttled the send loop to **2 frames per second**. The same trap exists in any BLE connect-then-stream state machine.
