#!/usr/bin/env python3
"""A fake ESP32 bridge: stream OpenBCI-compatible frames over UDP.

Purpose
-------
The kit is stated to speak the same firmware protocol as an OpenBCI board, and
the wireless plan is an ESP32 bridge feeding BrainFlow's ``CYTON_WIFI_BOARD``.
That firmware cannot be written against a real headset in a loop, so this script
is the other end of the wire: it emits a continuous stream of valid 33-byte
sample packets over UDP, at 250 Hz, so firmware and client work can proceed
before a unit is on the bench.

Two ways to use it
------------------
1. **As a parser target** (works today, no hardware): run the server, then point
   :class:`broadmind.protocol.PacketParser` at the socket. The frames it emits
   are the ones the SDK already decodes.

       python examples/05_wifi_loopback.py --port 9000
       # in another shell:
       python examples/05_wifi_loopback.py --self-test

2. **As a BrainFlow target** (once firmware is closer): BrainFlow's
   ``CYTON_WIFI_BOARD`` also expects a command channel towards the shield, which
   this script does not implement. Treat it as a *data-plane* reference, not a
   device emulator. See ``docs/esp32.md`` for what the firmware must add.

Safety
------
It binds to loopback by default and refuses to broadcast. Nothing here claims to
be a BROADMIND device; it is a development fixture.
"""
from __future__ import annotations

import argparse
import math
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from broadmind.protocol import PacketParser, encode_packet  # noqa: E402

SAMPLE_RATE_HZ = 250
#: 10 Hz alpha-ish tone so a decoder has something to find, in ADC counts.
AMPLITUDE_COUNTS = 20_000


def frame_at(sample_index: int) -> bytes:
    """Build one 33-byte packet for a given sample index."""
    phase = 2 * math.pi * 10.0 * (sample_index / SAMPLE_RATE_HZ)
    value = int(AMPLITUDE_COUNTS * math.sin(phase))
    # Channels alternate in phase so the eight traces are visibly distinct.
    channels = [value if i % 2 == 0 else -value for i in range(8)]
    return encode_packet(sample_index & 0xFF, channels)


def run_server(host: str, port: int, rate: float, duration: float | None) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    peers: set[tuple[str, int]] = set()

    print(f"fake ESP32 bridge streaming {rate:g} Hz to {host}:{port}", flush=True)
    print("  waiting for a client to send one byte so we know where to reply…", flush=True)
    # Non-blocking: a blocking recv here would throttle the send loop to one
    # frame per timeout, which is exactly the bug this fixture exists to catch
    # in firmware.
    sock.setblocking(False)

    started = time.monotonic()
    index = 0
    next_tick = started
    while duration is None or (time.monotonic() - started) < duration:
        try:
            while True:
                _data, addr = sock.recvfrom(64)
                if addr not in peers:
                    peers.add(addr)
                    print(f"  client registered: {addr[0]}:{addr[1]}", flush=True)
        except BlockingIOError:
            pass
        except KeyboardInterrupt:
            break

        now = time.monotonic()
        if now < next_tick:
            time.sleep(min(next_tick - now, 0.02))
            continue
        next_tick += 1.0 / rate

        packet = frame_at(index)
        for peer in list(peers):
            try:
                sock.sendto(packet, peer)
            except OSError:
                peers.discard(peer)
        index += 1

        if index % int(rate * 10) == 0:
            print(f"  {index} samples sent ({index / rate:.1f} s)", flush=True)

    sock.close()
    print(f"stopped after {index} samples", flush=True)
    return 0


def run_self_test(host: str, port: int, seconds: float) -> int:
    """Client side: receive frames over UDP and decode them with the SDK codec."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    sock.sendto(b"?", (host, port))  # register with the server

    parser = PacketParser()
    samples = 0
    first_uv: tuple[float, ...] | None = None
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        try:
            chunk, _ = sock.recvfrom(4096)
        except socket.timeout:
            print("  no data — is the server running on this host/port?")
            return 1
        for sample in parser.feed(chunk):
            if first_uv is None:
                first_uv = sample.to_uv()
            samples += 1

    sock.close()
    print(f"decoded {samples} samples in {seconds:g}s (~{samples / seconds:.0f} Hz)")
    if first_uv:
        print(f"first sample in µV: {[round(v, 2) for v in first_uv]}")
    ok = samples > 0
    print("PASS: frames decode through the SDK codec" if ok else "FAIL: nothing decoded")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1", help="bind/connect address (default loopback)")
    ap.add_argument("--port", type=int, default=9000, help="UDP port (default 9000)")
    ap.add_argument("--rate", type=float, default=SAMPLE_RATE_HZ, help="frames per second")
    ap.add_argument("--duration", type=float, default=None, help="server run time in seconds")
    ap.add_argument("--self-test", action="store_true", help="client mode: decode what is sent")
    ap.add_argument("--seconds", type=float, default=2.0, help="self-test duration")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test(args.host, args.port, args.seconds)
    return run_server(args.host, args.port, args.rate, args.duration)


if __name__ == "__main__":
    raise SystemExit(main())
