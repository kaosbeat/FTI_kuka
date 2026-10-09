"""Headless tests for the SDL2 remote app + shared view layer.

Covers (no hardware, ``SDL_VIDEODRIVER=dummy``):

- **View builder** — mock state + zones -> expected row texts/styles; page
  switching; item labels with ``↻`` / ``→`` / ``←`` markers; disabled rows get
  the dim style; the selected row gets the highlight style.
- **Chord detector** — L1+R1 within the window -> quit; outside -> page nav;
  release resets state.
- **Input controller** — EV_KEY joystick events map to app keys + the quit chord.
- **Config** — defaults + the ``joy_map`` JSON round-trip (string keys -> int).
- **Selftest** — run the full ``--selftest`` in a subprocess, assert exit 0 and
  ``SELFTEST PASS``.
- **urwid regression** — :mod:`rtr.remote.tui` still builds rows via
  :mod:`rtr.remote.view` headlessly.

Run:  ``pytest tests/test_remote_sdl.py``   (from ``realtimecontrol/rtr``)
pygame / urwid are ``importorskip``'d; install with
``pip install pygame websockets pytest urwid``.
"""

import json
import os
import subprocess
import sys

# Bootstrap the package root so ``rtr.*`` imports resolve under pytest.
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

import pytest

from rtr.remote import sdlapp, view
from rtr.remote.config import Config


# ---------------------------------------------------------------------------
# Mock zone graph (a small three-zone loop with a disabled action).
# ---------------------------------------------------------------------------
ZONES = {
    "rest": {
        "actions": {
            "breathe": {"loop": True, "next": {"zone": "patrol"}},
            "shimmer": {"loop": False},
        },
    },
    "patrol": {
        "actions": {
            "scan": {"loop": True, "next": {"zone": "rest"}},
        },
    },
    "dormant": {
        "actions": {
            "settle": {},
            "hold": {"enabled": False},
        },
    },
    "dock": {
        "actions": {
            "return_home": {"loop": False, "next": {"zone": "rest"}},
        },
    },
}


def make_state():
    rs = view.RemoteState()
    rs.apply_zones(ZONES)
    rs.apply_state({"zone": "rest", "mode": "wander", "action": "breathe",
                    "moving": False, "speed": 0})
    rs.cursor = 0
    return rs


# ---------------------------------------------------------------------------
# View builder.
# ---------------------------------------------------------------------------
def test_view_build_rows_bar_and_conn():
    rs = make_state()
    rows = rs.build_rows(80, connected=True, endpoint="ws://core:8765")
    styles = [r["style"] for r in rows]
    assert styles[0] == "bar"
    assert "RTR REMOTE" in rows[0]["text"]
    # Connected -> a green conn line.
    assert "conn_ok" in styles
    # The page selector line lists all pages and marks the current one.
    page_rows = [r for r in rows if r["style"] == "page"]
    assert page_rows and "NAV" in page_rows[0]["text"]


def test_view_page_switching_marks_current_page():
    rs = make_state()
    for i, name in enumerate(view.PAGES):
        rs.page = i
        rows = rs.build_rows(80, connected=True, endpoint="ws://x")
        page_row = next(r for r in rows if r["style"] == "page")
        assert f"[{name}]" in page_row["text"]


def test_item_label_markers():
    items = view.build_items(ZONES, "rest")
    labels = {it["name"]: view.item_label(it) for it in items}
    # Loops -> ↻, next zone -> → patrol.
    assert "↻" in labels["breathe"]
    assert "→ patrol" in labels["breathe"]
    # No loop, no next -> plain name.
    assert labels["shimmer"] == "shimmer"
    # A forward zone row has no back marker.
    assert labels["patrol"] == "patrol"


def test_item_label_back_marker():
    items = view.build_items(ZONES, "rest")
    # dock points to rest (dock.return_home.next -> rest) but rest does not
    # reach dock, so dock is a reverse-only target: back=True, ← marker.
    back = next(it for it in items if it["name"] == "dock" and it.get("back"))
    assert "←" in view.item_label(back)


def test_disabled_row_gets_dim_style():
    rs = make_state()
    rs.apply_state({"zone": "dormant", "mode": "idle", "action": "settle",
                    "moving": False, "speed": 0})
    rs.page = 0
    rs.cursor = 0
    rows = rs.build_rows(80, connected=True, endpoint="ws://x")
    # The disabled "hold" action (index 1, not at the cursor) is dimmed.
    assert any(r["style"] == "item_dim" and "hold" in r["text"] for r in rows)


def test_selected_row_gets_highlight_style():
    rs = make_state()
    rs.page = 0
    rs.cursor = 0
    rows = rs.build_rows(80, connected=True, endpoint="ws://x")
    sel = [r for r in rows if r["style"] == "item_sel"]
    assert len(sel) == 1
    assert "breathe" in sel[0]["text"]


# ---------------------------------------------------------------------------
# Chord detector (pure).
# ---------------------------------------------------------------------------
def test_chord_within_window_fires():
    d = sdlapp.ChordDetector(200)
    assert d.press(view.JOY_L1, 0.0) is False     # first press
    assert d.press(view.JOY_R1, 50.0) is True     # within 200 ms -> chord


def test_chord_outside_window_does_not_fire():
    d = sdlapp.ChordDetector(200)
    d.press(view.JOY_L1, 0.0)
    assert d.press(view.JOY_R1, 500.0) is False   # too slow -> page nav only


