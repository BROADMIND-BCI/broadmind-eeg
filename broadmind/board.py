"""Device and transport configuration for the BROADMIND 8-channel EEG headset.

What this module does
---------------------
* Declares the *vendor-stated* device specification (8 active dry electrodes,
  250 Hz, 24-bit, 0.02-125 Hz) in one immutable :class:`DeviceSpec`.
* Maps the device onto a concrete BrainFlow board id and transport:
  ``SYNTHETIC_BOARD`` for software-only runs and tests, ``CYTON_BOARD`` for
  the OpenBCI-compatible 8-channel 250 Hz serial path.
* Builds the ``BrainFlowInputParams`` used to open a session (serial port,
  MAC address) and exposes the row indices for EEG, timestamp and marker data.
* Enumerates serial ports on the host and, on macOS, lists the Bluetooth
  devices the operating system already knows about.

What this module deliberately does NOT do
-----------------------------------------
* It does **not** implement the BROADMIND device's own USB/BLE wire protocol.
  That protocol is not public. Live acquisition goes through BrainFlow, which
  owns the transport layer (see ``docs/protocol.md`` for exactly what is and
  is not implemented).
* It does **not** perform a live BLE advertising scan. :func:`list_known_bluetooth_devices`
  reads what macOS already knows; a real scan needs a BLE stack (``bleak`` /
  CoreBluetooth) and is roadmap, not feature.
* It does **not** verify that a BroadMind headset is protocol-compatible with
  the Cyton path. That assumption is labelled ``unverified`` everywhere and is
  the first thing hardware-in-the-loop testing must confirm.
* It does **not** interpret the signal or make clinical claims.
"""
from __future__ import annotations

import glob
import json
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams

from . import montage

__all__ = [
    "DeviceSpec",
    "DEVICE",
    "Transport",
    "TRANSPORT_STATUS",
    "DEFAULT_PREAMP_GAIN",
    "BoardConfig",
    "BoardConfigError",
    "UnsupportedTransportError",
    "make_board_config",
    "supported_transports",
    "microvolts_per_count",
    "adc_counts_to_uv",
    "channel_gains",
    "discover_serial_ports",
    "list_known_bluetooth_devices",
    "discover",
    "brainflow_version",
]

