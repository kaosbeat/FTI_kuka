# RTR Remote TUI as a NextUI PAK (SDL2 terminal app)

## Goal

Turn the existing urwid remote TUI (`realtimecontrol/rtr/rtr/remote/tui.py`) into a
**terminal app for the Trimui handheld running NextUI**, packaged as a NextUI **PAK**
with its **own bundled Python + libs**, supporting **joystick AND keyboard**, with
**easily selectable font and colors**, an **easily testable result**, and quit via
**pressing the two shoulder buttons (L1+R1) together**.

## Resolved decisions

| Decision | Choice |
| --- | --- |
| Rendering | **SDL2 direct text renderer** (pygame). Reuse the existing `RemoteTUI` state model (items / cursor / pages / state). No PTY, no urwid on device. |
| Python env | **Bundle Python + needed libs inside the PAK** (python-build-standalone aarch64 + venv with `pygame` and `websockets`). |
| App code location | The FTI_kuka repo lives at a **fixed path on the device: `/storage/rtr-remote`** (configurable `repo` field in the PAK `config.json`). Workflow: ssh → `git pull` → launch PAK from the NextUI menu. |
| Platform | NextUI ⇒ Trimui Brick / Smart Pro / Smart Pro S ⇒ platform folder **`tg5040`** (`DEVICE` env: `brick`/`brickpro`/`smartpro`). PAK goes in `Tools/tg5040/`. |
| Quit | **L1+R1 chord** (both shoulder buttons pressed, second press within 200 ms of the first). `q`/`escape` kept as fallback. |
| Input | Raw **`/dev/input`** joystick (existing `events.py`) + **SDL2 keyboard** events, unified into app keys. |
| Theme | PAK `config.json`: font path, font size, colors (mirrors the `Theme` class). |
| Testability | Headless pytest with `SDL_VIDEODRIVER=dummy` + a **`--selftest` mode** that runs with no core connection (mock state) and prints PASS/FAIL. |

## Architecture

Shared **pure view layer** + two renderers (urwid desktop, SDL2 device):

```
rtr/remote/
  flow.py          (existing, unchanged)  pure navigation helpers
  config.py        (extend)              + font/color fields
  connection.py    (existing, unchanged) WS/HTTP client (asyncio)
  events.py        (existing, unchanged) raw /dev/input joystick reader
  view.py          (NEW)                 pure: state → rows + items
  tui.py           (refactor)            urwid renderer consumes view.py
  sdlapp.py        (NEW)                 SDL2 renderer + input + loop + --selftest
```

### 1. `rtr/remote/view.py` (NEW, pure)

Extract the row-building logic out of `tui.py` (`_build_rows`, `_nav_content`,
`_camera_content`, `_brain_content`, `_hunt_content`, `_cadence_content`,
`item_label`) into framework-free functions:

- `build_rows(state, zones, page, cursor, engine, hunt, cam_telem, width) -> list[Row]`
  where `Row = {"text": str, "style": str}`.
- Styles (named keys, no urwid/pygame types): `bar`, `conn_ok`, `conn_bad`,
  `page`, `divider`, `state_lab`, `state_val`, `label`, `item`, `item_sel`,
  `item_dim`, `status`, `help`, `feral`, `text`.
- Reuses `item_label`, `build_items`, `CADENCE_ITEMS`, `PAGES`, `SECTION_LABELS`
  (move these from `tui.py` to `view.py`; `tui.py` imports them back).
- `current_items(page, items)` — selectable rows (NAV items vs `CADENCE_ITEMS`),
  mirroring `RemoteTUI._current_items`.

The urwid `tui.py` is refactored to call `view.build_rows` and map each style to its
existing `Theme` AttrSpec (black-on-yellow selected row, dim disabled rows, etc.).
Behavior of the desktop TUI is unchanged; it just gets its rows from the shared builder.

### 2. `rtr/remote/sdlapp.py` (NEW)

`SdlRemote` class mirroring `RemoteTUI`'s threading model:

- **Connection**: reuse `_start_conn`/`_shutdown` pattern — `Connection`
  (asyncio) on a worker thread, thread-safe queue drained on the main thread
  every ~100 ms. Same `_apply_state` / `_apply_zones` / `_on_joystick` logic
  (moved to `view.py`-driven state fields so both renderers share it).
