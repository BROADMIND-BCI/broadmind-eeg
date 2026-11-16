"""Optional parser for the OpenBCI-compatible 33-byte sample packet.

What this module does
---------------------
Implements the *public* OpenBCI (Cyton-class) sample packet layout: a 33-byte
frame carrying one sample of eight 24-bit channels plus six auxiliary bytes.
It provides a decoder, an encoder (for tests and for firmware round-trips), a
streaming parser that resynchronises on garbage, and ADC-count -> microvolt
conversion.

Why this exists
---------------
The SDK's live acquisition path is BrainFlow, which already parses whatever
protocol the board speaks. This module exists because:

* ``docs/protocol.md`` must document a byte layout that is *actually
  implemented*, not hand-waved;
* captures from serial sniffers can be validated offline;
* BroadMind firmware engineers get a reference codec for the
  OpenBCI-compatible 8-channel 250 Hz format.

What this module deliberately does NOT do
-----------------------------------------
* It does **not** claim that BROADMIND hardware emits this format. The
  BroadMind wire protocol is not public and is not implemented here.
* It does **not** open ports or talk to a device - BrainFlow owns transport.
* It does **not** implement the WiFi, accelerometer or "Daisy" variants, nor
  the OpenBCI USB dongle framing. Only the 33-byte sample frame.
* It does **not** interpret the signal or make clinical claims.

Derivation
----------
The layout follows OpenBCI's public documentation and the historical
``openbci-archive/OpenBCI_Python`` parser (MIT). No OpenBCI source code is
copied. See ``PROVENANCE.md`` and ``docs/protocol.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .board import adc_counts_to_uv

__all__ = [
    "PACKET_LENGTH",
    "START_BYTE",
    "STOP_BYTE",
    "CHANNELS_PER_PACKET",
    "AUX_BYTES",
    "ProtocolError",
    "Sample",
    "decode_packet",
    "encode_packet",
    "read_int24",
    "write_int24",
    "PacketParser",
    "parse_packets",
]

#: Total length of one OpenBCI-compatible sample packet, in bytes.
PACKET_LENGTH: int = 33

#: First byte of every packet.
START_BYTE: int = 0xA0

#: Last byte of every packet.
STOP_BYTE: int = 0xC0

#: Channel fields per packet.
CHANNELS_PER_PACKET: int = 8

#: Auxiliary bytes per packet (6 bytes: accelerometer or external aux data).
AUX_BYTES: int = 6

#: Byte offset of the sample number field.
SAMPLE_NUMBER_OFFSET: int = 1

#: Byte offset of the first channel field.
CHANNEL_OFFSET: int = 2

#: Byte offset of the first auxiliary byte.
AUX_OFFSET: int = CHANNEL_OFFSET + 3 * CHANNELS_PER_PACKET  # 26

#: Byte offset of the stop byte.
STOP_BYTE_OFFSET: int = AUX_OFFSET + AUX_BYTES  # 32

_MIN_INT24 = -(1 << 23)
_MAX_INT24 = (1 << 23) - 1


class ProtocolError(ValueError):
    """Raised when a byte frame is not a valid 33-byte sample packet."""


def read_int24(data: bytes, offset: int) -> int:
    """Decode a big-endian, two's-complement 24-bit signed integer."""
    if offset < 0 or offset + 3 > len(data):
        raise ProtocolError(
            f"cannot read 3 bytes at offset {offset} from {len(data)}-byte buffer"
        )
    value = (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2]
    if value & 0x800000:
        value -= 1 << 24
    return value


def write_int24(value: int) -> bytes:
    """Encode a 24-bit signed integer as three big-endian bytes."""
    value = int(value)
    if not _MIN_INT24 <= value <= _MAX_INT24:
        raise ProtocolError(
            f"value {value} does not fit a signed 24-bit field "
            f"[{_MIN_INT24}, {_MAX_INT24}]"
        )
    return (value & 0xFFFFFF).to_bytes(3, "big")


@dataclass(frozen=True)
class Sample:
    """One decoded sample packet."""

    sample_number: int
    channels: tuple[int, ...]
    aux: bytes = b"\x00" * AUX_BYTES
    raw: bytes = b""

    @property
    def n_channels(self) -> int:
        return len(self.channels)

    def to_uv(self, gain: float = 5.0) -> tuple[float, ...]:
        """Convert the raw ADC counts to microvolts for the given hardware gain.

        ``gain`` defaults to the BroadMind pre-amplification (x5). Pass ``24``
        to match an OpenBCI Cyton board running its documented default gain.
        """
        return tuple(adc_counts_to_uv(self.channels, gain=gain))

    def to_dict(self) -> dict:
        return {
            "sample_number": self.sample_number,
            "counts": list(self.channels),
            "aux": list(self.aux),
        }


