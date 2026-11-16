# Montage

The BROADMIND headset records **8 EEG channels** in a fixed order, with `Cz`
tied to ground and `CPz` tied to the reference (driven right leg, DRL):

| Index | Channel | 10-20 site | Approx. plotting x | Approx. plotting y |
|---:|---|---|---:|---:|
| 0 | `CP1` | left centro-parietal | -0.25 | -0.32 |
| 1 | `CP2` | right centro-parietal | +0.25 | -0.32 |
| 2 | `C3` | left central (sensorimotor) | -0.50 | 0.00 |
| 3 | `C4` | right central (sensorimotor) | +0.50 | 0.00 |
| 4 | `CP5` | left centro-parietal, lateral | -0.72 | -0.26 |
| 5 | `CP6` | right centro-parietal, lateral | +0.72 | -0.26 |
| 6 | `FT7` | left fronto-temporal | -0.90 | +0.32 |
| 7 | `FT8` | right fronto-temporal | +0.90 | +0.32 |

| Role | Electrode | Notes |
|---|---|---|
| Ground (GND) | `Cz` | device ground, not a data channel |
| Reference (DRL) | `CPz` | driven-right-leg reference of the active-electrode front end |

This ordering is the on-disk contract of the SDK: the CSV header, the EDF
signal order and the JSON `channel_names` field all use exactly this sequence
(`tests/test_smoke.py::test_montage_order_and_length` locks it down).

## Hardware notes (vendor-stated)

* **Full Kit**: 8 dry **active** electrodes with x5 pre-amplification at the
  electrode; vendor-stated input-referred noise 0.16 µVrms.
* **Starter Kit**: **passive** electrodes; vendor-stated input-referred noise
  0.8 µVrms.
* Sampling 250 Hz, 24-bit, 0.02-125 Hz analog bandwidth; individually
  replaceable electrodes; self-adjusting shell.
* Ground `Cz`, reference `CPz`, as above.

The channel layout is identical between kits; only the electrode type (and
therefore noise and some artefact sensitivity) changes.

## Plotting layout

`broadmind.montage.POSITIONS_2D` gives one `(x, y)` pair per channel on a unit
head model: `x` grows to the subject's right in `[-1, +1]`, `y` grows towards
the nose in `[-1, +1]`, nose at `(0, +1)`, inion at `(0, -1)`. The coordinates
were hand-derived from the 10-20/10-10 lattice as a **drawing aid**:

> They are **not** measured, digitised or MRI-registered positions, and no
> distance in this layout should be read as anatomy.

```python
import matplotlib.pyplot as plt
from broadmind.montage import CHANNEL_NAMES, POSITIONS_2D, GROUND_ELECTRODE, REFERENCE_ELECTRODE

fig, ax = plt.subplots(figsize=(4, 4))
ax.add_patch(plt.Circle((0, 0), 1.0, fill=False, lw=1.5, color="black"))
for name, (x, y) in POSITIONS_2D.items():
    ax.plot(x, y, "o", ms=9, color="#1f77b4")
    ax.text(x, y + 0.07, name, ha="center", fontsize=9)
for name, (x, y) in {GROUND_ELECTRODE: (0, 0), REFERENCE_ELECTRODE: (0, -0.42)}.items():
    ax.plot(x, y, "x", ms=9, color="grey")
    ax.text(x, y - 0.12, name, ha="center", fontsize=8, color="grey")
ax.set_xlim(-1.15, 1.15); ax.set_ylim(-1.15, 1.15); ax.set_aspect("equal"); ax.axis("off")
plt.show()
```

## Channel order in files and how to fix a mismatch

If data arrives in a different order (e.g. exported by another tool), do not
rename rows by hand. Use:

```python
from broadmind.montage import reorder, validate_channel_order
fixed = reorder(data, current_names, target_names=("CP1", "CP2", "C3", "C4", "CP5", "CP6", "FT7", "FT8"))
validate_channel_order(fixed_names)   # raises MontageError on any mismatch
```

`Recording` refuses to construct when channel names are the right set but the
wrong order, which catches the most common silent data corruption early.

## Referencing

* The device references to `CPz` (DRL) in hardware. **The SDK does not
  re-reference data.** Exports contain what the hardware produced.
* If a study needs an average reference, a Laplacian or a linked-ear reference,
  do it explicitly downstream (MNE/EEGLAB) and document it. Re-referencing
  changes the numbers; an SDK should not do it invisibly.
* Because `Cz` is the ground and `CPz` the reference, neither appears as a data
  channel. Do not interpolate them from surrounding channels and claim 10
  channels were measured.

## Loading in EEGLAB / MNE

The EDF writer produces EDF+ files; both tools read them.

**MNE** (verified with MNE 1.13.2 on a file produced by this SDK):

```python
import mne

raw = mne.io.read_raw_edf("session.edf", preload=True)
print(raw.ch_names)          # ['CP1', 'CP2', 'C3', 'C4', 'CP5', 'CP6', 'FT7', 'FT8']
print(raw.info["sfreq"])     # 250.0
print(raw.get_data() * 1e6)  # MNE stores SI volts; x1e6 gives microvolts
print(raw.annotations)       # artefact labels arrive as EDF+ annotations

# All eight names exist in MNE's 10-20 montage:
raw.set_montage(mne.channels.make_standard_montage("standard_1020"), match_case=False)
# MNE >= 1.13 deprecates the name "standard_1020" in favour of "colin27_1020".
```

**EEGLAB** (untested here - no MATLAB on the build machine):

```matlab
EEG = pop_biosig('session.edf');
% Channel labels are CP1..FT8; Cz (ground) and CPz (reference) are not data.
EEG = pop_chanedit(EEG, 'lookup', 'standard-10-5-cap385.elp');
```

## Limits

* No electrode impedance measurement, channel quality check or automatic bad
  channel detection. The hardware may support impedance checks; the command
  protocol is not documented publicly, so this SDK does not guess it.
* No digitised subject-specific electrode positions.
* No support for moving the ground/reference off `Cz`/`CPz`.