# ---------------------------------------------------------------------------
# Device specification (vendor-stated)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceSpec:
    """Specification of the BROADMIND headset.

    Every value here is **as stated by BROADMIND on its public site**; none of
    it has been measured by this SDK. ``noise_uvrms_*`` are quoted input-referred
    noise figures, not measured values.
    """

    name: str = "BROADMIND EEG (8-channel dry active)"
    n_channels: int = 8
    sampling_rate: int = 250
    adc_bits: int = 24
    #: Analog bandwidth of the front end, Hz (low cut, high cut).
    bandwidth_hz: tuple[float, float] = (0.02, 125.0)
    #: Pre-amplification at the electrode (hardware gain, before the ADC).
    preamp_gain: float = 5.0
    #: Input-referred noise, Full Kit (active electrodes), microvolts RMS.
    noise_uvrms_full_kit: float = 0.16
    #: Input-referred noise, Starter Kit (passive electrodes), microvolts RMS.
    noise_uvrms_starter_kit: float = 0.8
    #: Physical connectors available on the headset.
    connectors: tuple[str, ...] = ("USB-C", "magnetic 360", "Bluetooth LE")
    #: Electrode sites, in device channel order.
    channel_names: tuple[str, ...] = montage.CHANNEL_NAMES
    ground: str = montage.GROUND_ELECTRODE
    reference: str = montage.REFERENCE_ELECTRODE

    @property
    def nyquist_hz(self) -> float:
        """Nyquist frequency implied by the sampling rate."""
        return self.sampling_rate / 2.0

    @property
    def sample_period_ms(self) -> float:
        """Duration of one sample in milliseconds (4 ms at 250 Hz)."""
        return 1000.0 / self.sampling_rate

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable description, labelled as vendor-stated."""
        return {
            "name": self.name,
            "n_channels": self.n_channels,
            "sampling_rate_hz": self.sampling_rate,
            "adc_bits": self.adc_bits,
            "bandwidth_hz": list(self.bandwidth_hz),
            "preamp_gain": self.preamp_gain,
            "noise_uvrms_full_kit": self.noise_uvrms_full_kit,
            "noise_uvrms_starter_kit": self.noise_uvrms_starter_kit,
            "connectors": list(self.connectors),
            "channel_names": list(self.channel_names),
            "ground": self.ground,
            "reference": self.reference,
            "spec_source": "BROADMIND public product page (vendor-stated, not measured)",
        }


#: The device specification used throughout the package.
DEVICE = DeviceSpec()

#: Hardware pre-amplification, applied once per channel at the electrode.
DEFAULT_PREAMP_GAIN: float = DEVICE.preamp_gain


def microvolts_per_count(
    gain: float = DEFAULT_PREAMP_GAIN,
    vref_volts: float = 4.5,
    adc_bits: int = 24,
) -> float:
    """ADC count -> microvolts conversion factor.

    ``factor = VREF / 2**(bits-1) / gain * 1e6``.

    This is the same relationship OpenBCI publishes for its ADS1299 front end;
    at gain 24 and a 4.5 V reference it reproduces OpenBCI's documented
    ``0.0223517413 uV/count`` (see ``PROVENANCE.md``). For the BroadMind
    preamp gain of 5 the factor is ``0.1072883606 uV/count`` *if* the ADC and
    reference match that assumption - which is unverified.
    """
    if gain <= 0:
        raise ValueError(f"gain must be positive, got {gain}")
    if not 1 <= adc_bits <= 32:
        raise ValueError(f"adc_bits out of range: {adc_bits}")
    return vref_volts / float(2 ** (adc_bits - 1)) / gain * 1e6


def adc_counts_to_uv(
    counts: Sequence[int] | Any,
    gain: float = DEFAULT_PREAMP_GAIN,
    vref_volts: float = 4.5,
    adc_bits: int = 24,
) -> list[float]:
    """Convert raw ADC counts to microvolts (see :func:`microvolts_per_count`)."""
    factor = microvolts_per_count(gain=gain, vref_volts=vref_volts, adc_bits=adc_bits)
    return [float(c) * factor for c in counts]


def channel_gains(gain: float = DEFAULT_PREAMP_GAIN) -> tuple[float, ...]:
    """Return the per-channel hardware gain (all channels share the same gain)."""
    return tuple([float(gain)] * DEVICE.n_channels)


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class Transport(str, Enum):
    """How data reaches the SDK."""

    #: No hardware: BrainFlow's synthetic board. This is the *verified* path.
    SYNTHETIC = "synthetic"
    #: USB serial, OpenBCI Cyton-compatible 8-channel 250 Hz protocol.
    #: Vendor-confirmed to be the same protocol as an OpenBCI board; not yet
    #: verified against BroadMind hardware on this bench.
    SERIAL = "serial"
    #: Bluetooth LE. Not implemented: this is the product path and it is being
    #: built from scratch (BLE 5, own GATT service, battery-powered radio), not
    #: as an OpenBCI WiFi-shield clone. See docs/wireless.md.
    BLE = "ble"


#: Honest status of every transport, surfaced by the CLI and README.
TRANSPORT_STATUS: dict[str, str] = {
    Transport.SYNTHETIC.value: "verified (BrainFlow SYNTHETIC_BOARD, no hardware)",
    Transport.SERIAL.value: "OpenBCI-Cyton-compatible per vendor; not yet verified on this bench",
    Transport.BLE.value: "not implemented (own BLE 5 transport is the product path; see docs/wireless.md)",
}

_BOARD_IDS: dict[Transport, int] = {
    Transport.SYNTHETIC: int(BoardIds.SYNTHETIC_BOARD),
    Transport.SERIAL: int(BoardIds.CYTON_BOARD),
}


class BoardConfigError(ValueError):
    """Raised when a board/transport combination cannot be built."""


class UnsupportedTransportError(NotImplementedError):
    """Raised when a transport is advertised by the roadmap but not implemented."""


# ---------------------------------------------------------------------------
# Board configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardConfig:
    """A resolved BrainFlow configuration for one transport.

    This is a *value* description: it holds no open session. Pass it to
    :class:`broadmind.stream.Stream` to actually acquire data.
    """

    transport: Transport
    board_id: int
    device: DeviceSpec = DEVICE
    serial_port: str | None = None
    mac_address: str | None = None
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    # -- BrainFlow glue -----------------------------------------------------

    def input_params(self) -> BrainFlowInputParams:
        """Return the ``BrainFlowInputParams`` for this configuration."""
        params = BrainFlowInputParams()
        if self.serial_port:
            params.serial_port = self.serial_port
        if self.mac_address:
            params.mac_address = self.mac_address
        for key, value in self.extra.items():
            if hasattr(params, key):
                setattr(params, key, value)
        return params

    @property
    def status(self) -> str:
        """Honest status string for this transport."""
        return TRANSPORT_STATUS.get(self.transport.value, "unknown")

    # -- Channel layout -----------------------------------------------------

    def eeg_rows(self) -> tuple[int, ...]:
        """BrainFlow row indices for the device's eight EEG channels.

        BrainFlow returns the board's full row set (package number, EEG,
        accelerometer, timestamps, marker, ...). For the 8-channel device the
        first eight EEG rows are the device channels, in device order.
        """
        rows = list(BoardShim.get_eeg_channels(self.board_id))
        if len(rows) < self.device.n_channels:
            raise BoardConfigError(
                f"board {self.board_id} exposes {len(rows)} EEG rows, "
                f"the device has {self.device.n_channels} channels"
            )
        return tuple(rows[: self.device.n_channels])

    def timestamp_row(self) -> int:
        """BrainFlow row index holding the per-sample UNIX timestamp (float64 s)."""
        return int(BoardShim.get_timestamp_channel(self.board_id))

    def marker_row(self) -> int:
        """BrainFlow row index holding the marker channel."""
        return int(BoardShim.get_marker_channel(self.board_id))

    def channel_names(self) -> tuple[str, ...]:
        """Channel labels in device order (authoritative, from the montage)."""
        return montage.CHANNEL_NAMES

    def sampling_rate(self) -> int:
        """Sampling rate reported by BrainFlow for this board."""
        return int(BoardShim.get_sampling_rate(self.board_id))

    # -- Reporting ----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable description of this configuration."""
        return {
            "transport": self.transport.value,
            "transport_status": self.status,
            "board_id": self.board_id,
            "sampling_rate_hz": self.sampling_rate(),
            "eeg_rows": list(self.eeg_rows()),
            "timestamp_row": self.timestamp_row(),
            "marker_row": self.marker_row(),
            "serial_port": self.serial_port,
            "mac_address": self.mac_address,
            "note": self.note,
            "device": self.device.to_dict(),
        }

    def describe(self) -> str:
        """Human-readable multi-line summary."""
        rows = ", ".join(str(r) for r in self.eeg_rows())
        lines = [
            f"transport     : {self.transport.value} [{self.status}]",
            f"brainflow id  : {self.board_id}",
            f"sampling rate : {self.sampling_rate()} Hz",
            f"eeg rows      : {rows}",
            f"timestamp row : {self.timestamp_row()}",
            f"marker row    : {self.marker_row()}",
            f"channels      : {', '.join(self.channel_names())}",
            f"ground/ref    : {self.device.ground} / {self.device.reference}",
        ]
        if self.serial_port:
            lines.append(f"serial port   : {self.serial_port}")
        if self.mac_address:
            lines.append(f"mac address   : {self.mac_address}")
        if self.note:
            lines.append(f"note          : {self.note}")
        return "\n".join(lines)


