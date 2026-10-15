# remote

The remote component is a client that talks to the core over a WebSocket (state
frames in, commands out) and renders them. There are two renderers sharing the
same `connection.py` + `view.py` + `config.py`:

| Renderer | File | Renderer lib | Where it runs |
|---|---|---|---|
| Desktop TUI | `rtr/remote/tui.py` | urwid (yellow) | dev machine, terminal |
| Tremui / Trimui app | `rtr/remote/sdlapp.py` | pygame / SDL2 | R36S device |

The **joystick is read raw** from `/dev/input/event*` (via `events.py`) — pygame
is only used for the SDL display surface, fonts, and on-screen keyboard. There is
no `pygame.joystick`.

> **Commands below are run from the `realtimecontrol/rtr` root** (the directory
> that contains `scripts/`, `rtr/`, `pak/`, `dist/`).

## Desktop TUI (yellowbrick)

The canonical TUI lives in `rtr/remote/tui.py`; the top-level `remote/tui.py`
is a thin launcher that bootstraps the package root onto `sys.path` and
re-exports `run()`, so the original invocation keeps working.

```bash
# urwid must be installed on the dev machine
pip install urwid

# run against a core (explicit host)
python rtr/remote/tui.py --core-host 192.168.1.185

# same, via the top-level launcher
python remote/tui.py --core-host 192.168.1.185

# from a config file (base) + explicit flags (override)
python remote/tui.py --config config.json --core-host 192.168.1.185
```

TUI-only flags: `--height` (UI lines, default 4). The TUI ignores the SDL-only
fields (screen size / font / colors / `repo` / `joy_map`).

## Tremui / Trimui SDL app

Runs the same `Connection` + `view` on the device (or locally for testing).

```bash
# local / dev (needs pygame)
python rtr/remote/sdlapp.py --config config.json

# on-device (via the PAK) — launch.sh reads "repo" from config.json and runs
# sdlapp.py with the PAK's bundled python; extra flags pass through via "$@"
/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/launch.sh
/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/launch.sh --core-host 192.168.1.185
```

### Diagnostics (no UI)

| Flag | What it does |
|---|---|
| `--selftest` | Headless self-test: no core, dummy driver. `launch.sh --selftest` is the on-device smoke test. |
| `--ws-test` | Test the WebSocket link to the core and exit (no UI). |
| `--diag` | Print the display / framebuffer diagnostic and exit. |
| `--joy-diag` | Read joystick events and print their codes, then exit. |

```bash
# on-device smoke test (uses the PAK's bundled python)
ssh root@192.168.1.33 "/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/launch.sh --selftest"

# verify the WS link before shipping
python rtr/remote/sdlapp.py --ws-test --core-host 192.168.1.185
```

## Deploying the Trimui PAK

The PAK (`python/` + `venv/` + `fonts/` + `config.json`, ~200 MB) is sent **once**;
the app code (a few hundred KB) is staged in `dist/repo/` and shipped **separately**
for code updates. There is no git/rsync on the device — code ships over
scp / tar-over-ssh.

Built by `scripts/build_pak.sh` (cross-builds aarch64 on the x86_64 dev machine).

### First time (full PAK)

```bash
# 1. assemble the PAK (fonts + config + code + python/ + venv/ wheels)
scripts/build_pak.sh --full

# 2. copy the PAK to the SD card (slow over wifi — once)
scp -r dist/RTRREMOTE.pak root@192.168.1.33:/mnt/SDCARD/Tools/tg5040/

# 3. ship the app code to the on-device repo path (fast)
scripts/build_pak.sh --ship-code
#    =  tar-over-ssh of dist/repo/rtr -> /storage/rtr-remote

# 4. smoke test on-device
ssh root@192.168.1.33 "/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/launch.sh --selftest"
```

### Code-only update (PAK already on the device)

```bash
# re-stage + ship ONLY the python app code; the ~200MB PAK is NOT re-sent
scripts/build_pak.sh --ship-code

# then smoke test
ssh root@192.168.1.33 "/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/launch.sh --selftest"
```

`--ship-code` streams `dist/repo/rtr` over ssh via `tar` (no rsync, atomic
destination, avoids the OpenSSH 9.x scp `path canonicalization failed` error).
The on-device repo path defaults to `/storage/rtr-remote` (override with
`REPO_PATH=...`), and must match the `"repo"` field in the PAK's `config.json`
so `launch.sh` finds the code.

## Configuration

Shared by both renderers (`rtr/remote/config.py`). Precedence:
`--config <json>` (base) → explicit CLI flags (override) → dataclass defaults.

| Field | Default | Notes |
|---|---|---|
| `core_host` | `192.168.1.185` | core WebSocket host. **PAK ships `127.0.0.1` (loopback) — set to the core's real IP on-device.** |
| `core_port` | `8765` | core WebSocket port |
| `http_host` | `None` | core HTTP host for the zone table (defaults to `core_host`) |
| `http_port` | `8766` | core HTTP port |
| `http_poll` | `30` | poll `/api/zones` every N s while the WS is down |
| `device` | `/dev/input/event3` | input device to read; `"auto"` scans `devices` |
| `devices` | `None` | list to auto-scan when `device` is `"auto"` |
| `joy_map` | `view.DEFAULT_JOY_MAP` | EV_KEY → app-key map (override for a different gamepad) |
| `chord_window_ms` | `200` | L1+R1 quit-chord window (ms) |
| `quit_codes` | `[172]` | single-button quit codes (e.g. BTN_MODE/home) |
| `height` | `4` | desktop TUI lines (TUI only) |
| `width` | `320` | SDL screen width in px (SDL only) |
| `screen_height` | `240` | SDL screen height in px (SDL only) |
| `font` | `None` | font path (SDL only; `None` → pygame default) |
| `font_size` | `16` | font size in px (SDL only) |
| `theme` | `DEFAULT_THEME` | style key → hex color overrides (SDL only) |
| `repo` | `/storage/rtr-remote` | on-device app-code dir (PAK) |

CLI flags (both renderers, via `config._add_flags`): `--config`, `--core-host`,
`--core-port`, `--http-host`, `--http-port`, `--device`, `--repo`,
`--chord-window-ms`, `--height` (TUI), and `--font` / `--font-size` / `--width` /
`--screen-height` (SDL).

### The `core_host: "127.0.0.1"` gotcha

The PAK's `config.json` ships with `core_host` set to `127.0.0.1` (loopback) so
the build is self-contained. On the device that points at the R36S itself, not
the core — so the WS link never connects. Set it to the core's real IP either in
the PAK's `config.json` (`"core_host"`) or at launch:

```bash
/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/launch.sh --core-host 192.168.1.185
```

## OS setup

The R36S runs DarkOS. The original `dtbo` file had to be copied to the boot
partition (somewhere) to make it run.

## emustation setup

An "apps" section was added to `.emustation`.
