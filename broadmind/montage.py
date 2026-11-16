"""Channel layout for the BROADMIND 8-channel EEG headset.

What this module does
---------------------
* Declares the authoritative channel order of the device:
  ``CP1, CP2, C3, C4, CP5, CP6, FT7, FT8`` (10-20 / 10-10 sensorimotor sites).
* Declares the electrode roles: ``Cz`` is the ground (GND) and ``CPz`` is the
  driven-right-leg / reference electrode (DRL), exactly as specified by
  BROADMIND for this hardware.
* Provides an approximate, plotting-oriented 2D layout, plus small helpers to
  validate or reorder channel names in files that arrived from elsewhere.

What this module deliberately does NOT do
-----------------------------------------
* It does **not** claim that :data:`POSITIONS_2D` are measured scalp
  coordinates, MRI-registered positions or anatomical distances. They are
  hand-derived plotting coordinates on a unit head model (see the note on
  :data:`POSITIONS_2D`).
* It does **not** re-reference the signal, interpolate bad channels, or
  apply any spatial filter. The device already references to ``CPz``; this
  SDK records and exports what the hardware produced.
* It does **not** interpret the signal or make clinical claims.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

__all__ = [
    "CHANNEL_NAMES",
    "N_CHANNELS",
    "GROUND_ELECTRODE",
    "REFERENCE_ELECTRODE",
    "POSITIONS_2D",
    "MontageError",
    "channel_names",
    "channel_index",
    "plot_positions",
    "validate_channel_order",
    "canonical_order",
    "reorder",
    "to_dict",
    "describe",
]


class MontageError(ValueError):
    """Raised when channel names do not match the BROADMIND montage."""


#: Channel order as wired on the headset, index 0 is the first EDF/CSV column.
CHANNEL_NAMES: tuple[str, ...] = (
    "CP1",
    "CP2",
    "C3",
    "C4",
    "CP5",
    "CP6",
    "FT7",
    "FT8",
)

#: Number of EEG channels on the device.
N_CHANNELS: int = len(CHANNEL_NAMES)

#: Ground electrode (the ``Cz`` site is tied to GND on this hardware).
GROUND_ELECTRODE: str = "Cz"

#: Reference / driven-right-leg electrode (``CPz`` is tied to DRL).
REFERENCE_ELECTRODE: str = "CPz"

#: Human-readable description of where each electrode sits.
POSITION_DESCRIPTIONS: dict[str, str] = {
    "CP1": "left centro-parietal, halfway between Cz and P1",
    "CP2": "right centro-parietal, halfway between Cz and P2",
    "C3": "left central, over the sensorimotor cortex",
    "C4": "right central, over the sensorimotor cortex",
    "CP5": "left centro-parietal, lateral (between C5 and P5)",
    "CP6": "right centro-parietal, lateral (between C6 and P6)",
    "FT7": "left fronto-temporal, anterior",
    "FT8": "right fronto-temporal, anterior",
}

#: Approximate 2D plotting coordinates on a unit head model.
#:
#: Convention: ``x`` grows to the subject's right in ``[-1, +1]``, ``y`` grows
#: towards the nose in ``[-1, +1]``, nose at ``(0, +1)``, inion at ``(0, -1)``.
#: Values were hand-derived from the 10-20/10-10 lattice as a *drawing aid*
#: (lateral fraction of the hemi-width for ``x``, anterior-posterior row for
#: ``y``). They are **not** measured, digitised or MRI-registered coordinates;
#: use a validated digitised montage when spatial precision matters.
POSITIONS_2D: dict[str, tuple[float, float]] = {
    "CP1": (-0.25, -0.32),
    "CP2": (0.25, -0.32),
    "C3": (-0.50, 0.00),
    "C4": (0.50, 0.00),
    "CP5": (-0.72, -0.26),
    "CP6": (0.72, -0.26),
    "FT7": (-0.90, 0.32),
    "FT8": (0.90, 0.32),
}

#: Non-recording electrode roles, for plotting/reporting.
ROLE_POSITIONS_2D: dict[str, tuple[float, float]] = {
    GROUND_ELECTRODE: (0.0, 0.0),
    REFERENCE_ELECTRODE: (0.0, -0.42),
}


def channel_names() -> tuple[str, ...]:
    """Return the channel order as a tuple (a copy: callers may not mutate it)."""
    return CHANNEL_NAMES


def channel_index(name: str) -> int:
    """Return the 0-based row index of ``name`` in the device channel order."""
    try:
        return CHANNEL_NAMES.index(name)
    except ValueError as exc:  # pragma: no cover - message is the point
        raise MontageError(
            f"unknown channel {name!r}; expected one of {CHANNEL_NAMES}"
        ) from exc


def plot_positions() -> dict[str, tuple[float, float]]:
    """Return a channel -> (x, y) plotting layout (approximate, see module docs)."""
    return dict(POSITIONS_2D)


def validate_channel_order(names: Sequence[str]) -> tuple[str, ...]:
    """Validate that ``names`` is exactly the BROADMIND channel order.

    Parameters
    ----------
    names:
        Channel labels, e.g. read back from a CSV or EDF file.

    Returns
    -------
    tuple of str
        The validated names, unchanged.

    Raises
    ------
    MontageError
        If the length is wrong, a name is duplicated, or the order differs.
    """
    names = tuple(str(n) for n in names)
    if len(names) != N_CHANNELS:
        raise MontageError(
            f"expected {N_CHANNELS} channels {CHANNEL_NAMES}, got {len(names)}: {names}"
        )
    if len(set(names)) != len(names):
        raise MontageError(f"duplicate channel names: {names}")
    if names != CHANNEL_NAMES:
        raise MontageError(
            "channel order does not match the device montage.\n"
            f"  expected: {CHANNEL_NAMES}\n"
            f"  got     : {names}\n"
            "Use broadmind.montage.reorder() if the data is correct but ordered "
            "differently."
        )
    return names


def canonical_order(names: Sequence[str]) -> list[int]:
    """Return row indices that put ``names`` into the canonical channel order.

    Example: ``names=("C3", "CP1", ...)`` returns ``[1, 0, ...]``, meaning
    "take row 1 first, then row 0, ...".
    """
    names = [str(n) for n in names]
    if len(names) != len(set(names)):
        raise MontageError(f"duplicate channel names: {names}")
    missing = [c for c in CHANNEL_NAMES if c not in names]
    if missing:
        raise MontageError(f"missing channels: {missing}; got {names}")
    extra = [c for c in names if c not in CHANNEL_NAMES]
    if extra:
        raise MontageError(f"unexpected channels: {extra}; expected {CHANNEL_NAMES}")
    return [names.index(c) for c in CHANNEL_NAMES]


def reorder(
    data: np.ndarray,
    current_names: Sequence[str],
    target_names: Sequence[str] = CHANNEL_NAMES,
) -> np.ndarray:
    """Reorder rows of ``data`` (channels, samples) to ``target_names``.

    ``target_names`` must be a permutation of ``current_names``; a reordering
    never invents, drops or interpolates a channel.
    """
    arr = np.asarray(data)
    if arr.ndim != 2:
        raise MontageError(f"data must be 2-D (channels, samples), got shape {arr.shape}")
    if arr.shape[0] != len(current_names):
        raise MontageError(
            f"data has {arr.shape[0]} rows but {len(current_names)} channel names"
        )
    index = {str(n): i for i, n in enumerate(current_names)}
    target = [str(n) for n in target_names]
    missing = [c for c in target if c not in index]
    if missing:
        raise MontageError(f"target channels not present in data: {missing}")
    if len(set(target)) != len(target):
        raise MontageError(f"duplicate target channels: {target}")
    return arr[[index[c] for c in target], :]


def to_dict() -> dict:
    """Return a JSON-serialisable description of the montage."""
    return {
        "channels": list(CHANNEL_NAMES),
        "n_channels": N_CHANNELS,
        "ground": GROUND_ELECTRODE,
        "reference": REFERENCE_ELECTRODE,
        "positions_2d": {c: list(POSITIONS_2D[c]) for c in CHANNEL_NAMES},
        "position_descriptions": dict(POSITION_DESCRIPTIONS),
        "positions_note": (
            "Approximate 2D plotting layout on a unit head model; not measured "
            "or MRI-registered coordinates."
        ),
    }


def describe() -> str:
    """Return a plain-text table of the montage (useful for CLI output)."""
    lines = [
        f"{'idx':>3}  {'ch':<4} {'x':>6} {'y':>6}  site",
        f"{'-' * 3}  {'-' * 4} {'-' * 6} {'-' * 6}  {'-' * 4}",
    ]
    for i, name in enumerate(CHANNEL_NAMES):
        x, y = POSITIONS_2D[name]
        lines.append(
            f"{i:>3}  {name:<4} {x:>6.2f} {y:>6.2f}  {POSITION_DESCRIPTIONS[name]}"
        )
    lines.append("")
    lines.append(f"ground (GND): {GROUND_ELECTRODE}    reference (DRL): {REFERENCE_ELECTRODE}")
    return "\n".join(lines)


def electrodes() -> tuple[str, ...]:
    """Return recording channels plus the ground and reference sites."""
    return CHANNEL_NAMES + (GROUND_ELECTRODE, REFERENCE_ELECTRODE)