def supported_transports() -> dict[str, str]:
    """Return transport name -> implementation status."""
    return dict(TRANSPORT_STATUS)


def make_board_config(
    transport: str | Transport = "synthetic",
    serial_port: str | None = None,
    mac_address: str | None = None,
    board_id: int | None = None,
    **extra: Any,
) -> BoardConfig:
    """Build a :class:`BoardConfig` for a transport.

    Parameters
    ----------
    transport:
        ``"synthetic"`` (default, no hardware), ``"serial"`` (OpenBCI Cyton
        compatible USB serial path) or ``"ble"`` (not implemented; the product
        path is a purpose-built BLE 5 transport — see docs/wireless.md).
    serial_port:
        Serial device path for the ``serial`` transport, e.g.
        ``/dev/cu.usbserial-1420`` or ``COM3``.
    mac_address:
        Recorded for reporting only; the BLE transport itself is not implemented.
    board_id:
        Override the BrainFlow board id (advanced use, e.g. a different
        BrainFlow-supported board).
    **extra:
        Additional ``BrainFlowInputParams`` fields (e.g. ``timeout=10``).

    Raises
    ------
    UnsupportedTransportError
        For ``"ble"``, which is documented as roadmap rather than shipped.
    BoardConfigError
        For unknown transport names.
    """
    if isinstance(transport, Transport):
        resolved = transport
    else:
        try:
            resolved = Transport(str(transport).lower())
        except ValueError as exc:
            known = ", ".join(t.value for t in Transport)
            raise BoardConfigError(
                f"unknown transport {transport!r}; expected one of: {known}"
            ) from exc

    if resolved is Transport.BLE:
        raise UnsupportedTransportError(
            "The BLE transport is not implemented yet. The wireless path is a "
            "purpose-built BLE 5 transport (own GATT service, battery-powered "
            "radio), designed from scratch rather than adopting the legacy "
            "OpenBCI WiFi-shield protocol — see docs/wireless.md. "
            "Use transport='serial' for the OpenBCI-compatible USB path, or "
            "transport='synthetic' for software-only runs."
        )

    resolved_board_id = _BOARD_IDS[resolved] if board_id is None else int(board_id)
    note = ""
    if resolved is Transport.SERIAL:
        note = (
            "OpenBCI Cyton-compatible 8-channel 250 Hz serial path. The vendor "
            "states the kit's firmware protocol is the same as an OpenBCI "
            "board; this SDK has not yet confirmed it on hardware."
        )
    elif resolved is Transport.SYNTHETIC:
        note = (
            "BrainFlow synthetic board: realistic-shaped but artificial data, "
            "not a recording of a person."
        )

    return BoardConfig(
        transport=resolved,
        board_id=resolved_board_id,
        serial_port=serial_port,
        mac_address=mac_address,
        note=note,
        extra=dict(extra),
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

#: Substrings (lower case) that hint at an OpenBCI-compatible USB-serial bridge.
_SERIAL_HINTS: tuple[str, ...] = (
    "openbci",
    "cyton",
    "ftdi",
    "ft232",
    "cp210",
    "ch340",
    "ch341",
    "silicon labs",
    "usb serial",
    "usbmodem",
    "usbserial",
)


def _looks_compatible(description: str) -> bool:
    text = (description or "").lower()
    return any(hint in text for hint in _SERIAL_HINTS)


def discover_serial_ports() -> list[dict[str, Any]]:
    """List serial ports on the host.

    Uses ``pyserial`` when it happens to be installed (rich USB VID/PID data),
    and otherwise falls back to the operating system's device nodes - no extra
    dependency is required. A port is flagged ``likely_compatible`` when its
    name/description matches the usual OpenBCI USB-serial bridges; the flag is a
    heuristic, not proof.

    Returns
    -------
    list of dict
        ``{"port", "description", "hwid", "likely_compatible"}`` per port.
    """
    ports: list[dict[str, Any]] = []
    try:  # optional, richer metadata
        from serial.tools import list_ports  # type: ignore

        for info in list_ports.comports():
            ports.append(
                {
                    "port": info.device,
                    "description": info.description or "",
                    "hwid": info.hwid or "",
                    "likely_compatible": _looks_compatible(
                        f"{info.device} {info.description} {info.hwid}"
                    ),
                }
            )
        return ports
    except ImportError:
        pass

    patterns: list[str]
    if sys.platform.startswith("win"):  # pragma: no cover - not exercised here
        patterns = [f"COM{i}" for i in range(1, 65)]
    elif sys.platform == "darwin":
        patterns = ["/dev/cu.*", "/dev/tty.usb*", "/dev/tty.usbserial*"]
    else:
        patterns = ["/dev/ttyUSB*", "/dev/ttyACM*", "/dev/serial/by-id/*"]

    seen: set[str] = set()
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            if path in seen:
                continue
            seen.add(path)
            ports.append(
                {
                    "port": path,
                    "description": "serial device node (pyserial not installed)",
                    "hwid": "",
                    "likely_compatible": _looks_compatible(path),
                }
            )
    return ports


def list_known_bluetooth_devices(timeout: float = 15.0) -> list[dict[str, Any]]:
    """List Bluetooth devices the operating system already knows about.

    On macOS this shells out to ``system_profiler SPBluetoothDataType -json``
    (read-only, no pairing, no scanning). On other platforms it returns an empty
    list and says so in the ``note`` entry of the returned list.

    This is **not** a live BLE advertising scan: it lists paired/remembered
    devices, including powered-off speakers and keyboards. A live scan needs a
    BLE stack and is roadmap. The result is best effort and OS-dependent.

    Returns
    -------
    list of dict
        ``{"name", "address", "connected", "likely_broadmind"}`` entries, or a
        single ``{"unavailable": True, "note": ...}`` entry when unsupported.
    """
    if platform.system() != "Darwin":
        return [
            {
                "unavailable": True,
                "note": (
                    "Known-Bluetooth-device enumeration is only implemented for "
                    "macOS; a portable live BLE scan is roadmap."
                ),
            }
        ]
    try:
        proc = subprocess.run(
            ["system_profiler", "SPBluetoothDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [{"unavailable": True, "note": f"system_profiler failed: {exc}"}]

    if proc.returncode != 0 or not proc.stdout.strip():
        return [
            {
                "unavailable": True,
                "note": "system_profiler returned no Bluetooth data on this machine",
            }
        ]
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return [{"unavailable": True, "note": f"unparseable system_profiler output: {exc}"}]

    devices: list[dict[str, Any]] = []
    for entry in payload.get("SPBluetoothDataType", []) or []:
        for section, connected in (
            ("device_connected", True),
            ("device_not_connected", False),
        ):
            for item in entry.get(section, []) or []:
                if not isinstance(item, dict):
                    continue
                for name, attrs in item.items():
                    attrs = attrs if isinstance(attrs, dict) else {}
                    devices.append(
                        {
                            "name": str(name),
                            "address": str(attrs.get("device_address", "")),
                            "connected": connected,
                            "likely_broadmind": "broadmind" in str(name).lower(),
                        }
                    )
    return devices


def discover() -> dict[str, Any]:
    """Aggregate discovery information for reports and CLI output.

    The result includes the serial ports present on the host, the Bluetooth
    devices the OS knows about, and an explicit statement of what is *not*
    implemented (BLE transport, live BLE scanning, protocol compatibility).
    """
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "serial_ports": discover_serial_ports(),
        "known_bluetooth_devices": list_known_bluetooth_devices(),
        "serial_transport": TRANSPORT_STATUS[Transport.SERIAL.value],
        "ble_transport": TRANSPORT_STATUS[Transport.BLE.value],
        "caveats": [
            "A serial port that looks like an OpenBCI bridge is not proof that "
            "BroadMind hardware speaks that protocol.",
            "Known Bluetooth devices are not a live BLE scan.",
        ],
    }


def brainflow_version() -> str:
    """Return the installed BrainFlow package version (or 'unknown')."""
    try:
        from importlib.metadata import version

        return version("brainflow")
    except Exception:  # pragma: no cover - defensive
        return "unknown"