- **Renderer** (pygame):
  - `pygame.display.init()` with `SDL_VIDEODRIVER=kmsdrm` on device
    (`fb` fallback), `dummy` under `--selftest`. Display only — no audio/mixer.
  - Compute `cols`/`rows` from display size + font metrics
    (`font.get_height()`, measured char width).
  - Draw rows from `view.build_rows`; map styles to colors from `config.json`.
  - Selected row = black text on yellow bar (same as the urwid theme).
- **Input** — `InputController` unifying two sources into app keys
  (`up`, `down`, `left`, `right`, `enter`, `q`, `escape`, `h`):
  - **Keyboard**: SDL2 `KEYDOWN` events (physical/OTG keyboard): arrows, `Return`,
    `q`, `Escape`, `h`.
  - **Joystick**: existing `events.Joystick` reader thread on `/dev/input/event*`
    (config `device`, or auto-scan all readable `event*` nodes). EV_KEY mapping
    (configurable `joy_map` in `config.json`):
    - d-pad up/down/left/right (15/16/17/18) → up/down/left/right
    - A (29) → enter, B (30) → q, H (8) → h
    - L1 (310) / R1 (311) → page left/right (existing behavior)
  - **Quit chord**: track pressed set + press timestamps. On a press of L1 or R1,
    if the other is already pressed and the first press is ≤ 200 ms old → quit.
    (Holding L1 to page left, then pressing R1 500 ms later pages right and does
    NOT quit.)
- **`--selftest` mode** (the easily testable result):
  - No core: built-in mock zone table + a state that cycles zones/actions.
  - `SDL_VIDEODRIVER=dummy` (works over ssh, no display needed).
  - Checks: pygame display init, font load, render N frames of mock state,
    simulate up/down/left/right/enter, L1+R1 chord → clean quit.
  - Prints `SELFTEST PASS` (exit 0) or a FAIL line (exit 1).
- **CLI**: reuse `config._add_flags` pattern + `--selftest`, `--config`, `--repo`,
  `--core-host`, `--core-port`, `--device`.

### 3. `rtr/remote/config.py` (extend)

- Extend `Config.theme` colors to the new style-key set (keep existing keys as
  aliases) and add `font` (path) + `font_size`.
- Add `repo: str = "/storage/rtr-remote"` (PAK-level default; harmless elsewhere).
- Add `devices: Optional[list]` (auto-scan when `device` is unset/`"auto"`).

## PAK packaging

NextUI PAK = folder named `*.pak` with a `launch.sh`; tool paks live in
`Tools/<platform>/` on the SD root.

```
realtimecontrol/rtr/pak/RTRREMOTE.pak/
  launch.sh              # entrypoint (below)
  config.json            # repo, core_host/port, device, font, font_size, colors, joy_map
  fonts/DejaVuSansMono.ttf
  python/                # bundled python-build-standalone aarch64 (bin/, lib/)
  venv/lib/...           # site-packages: pygame (bundles SDL2), websockets
realtimecontrol/rtr/scripts/build_pak.sh   # assembles python/ + venv/ + fonts/
realtimecontrol/rtr/pak/requirements-pak.txt  # pygame, websockets (>=12,<14)
```

### `launch.sh` (sketch)

```sh
#!/bin/sh
PakDir=$(dirname "$0")
REPO=$(jq -r .repo "$PakDir/config.json" 2>/dev/null || echo /storage/rtr-remote)
export LD_LIBRARY_PATH="$PakDir/venv/lib/python3*/site-packages/pygame/.libs:$LD_LIBRARY_PATH"
export PYTHONPATH="$REPO:$PYTHONPATH"
export LOG_FILE=${LOGS_PATH:-/tmp}/RTRREMOTE.txt
[ -n "$LOGS_PATH" ] && exec > "$LOGS_PATH/RTRREMOTE.txt" 2>&1   # NextUI logs
exec "$PakDir/python/bin/python3" "$REPO/rtr/remote/sdlapp_main.py" \
  --config "$PakDir/config.json" "$@"
```

