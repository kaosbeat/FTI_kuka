# kukart

The **original** KUKA control scripts — monolithic prototypes that map MIDI input to
KUKA motion. They predate the modular rewrite in `../rtr/` (which is the current
system). Kept for reference; `../rtr/README.md` describes what changed and why.

These are **not CLIs**: there is no `argparse`. Each script runs its own event loop,
reads MIDI, and drives the robot through `kukapy`. Settings live in the global
`kukastate` dict (`robotstates.py`) and the MIDI port index in each script's `main()`.

## Scripts

| Script | MIDI port | Notes |
| ------ | --------- | ----- |
| `tidalkuka.py` | `0` | The main prototype: MIDI-in → KUKA, `kukastate` + wander/zone logic. |
| `tidalkuka_v01.py` | `0` | Earlier variant of `tidalkuka.py` (same shape). |
| `kukaserver.py` | `1` | Same logic, reads MIDI on a second port. |
| `robotstates.py` | — | The global `kukastate` dict: zones, poses, per-axis `limits`, modes, speeds. |
| `robothelpers.py` | — | Joint-limit math + pose helpers shared by the three scripts. |
| `tidalkuka.py.backup` | — | Scratch backup; not run. |

## Run

Each script runs to completion until Ctrl-C. `main()` opens the MIDI port, starts the
KUKA loop in a daemon thread, and registers the MIDI callback:

```bash
python tidalkuka.py       # MIDI in port 0
python kukaserver.py      # MIDI in port 1
```

The KUKA link is the **reversed** EKI socket (Python is the TCP *server*, the KRC
dials in on port `18735`). `main()` calls `robot.connect()` before the loop starts, so
the blocking `accept()` never touches the event loop. Start the robot side
(`KUKAPY_SERVER` on the teach pendant) to complete the link.

## Dependencies

```bash
pip install rtmidi numpy scipy
# kukapy (the EKI client) is installed editable from the local checkout:
pip install -e ../kukapyEKI/KukaPyNEW
```

`kukapy` is imported at the top of each script, so these scripts require it (unlike
`../rtr/`, which imports it lazily and runs `--sim` without it).

## Config

Edit `robotstates.py`:

- `kukastate["limits"]` / `kukastate["softwarelimits"]` — per-axis joint limits.
- `kukastate["zones"]` — the zone table (`startpos`, `safezone`, `actions`, `exitpos`,
  `exits`, `speed` per zone).
- `kukastate["speed"]`, `wandermode`, `randomwristmode`, `limitadjust` — motion behaviour.

The MIDI → command mapping is in the `MidiInputHandler` class in each script
(hardcoded, unlike `../rtr/`'s learned `midi.json`).