def test_chord_release_resets_state():
    d = sdlapp.ChordDetector(200)
    d.press(view.JOY_L1, 0.0)
    d.release(view.JOY_L1)
    # After release, L1 is no longer tracked; pressing R1 cannot complete a chord.
    assert d.press(view.JOY_R1, 100.0) is False


# ---------------------------------------------------------------------------
# Input controller (joystick -> app keys + chord).
# ---------------------------------------------------------------------------
def test_input_maps_dpad_and_buttons():
    ic = sdlapp.InputController()
    assert ic.on_joystick_event((1, 15, 1), 0.0) == ("key", "up")
    assert ic.on_joystick_event((1, 16, 1), 0.0) == ("key", "down")
    assert ic.on_joystick_event((1, 17, 1), 0.0) == ("key", "left")
    assert ic.on_joystick_event((1, 18, 1), 0.0) == ("key", "right")
    assert ic.on_joystick_event((1, 29, 1), 0.0) == ("key", "enter")
    assert ic.on_joystick_event((1, 8, 1), 0.0) == ("key", "h")


def test_input_ignores_non_key_events_and_releases():
    ic = sdlapp.InputController()
    assert ic.on_joystick_event((3, 15, 1), 0.0) is None   # ABS event
    assert ic.on_joystick_event((1, 15, 0), 0.0) is None   # release edge


def test_input_chord_reports_quit():
    ic = sdlapp.InputController()
    ic.on_joystick_event((1, view.JOY_L1, 1), 0.0)
    assert ic.on_joystick_event((1, view.JOY_R1, 1), 40.0) == ("quit",)


# ---------------------------------------------------------------------------
# Config.
# ---------------------------------------------------------------------------
def test_config_defaults():
    c = Config()
    assert c.repo == "/storage/rtr-remote"
    assert c.width == 320
    assert c.screen_height == 240
    assert c.chord_window_ms == 200
    assert c.joy_map[view.JOY_L1] == "left"
    assert c.theme.get("bar") == "#ffff00"


def test_config_joy_map_json_round_trip(tmp_path):
    c = Config()
    p = tmp_path / "cfg.json"
    c.to_json(p)
    # Raw JSON has string keys.
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert all(isinstance(k, str) for k in raw["joy_map"])
    # Reloaded config normalises keys back to int.
    c2 = Config.from_json(p)
    assert c2.joy_map[view.JOY_R1] == "right"


# ---------------------------------------------------------------------------
# PAK config + font resolution.
# ---------------------------------------------------------------------------
def _pak_config_path():
    return os.path.join(_PKG_ROOT, "pak", "RTRREMOTE.pak", "config.json")


def test_pak_config_is_valid_and_loads():
    p = _pak_config_path()
    assert os.path.exists(p), f"missing PAK config: {p}"
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    # Key PAK fields present and sane.
    assert data["repo"] == "/storage/rtr-remote"
    assert data["width"] == 320 and data["screen_height"] == 240
    assert data["font"].endswith("DejaVuSansMono.ttf")
    assert "310" in data["joy_map"] and "311" in data["joy_map"]
    # Loads into a Config with int-normalised joy_map keys.
    c = Config.from_json(os.path.abspath(p))
    assert c.joy_map[view.JOY_L1] == "left"
    assert c.joy_map[view.JOY_R1] == "right"


def test_font_resolves_against_config_dir():
    pygame = pytest.importorskip("pygame")
    pygame.font.init()
    p = _pak_config_path()
    import argparse
    from rtr.remote import config as rcfg
    ap = argparse.ArgumentParser()
    rcfg._add_flags(ap)
    cfg = rcfg.from_args(ap.parse_args(["--config", os.path.abspath(p)]))
    app = sdlapp.SdlRemote(cfg)
    resolved = app._resolve_font()
    assert resolved and os.path.exists(resolved)
    # The relative "fonts/..." path resolves into the PAK fonts dir.
    assert "RTRREMOTE.pak" in resolved
    assert os.path.basename(resolved) == "DejaVuSansMono.ttf"


# ---------------------------------------------------------------------------
# Selftest (subprocess, dummy driver).
# ---------------------------------------------------------------------------
def test_selftest_subprocess():
    pygame = pytest.importorskip("pygame")  # noqa: F841 - ensure available
    env = dict(os.environ)
    env["SDL_VIDEODRIVER"] = "dummy"
    env["PYTHONPATH"] = _PKG_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "rtr.remote.sdlapp", "--selftest"],
        capture_output=True, text=True, env=env, timeout=90)
    assert r.returncode == 0, f"rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "SELFTEST PASS" in r.stdout


# ---------------------------------------------------------------------------
# urwid regression: tui.py still builds rows via view.py headlessly.
# ---------------------------------------------------------------------------
def test_tui_builds_rows_via_view():
    urwid = pytest.importorskip("urwid")
    from rtr.remote import tui
    app = tui.RemoteTUI(Config())
    app._rs = make_state()
    app._rs.page = 0
    top = app._build_top()
    assert isinstance(top, urwid.Pile)
    # A full screen of rows (bar, conn, page, nav content, status, help).
    assert len(top.contents) >= 6
    # Every page still renders a non-empty Pile through the shared builder.
    for i in range(len(view.PAGES)):
        app._rs.page = i
        assert len(app._build_top().contents) >= 6


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