def decode_packet(packet: bytes | bytearray) -> Sample:
    """Decode one 33-byte packet.

    Raises
    ------
    ProtocolError
        If the length is wrong, or the start/stop bytes are missing.
    """
    data = bytes(packet)
    if len(data) != PACKET_LENGTH:
        raise ProtocolError(
            f"packet must be exactly {PACKET_LENGTH} bytes, got {len(data)}"
        )
    if data[0] != START_BYTE:
        raise ProtocolError(
            f"bad start byte 0x{data[0]:02X}, expected 0x{START_BYTE:02X}"
        )
    if data[STOP_BYTE_OFFSET] != STOP_BYTE:
        raise ProtocolError(
            f"bad stop byte 0x{data[STOP_BYTE_OFFSET]:02X}, expected 0x{STOP_BYTE:02X}"
        )
    channels = tuple(
        read_int24(data, CHANNEL_OFFSET + 3 * i) for i in range(CHANNELS_PER_PACKET)
    )
    aux = data[AUX_OFFSET:AUX_OFFSET + AUX_BYTES]
    return Sample(
        sample_number=data[SAMPLE_NUMBER_OFFSET],
        channels=channels,
        aux=aux,
        raw=data,
    )


def encode_packet(
    sample_number: int,
    channels: Iterable[int],
    aux: bytes = b"\x00" * AUX_BYTES,
) -> bytes:
    """Encode one 33-byte packet (inverse of :func:`decode_packet`).

    ``sample_number`` is the low 8 bits of the device sample counter.
    """
    channels = tuple(int(c) for c in channels)
    if len(channels) != CHANNELS_PER_PACKET:
        raise ProtocolError(
            f"expected {CHANNELS_PER_PACKET} channel values, got {len(channels)}"
        )
    aux = bytes(aux)
    if len(aux) != AUX_BYTES:
        raise ProtocolError(f"expected {AUX_BYTES} auxiliary bytes, got {len(aux)}")
    body = bytes([START_BYTE, int(sample_number) & 0xFF])
    body += b"".join(write_int24(c) for c in channels)
    body += aux
    body += bytes([STOP_BYTE])
    return body


class PacketParser:
    """Incremental parser for a byte stream of 33-byte sample packets.

    Bytes that cannot start a valid packet are skipped one byte at a time until
    the framing lines up again, which mirrors what a serial sniffer has to do
    when it attaches mid-stream.

    Counters
    --------
    ``packets_parsed``
        Number of valid packets returned.
    ``bytes_discarded``
        Number of bytes skipped while resynchronising (a healthy stream that
        was captured mid-packet discards at most 32).
    """

    def __init__(self, max_buffer: int = 1 << 16) -> None:
        self._buffer = bytearray()
        self._max_buffer = int(max_buffer)
        self.packets_parsed = 0
        self.bytes_discarded = 0

    @property
    def buffered_bytes(self) -> int:
        """Number of bytes currently held waiting for more data."""
        return len(self._buffer)

    def reset(self) -> None:
        """Drop any buffered bytes and reset counters."""
        self._buffer.clear()
        self.packets_parsed = 0
        self.bytes_discarded = 0

    def feed(self, chunk: bytes | bytearray | memoryview) -> list[Sample]:
        """Feed raw bytes; return the samples that became complete.

        Incomplete trailing bytes stay buffered until the next call.
        """
        self._buffer.extend(bytes(chunk))
        samples: list[Sample] = []

        while True:
            start = self._buffer.find(bytes([START_BYTE]))
            if start < 0:
                # No possible packet start: everything here is garbage. Keep
                # nothing (a start byte can never span a chunk boundary).
                self.bytes_discarded += len(self._buffer)
                self._buffer.clear()
                break
            if start > 0:
                self.bytes_discarded += start
                del self._buffer[:start]
            if len(self._buffer) < PACKET_LENGTH:
                break
            candidate = bytes(self._buffer[:PACKET_LENGTH])
            if candidate[STOP_BYTE_OFFSET] != STOP_BYTE:
                # False start (0xA0 can occur inside sample data). Drop one
                # byte and look for the next start byte.
                self.bytes_discarded += 1
                del self._buffer[:1]
                continue
            samples.append(decode_packet(candidate))
            self.packets_parsed += 1
            del self._buffer[:PACKET_LENGTH]

        if len(self._buffer) > self._max_buffer:  # pragma: no cover - guard only
            excess = len(self._buffer) - self._max_buffer
            self.bytes_discarded += excess
            del self._buffer[:excess]
        return samples


def parse_packets(blob: bytes | bytearray) -> list[Sample]:
    """Parse a complete byte blob into samples, skipping unframed garbage."""
    parser = PacketParser()
    return parser.feed(blob)

