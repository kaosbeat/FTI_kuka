# remote (top-level)

This directory is **not** the remote app. The canonical remote package lives in
[`rtr/remote/`](../rtr/remote/README.md) (desktop TUI + Trimui SDL app, shared
`connection.py` / `view.py` / `config.py`).

This folder holds:

| Path | What |
|---|---|
| `tui.py` | Thin launcher for the **desktop** TUI: bootstraps the package root onto `sys.path` and re-exports `rtr.remote.tui.run`. The `python remote/tui.py [flags]` invocation keeps working. |
| `deploy.md` | Trimui / R36S deploy runbook: PAK layout, shipping app code to the device, on-device selftest, and the socat forwarding for the robot's network. |
| `yellowbrick/` | R36S/Trimui **hardware** assets: device-tree blobs (`dtbs/`) and the MIPI panel overlay (`overlays/mipi-panel.dtbo`). Not used by the Python app. |

## Running the desktop TUI from here

From the `realtimecontrol/rtr` root:

```bash
# the launcher in this directory
python remote/tui.py --core-host 127.0.0.1 --core-port 8765

# the canonical module directly (same thing)
python -m rtr.remote.tui --core-host 127.0.0.1 --core-port 8765
```

`urwid` must be installed (`pip install urwid`). Keyboard: `↑`/`↓` move,
`enter` activate, `←`/`→` pages, `q`/`escape` quit.

**Do not** run the canonical file directly (`python rtr/remote/tui.py`): its
module-level relative imports fail before any bootstrap can run. Use one of the
two commands above.

## Config

The live config is [`rtr/remote/config.json`](../rtr/remote/config.json)
(base values) — pass it with `--config rtr/remote/config.json`; explicit flags
(`--core-host` / `--core-port` / `--http-port` / ...) override it.

The old `remote/config.py` + `remote/config.json.example` (a legacy scaffold
with `localhost:5000` / `js0` defaults) were removed; all configuration now
lives in `rtr/remote/config.py` + `rtr/remote/config.json`.

## Trimui device

The device does **not** run anything from this directory. The PAK
(`pak/RTRREMOTE.pak/launch.sh`) runs `rtr/remote/mainmenu.py` → `sdlapp.py`
from the on-device repo (`/storage/rtr-remote`, the shipped `rtr/` tree). See
`deploy.md` and the [`rtr/remote` README](../rtr/remote/README.md#deploying-the-trimui-pak).