- `--selftest` passes through to the app (sets `SDL_VIDEODRIVER=dummy` itself).
- NextUI env (`USERDATA_PATH`, `LOGS_PATH`, `DEVICE`, ...) used when present;
  sane defaults so `launch.sh --selftest` also works over ssh.

### `scripts/build_pak.sh`

1. Download **python-build-standalone** aarch64 (pinned release) → extract into
   `pak/RTRREMOTE.pak/python/`.
2. `pip download --only-binary=:all: --platform manylinux_aarch64 --python-version 311
   -d <tmp> pygame websockets` → extract wheels into `pak/RTRREMOTE.pak/venv/lib/`
   (cross-build on the dev machine; no aarch64 box needed).
3. Copy `DejaVuSansMono.ttf` (covers ← → ↻ ▸ ⚠ #) into `fonts/`.
4. Write `config.json` defaults.
Result: copy the PAK folder to `Tools/tg5040/RTRREMOTE.pak/` on the SD card.

## Tests (dev machine, headless)

New `realtimecontrol/rtr/tests/test_remote_sdl.py` (first tests in a top-level
`tests/` dir; the README's referenced `tests/` dir does not exist yet):

- **View builder**: mock state + zones → expected row texts/styles; page switching
  (NAV/CAMERA/...); item labels with `↻` / `→` / `←` markers; disabled rows get the
  dim style; selected-row styling.
- **Chord detector**: L1@t0 + R1@t0+50 ms → quit; L1@t0 + R1@t0+500 ms → page nav
  only; release resets state.
- **Selftest**: run the full `--selftest` with `SDL_VIDEODRIVER=dummy`
  (subprocess), assert exit 0 + `SELFTEST PASS`.
- **urwid regression**: `tui.py` still builds rows via `view.py` headlessly.
- Pygame is `importorskip`'d; tests run with `pip install pygame websockets pytest`.

## Rollout (on-device, user-driven)

1. Dev machine: `scripts/build_pak.sh` → copy `RTRREMOTE.pak/` to the SD card
   `Tools/tg5040/`.
2. Device: `git clone <FTI_kuka repo> /storage/rtr-remote`.
3. Edit `RTRREMOTE.pak/config.json`: `core_host` (robot PC IP), `device`
   (gamepad event node — discover with `python rtr/remote/joystick_test.py /dev/input/eventX`),
   font/colors.
4. **Test loop**: ssh → `git -C /storage/rtr-remote pull` →
   `Tools/tg5040/RTRREMOTE.pak/launch.sh --selftest` → expect `SELFTEST PASS`.
5. Launch **RTRREMOTE** from the NextUI menu → live app (joystick + keyboard);
   **L1+R1 together quits**.

## Risks / notes

- **python-build-standalone on Trimui glibc**: the selftest is the direct check
  (it runs the bundled Python first thing).
- **SDL display grab under NextUI**: standard PAK behavior (SDL2 games run the same
  way); `fb` driver fallback if `kmsdrm` fails.
- **websockets API**: `connection.py` uses the `websockets.connect` async-context
  form (v12+); pin `websockets>=12,<14` in the PAK venv.
- **Gamepad EV_KEY codes** differ from the desktop `/dev/input/event3` default →
  config-driven `joy_map` + `joystick_test.py` for discovery.
- **`events.py` `EVENT` struct `"llHHI"`** is 64-bit only — fine for aarch64/x86_64.
- **urwid TUI regression** from the `view.py` split — covered by the regression test.
- `git`/`jq` availability on the device: `launch.sh` degrades gracefully
  (fixed `REPO` default when `jq` is absent).

## Task order

1. Extract `view.py` (pure rows/items) + refactor `tui.py` to consume it; verify
   desktop TUI unchanged.
2. `sdlapp.py`: renderer + `InputController` (keyboard + joystick + L1+R1 chord) +
   main loop + `--selftest`.
3. `config.py` extensions (theme/font/devices/repo).
4. Headless tests (`tests/test_remote_sdl.py`) + run them.
5. PAK: `launch.sh`, `config.json`, `fonts/`, `scripts/build_pak.sh`; run the build
   and verify PAK structure.
6. Document the on-device rollout (in the plan; optionally a short `pak/README.md`
   only if requested).
