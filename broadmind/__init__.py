"""BROADMIND EEG SDK - Python acquisition and analysis for the 8-channel headset.

This package is an *original* Python SDK for BROADMIND's 8-channel dry-electrode
EEG headset. It stands on the OpenBCI ecosystem rather than pretending to have
invented a device stack: live acquisition is delegated to **BrainFlow**, the
maintained OpenBCI-compatible data-acquisition library, and the historical
OpenBCI packet format is documented and implemented for offline parsing.

What the package does
---------------------
* :mod:`broadmind.board` - device specification, BrainFlow board/transport
  configuration, serial and Bluetooth enumeration.
* :mod:`broadmind.montage` - the authoritative channel order (CP1, CP2, C3,
  C4, CP5, CP6, FT7, FT8), ``Cz`` ground, ``CPz`` reference and a plotting
  layout.
* :mod:`broadmind.stream` - sessions, ring buffer, blocking collect and
  callback API, artefact markers.
* :mod:`broadmind.features` - NumPy-only band power (delta/theta/alpha/beta/
  gamma).
* :mod:`broadmind.artifacts` - blink and jaw-clench detection that returns
  labels and never edits the signal.
* :mod:`broadmind.export` - CSV, EDF(+) and JSON writers plus readers.
* :mod:`broadmind.protocol` - OpenBCI-compatible 33-byte packet codec.
* :mod:`broadmind.cli` - ``broadmind info`` and ``broadmind record``.

What the package deliberately does NOT do
-----------------------------------------
* No clinical, diagnostic or cognitive claims anywhere in the code or docs.
* No signal interpretation: it measures and labels, it does not decide what a
  recording "means".
* No cloud, no telemetry, no training on user data - everything is local.
* No implemented Bluetooth transport (the BroadMind BLE protocol is not
  public) and no verified claim that BroadMind hardware speaks the
  OpenBCI-compatible serial protocol.
* No proprietary BroadMind firmware or hardware files are included.

See ``README.md`` for the honest bindings/status table and ``PROVENANCE.md``
for the attribution chain.
"""
from __future__ import annotations

__version__ = "0.1.0"

from . import artifacts, board, export, features, montage, protocol, stream
from .artifacts import (
    ARTIFACT_LABELS,
    ARTIFACT_MARKER_CODES,
    BLINK,
    CLENCH,
    Artifact,
    label_artifacts,
    summarize as summarize_artifacts,
)
from .board import (
    DEVICE,
    TRANSPORT_STATUS,
    BoardConfig,
    DeviceSpec,
    Transport,
    discover,
    discover_serial_ports,
    list_known_bluetooth_devices,
    make_board_config,
)
from .export import Recording, export as export_recording, load, read_csv, read_edf, read_json, to_csv, to_edf, to_json
from .features import BANDS_HZ, band_power, dominant_band, power_spectrum, relative_band_power
from .montage import CHANNEL_NAMES, GROUND_ELECTRODE, REFERENCE_ELECTRODE
from .stream import Block, RingBuffer, Stream, open_stream

__all__ = [
    "__version__",
    # modules
    "artifacts",
    "board",
    "export",
    "features",
    "montage",
    "protocol",
    "stream",
    # device / transport
    "DEVICE",
    "TRANSPORT_STATUS",
    "BoardConfig",
    "DeviceSpec",
    "Transport",
    "discover",
    "discover_serial_ports",
    "list_known_bluetooth_devices",
    "make_board_config",
    # montage
    "CHANNEL_NAMES",
    "GROUND_ELECTRODE",
    "REFERENCE_ELECTRODE",
    # acquisition
    "Block",
    "RingBuffer",
    "Stream",
    "open_stream",
    # features
    "BANDS_HZ",
    "band_power",
    "dominant_band",
    "power_spectrum",
    "relative_band_power",
    # artefacts
    "ARTIFACT_LABELS",
    "ARTIFACT_MARKER_CODES",
    "BLINK",
    "CLENCH",
    "Artifact",
    "label_artifacts",
    "summarize_artifacts",
    # export
    "Recording",
    "export_recording",
    "load",
    "read_csv",
    "read_edf",
    "read_json",
    "to_csv",
    "to_edf",
    "to_json",
]
