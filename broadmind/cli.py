"""Command line interface: ``broadmind info`` and ``broadmind record``.

What this module does
---------------------
* ``broadmind info`` prints the device specification, montage, transport
  status, library versions and the serial ports present on the machine
  (optionally the Bluetooth devices the OS knows about).
* ``broadmind record --seconds N --out FILE`` records from the synthetic board
  (default) or the OpenBCI-compatible serial path and writes CSV, EDF or JSON,
  optionally attaching artefact labels.

What this module deliberately does NOT do
-----------------------------------------
* It does not open a Bluetooth session (not implemented).
* It does not upload anything; files stay on disk.
* It does not claim a device is connected: every transport status is printed
  as verified / unverified / not implemented.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy
import edfio

from . import __version__
from .artifacts import describe as describe_artifacts, summarize
from .board import (
    DEVICE,
    TRANSPORT_STATUS,
    UnsupportedTransportError,
    BoardConfigError,
    brainflow_version,
    discover_serial_ports,
    list_known_bluetooth_devices,
    supported_transports,
)
from .board import make_board_config
from .export import SUPPORTED_FORMATS, export, _format_of
from .montage import describe as describe_montage
from .montage import to_dict as montage_dict
from .stream import Stream


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="broadmind",
        description=(
            "BROADMIND EEG SDK - acquire, label and export 8-channel EEG. "
            "Live acquisition uses BrainFlow; the synthetic board is the "
            "verified no-hardware path."
        ),
    )
    parser.add_argument("--version", action="version", version=f"broadmind-eeg {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="show device, montage and transport information")
    info.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of text",
    )
    info.add_argument(
        "--scan-bluetooth",
        action="store_true",
        help="also list Bluetooth devices the OS knows about (macOS, best effort)",
    )

    record = sub.add_parser("record", help="record data and write CSV, EDF or JSON")
    record.add_argument("--seconds", type=float, required=True, help="duration in seconds")
    record.add_argument("--out", required=True, help="output file (.csv, .edf or .json)")
    record.add_argument(
        "--format",
        choices=SUPPORTED_FORMATS,
        default=None,
        help="output format (default: from the file extension)",
    )
    record.add_argument(
        "--transport",
        default="synthetic",
        choices=sorted(TRANSPORT_STATUS),
        help="acquisition transport (default: synthetic, the verified path)",
    )
    record.add_argument(
        "--serial-port",
        default=None,
        help="serial device for --transport serial, e.g. /dev/cu.usbserial-1420",
    )
    record.add_argument(
        "--detect-artifacts",
        action="store_true",
        help="label blinks and jaw clenches in the recorded data (labels only)",
    )
    record.add_argument(
        "--buffer-seconds",
        type=float,
        default=300.0,
        help="ring buffer size in seconds (default: 300)",
    )
    return parser


def _versions() -> dict[str, str]:
    try:
        numpy_version = numpy.__version__
    except Exception:  # pragma: no cover - defensive
        numpy_version = "unknown"
    return {
        "broadmind-eeg": __version__,
        "brainflow": brainflow_version(),
        "numpy": numpy_version,
        "edfio": getattr(edfio, "__version__", "unknown"),
        "python": sys.version.split()[0],
    }


def _info_payload(scan_bluetooth: bool) -> dict:
    payload = {
        "sdk_version": __version__,
        "device": DEVICE.to_dict(),
        "montage": montage_dict(),
        "transports": supported_transports(),
        "versions": _versions(),
        "serial_ports": discover_serial_ports(),
    }
    if scan_bluetooth:
        payload["known_bluetooth_devices"] = list_known_bluetooth_devices()
    return payload


def cmd_info(args: argparse.Namespace) -> int:
    payload = _info_payload(args.scan_bluetooth)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    device = payload["device"]
    lines = [
        f"BROADMIND EEG SDK {__version__}  (Python {payload['versions']['python']})",
        "",
        "Device (vendor-stated, not measured by this SDK)",
        f"  {device['name']}",
        f"  channels        : {device['n_channels']}  ({', '.join(device['channel_names'])})",
        f"  sampling rate   : {device['sampling_rate_hz']} Hz, {device['adc_bits']}-bit",
        f"  bandwidth       : {device['bandwidth_hz'][0]}-{device['bandwidth_hz'][1]} Hz",
        f"  preamp gain     : x{device['preamp_gain']}",
        f"  noise (Full)    : {device['noise_uvrms_full_kit']} uVrms",
        f"  noise (Starter) : {device['noise_uvrms_starter_kit']} uVrms",
        f"  connectors      : {', '.join(device['connectors'])}",
        "",
        "Montage",
        describe_montage(),
        "",
        "Transports",
    ]
    for name, status in payload["transports"].items():
        lines.append(f"  {name:<10} : {status}")
    lines += [
        "",
        "Versions",
        "  " + "  ".join(f"{k}={v}" for k, v in payload["versions"].items()),
        "",
        "Serial ports",
    ]
    ports = payload["serial_ports"]
    if not ports:
        lines.append("  none found")
    for port in ports:
        flag = " (looks OpenBCI-compatible)" if port["likely_compatible"] else ""
        lines.append(f"  {port['port']}{flag}")
    if args.scan_bluetooth:
        lines += ["", "Bluetooth devices known to the OS (not a live scan)"]
        for device in payload.get("known_bluetooth_devices", []):
            if device.get("unavailable"):
                lines.append(f"  unavailable: {device.get('note', '')}")
            else:
                state = "connected" if device["connected"] else "not connected"
                lines.append(f"  {device['name']} [{device['address']}] {state}")
    lines += [
        "",
        "Notes",
        "  * Sending to a real headset needs the OpenBCI-compatible serial path",
        "    (unverified against BroadMind hardware) or a future BLE transport.",
        "  * Artefact detection returns labels only; it never deletes samples.",
    ]
    print("\n".join(lines))
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    try:
        fmt = (args.format or _format_of(args.out)).lower()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if fmt not in SUPPORTED_FORMATS:
        print(
            f"error: unsupported format {fmt!r}; use one of {', '.join(SUPPORTED_FORMATS)}",
            file=sys.stderr,
        )
        return 2

    try:
        config = make_board_config(args.transport, serial_port=args.serial_port)
    except UnsupportedTransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BoardConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.seconds <= 0:
        print("error: --seconds must be positive", file=sys.stderr)
        return 2

    stream = Stream(config=config, buffer_seconds=args.buffer_seconds)
    try:
        stream.start()
        recording = stream.record(
            args.seconds,
            detect_artifacts=args.detect_artifacts,
        )
    except TimeoutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    finally:
        stream.close()

    out_path = Path(args.out).expanduser()
    export(recording, out_path, fmt=fmt)

    size = out_path.stat().st_size if out_path.exists() else 0
    print(
        f"recorded {recording.duration_s:.3f} s "
        f"({recording.n_samples} samples, {recording.n_channels} channels) "
        f"from {args.transport} [{TRANSPORT_STATUS[args.transport]}]"
    )
    if args.detect_artifacts:
        counts = summarize(recording.artifacts)
        print(
            "artefacts: " + ", ".join(f"{k}={v}" for k, v in counts.items())
        )
        print(describe_artifacts(recording.artifacts))
    print(f"wrote {out_path} ({size} bytes, format={fmt})")
    if config.transport.value != "synthetic":
        print(
            "warning: serial-path compatibility with BroadMind hardware is "
            "UNVERIFIED; confirm with a hardware-in-the-loop test.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``broadmind`` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "info":
        return cmd_info(args)
    if args.command == "record":
        return cmd_record(args)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
