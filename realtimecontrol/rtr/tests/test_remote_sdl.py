"""Headless tests for the SDL2 remote app + shared view layer.

Covers (no hardware, ``SDL_VIDEODRIVER=dummy``):

- **View builder** — mock state + zones -> expected row texts/styles; page
  switching; item labels with ``↻`` / ``→`` / ``←`` markers; disabled rows get
  the dim style; the selected row gets the highlight style.
- **SETTINGS page** — font row + calibrate row + the d-pad (ABS) and face (KEY)
  mapper rows; first-launch calibration capture (step-aware ABS/KEY), abort on
  L1/R1, and persistence.
- **Input controller** — EV_KEY face buttons map to app keys; the analog d-pad
  (EV_ABS) emits a direction on a press edge (no repeat, center = release);
  L1/R1 are global page nav (no quit chord).
- **Config** — defaults (``dpad_map`` + ``calibrated``) and the ``joy_map`` /
  ``dpad_map`` JSON round-trip (string keys -> int).
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
# SETTINGS page (page 5): items, content, activation.
# ---------------------------------------------------------------------------
def test_settings_items_font_calibrate_and_mapper_rows():
    settings = {
        "font_size": 16,
        "joy_map": {29: "enter", 310: "left", 311: "right"},
        "dpad_map": {"up": [17, -1], "down": [17, 1]},
    }
    items = view.build_settings_items(settings)
    # Font row (THEME), then the calibrate row, then the 7 mapper rows (BUTTONS).
    assert len(items) == 9
    assert items[0]["ui"] == "font_size" and items[0]["section"] == "theme"
    assert items[0]["name"] == "font size: 16"
    assert items[1]["ui"] == "calibrate" and items[1]["section"] == "buttons"
    assert items[1]["name"] == "calibrate controller"
    assert [it["app_key"] for it in items[2:]] == list(view.SETTINGS_MAP_KEYS)
    # The d-pad rows are input="abs" (analog axis); the face rows are "key".
    assert [it["input"] for it in items[2:6]] == ["abs"] * 4
    assert [it["input"] for it in items[6:]] == ["key"] * 3
    # D-pad mapper shows the current axis/sign; face mapper shows the code.
    assert items[2]["name"] == "map up: 17/-1"
    assert items[6]["name"] == "map enter: 29"
    assert items[3]["name"] == "map down: 17/1"
    assert items[5]["name"] == "map right: ?"


def test_settings_content_and_help_text():
    settings = {"font_size": 16, "joy_map": {29: "enter"},
                "dpad_map": {"up": [17, -1]}}
    rows = view.build_rows(
        {"zone": "rest"}, {"rest": {"actions": {}}}, 5, 0, {}, {}, {}, 80,
        connected=True, endpoint="ws://x", settings=settings)
    texts = [r.get("text", "") for r in rows]
    # THEME / BUTTONS section labels + the SETUP page marker.
    assert any("THEME / LAYOUT" in t for t in texts)
    assert any("BUTTONS" in t for t in texts)
    assert any("[SETUP]" in t for t in texts)
    # Page-aware help: font adjust + L1/R1 page nav (not the default help).
    help_rows = [r for r in rows if r["style"] == "help"]
    assert help_rows and "font" in help_rows[0]["text"]
    assert "L1/R1 pages" in help_rows[0]["text"]


def test_setup_page_activation_returns_none_cmds():
    rs = view.RemoteState()
    rs.settings = {"font_size": 16, "joy_map": {}, "dpad_map": {}}
    rs.page = 5
    rs.cursor = 0
    res = rs.activate()
    assert res is not None
    cmds, status = res
    assert cmds is None
    assert "font size saved" in status


def test_setup_calibrate_activation_returns_prompt():
    rs = view.RemoteState()
    rs.settings = {"font_size": 16, "joy_map": {}, "dpad_map": {}}
    rs.page = 5
    rs.cursor = 1  # the "calibrate controller" row
    res = rs.activate()
    assert res is not None
    cmds, status = res
    assert cmds is None
    assert "press UP now" in status


def test_setup_map_activation_returns_capture_status():
    rs = view.RemoteState()
    rs.settings = {"font_size": 16, "joy_map": {}, "dpad_map": {}}
    rs.page = 5
    rs.cursor = 2  # the "map up" row (after font + calibrate)
    res = rs.activate()
    assert res is not None
    cmds, status = res
    assert cmds is None
    assert "capture next press" in status


def _app_with_settings():
    app = sdlapp.SdlRemote(Config(), selftest=True)
    app._build_settings()
    return app


def test_capture_assigns_codes_and_persists():
    app = _app_with_settings()
    app._start_capture_for([("up", "key"), ("down", "key")])
    assert app._capture_active
    # A KEY press edge (code 114) maps to the current app key ("up").
    app._on_capture((1, 114, 1), 0.0)
    assert app._input.joy_map[114] == "up"
    assert app._rs.settings["joy_map"][114] == "up"
    assert app._capture_idx == 1
    # Next press maps to "down".
    app._on_capture((1, 115, 1), 0.0)
    assert app._input.joy_map[115] == "down"
    # Sequence complete -> capture off, calibrated + persisted to config.
    assert not app._capture_active
    assert app._calibrated
    assert app.config.calibrated is True


def test_capture_dpad_abs_step_captures_axis_sign():
    app = _app_with_settings()
    app._start_capture_for([("up", "abs"), ("down", "abs")])
    assert app._capture_active
    # An ABS press edge (axis 17 negative = UP) captures the axis + sign.
    app._on_capture((3, 17, 4294967295), 0.0)
    assert app._input.dpad_map["up"] == [17, -1]
    assert app.config.dpad_map["up"] == [17, -1]
    assert app._capture_idx == 1
    # Next ABS press (axis 17 positive = DOWN) captures down.
    app._on_capture((3, 17, 1), 0.0)
    assert app._input.dpad_map["down"] == [17, 1]
    assert not app._capture_active
    assert app._calibrated


def test_capture_abs_ignores_center_and_repeats():
    app = _app_with_settings()
    app._start_capture_for([("up", "abs")])
    app._on_capture((3, 17, 0), 0.0)          # center: not a press
    assert app._capture_idx == 0
    app._on_capture((3, 17, 4294967295), 0.0)  # UP press edge
    assert app._capture_idx == 1
    assert not app._capture_active
    # A repeat of the same axis/sign (holding) is ignored (no second step).
    app._start_capture_for([("up", "abs"), ("down", "abs")])
    app._on_capture((3, 17, 4294967295), 0.0)  # UP press
    assert app._capture_idx == 1
    app._on_capture((3, 17, 4294967295), 10.0) # still held: ignored
    assert app._capture_idx == 1
    assert app._capture_active


def test_capture_key_step_ignores_abs_events():
    app = _app_with_settings()
    app._start_capture_for([("enter", "key")])
    app._on_capture((1, 29, 0), 0.0)   # release edge
    app._on_capture((3, 17, 4294967295), 0.0)  # ABS event (wrong kind)
    assert app._capture_idx == 0       # nothing captured
    assert app._capture_active


def test_capture_aborts_on_l1_r1():
    app = _app_with_settings()
    app._start_capture_for([("up", "key")])
    app._on_capture((1, view.JOY_L1, 1), 0.0)
    assert not app._capture_active
    assert "aborted" in app._rs.status
    # The abort press (L1) is not assigned to the captured app key ("up");
    # L1/R1 keep their page-nav mapping only.
    assert app._input.joy_map.get(view.JOY_L1) == "left"
    assert app._input.joy_map.get(view.JOY_R1) == "right"
    # And no capture progress happened (up still unmapped).
    assert app._capture_idx == 0


def test_set_joy_map_replaces_prior_code():
    app = _app_with_settings()
    app._set_joy_map(114, "up")
    app._set_joy_map(115, "up")
    # Only the latest code maps to "up" (114 replaced).
    assert app._input.joy_map[115] == "up"
    assert 114 not in app._input.joy_map


def test_setup_page_l1_r1_navigates_not_font():
    # The trap: on SETUP with the cursor on the font row, the shoulders must
    # still navigate away (global page nav), not adjust the font size.
    app = _app_with_settings()
    app._rs.set_page(5)        # SETUP (last page)
    app._rs.cursor = 0         # on the font-size row
    fs_before = app._rs.settings["font_size"]
    app._handle_key("page_left")   # L1
    assert app._rs.page == 4
    assert app._rs.settings["font_size"] == fs_before
    # From an earlier page, R1 (page_right) navigates forward.
    app._rs.set_page(0)
    app._rs.cursor = 0
    app._handle_key("page_right")  # R1
    assert app._rs.page == 1


def test_setup_dpad_left_right_still_font_on_font_row():
    # The d-pad left/right is the LOCAL key: on the font row it adjusts the
    # font size (and stays on the page).
    app = _app_with_settings()
    app._rs.set_page(5)
    app._rs.cursor = 0         # font row
    fs_before = app._rs.settings["font_size"]
    app._handle_key("right")   # d-pad right
    assert app._rs.settings["font_size"] == fs_before + 2
    assert app._rs.page == 5   # still on SETUP


def test_setup_dpad_left_right_pages_when_not_on_font_row():
    # Off the font row, the d-pad left/right falls back to page navigation
    # (a second, d-pad-based way out -- the shoulders always work too).
    app = _app_with_settings()
    app._rs.set_page(5)
    app._rs.cursor = 1         # a map row, not the font row
    app._handle_key("left")    # d-pad left
    assert app._rs.page == 4


# ---------------------------------------------------------------------------
# Input controller (joystick -> app keys, d-pad ABS, L1/R1 page nav).
# ---------------------------------------------------------------------------
def test_input_maps_face_buttons():
    ic = sdlapp.InputController()
    # The face buttons are digital EV_KEY events (A=29, B=30, X=307).
    assert ic.on_joystick_event((1, 29, 1), 0.0) == ("key", "enter")
    assert ic.on_joystick_event((1, 30, 1), 0.0) == ("key", "q")
    assert ic.on_joystick_event((1, 8, 1), 0.0) == ("key", "h")


def test_input_dpad_is_analog_abs_axis():
    ic = sdlapp.InputController()
    # The d-pad is an analog axis (EV_ABS, type 3), not a digital key:
    # axis 17 negative = UP, positive = DOWN; axis 16 negative = LEFT, positive = RIGHT.
    up = ic.on_joystick_event((3, 17, 4294967295), 0.0)   # axis 17 negative
    assert up == ("key", "up")
    # Holding it does not repeat; returning to center (release) emits nothing.
    assert ic.on_joystick_event((3, 17, 4294967295), 10.0) is None  # still held
    assert ic.on_joystick_event((3, 17, 0), 20.0) is None           # center
    down = ic.on_joystick_event((3, 17, 1), 30.0)                 # axis 17 positive
    assert down == ("key", "down")
    left = ic.on_joystick_event((3, 16, 4294901759), 40.0)       # axis 16 negative
    assert left == ("key", "left")
    right = ic.on_joystick_event((3, 16, 1), 50.0)               # axis 16 positive
    assert right == ("key", "right")


def test_input_l1_r1_are_global_page_nav():
    # L1/R1 are reserved for GLOBAL page navigation (distinct from the d-pad
    # left/right): a single shoulder press pages and is never a mappable button.
    ic = sdlapp.InputController()
    assert ic.on_joystick_event((1, view.JOY_L1, 1), 0.0) == ("key", "page_left")
    ic2 = sdlapp.InputController()
    assert ic2.on_joystick_event((1, view.JOY_R1, 1), 0.0) == ("key", "page_right")


def test_input_home_is_the_quit():
    ic = sdlapp.InputController(quit_codes=[172])
    assert ic.on_joystick_event((1, 172, 1), 0.0) == ("quit",)


def test_input_ignores_releases():
    ic = sdlapp.InputController()
    assert ic.on_joystick_event((1, 29, 0), 0.0) is None   # KEY release edge
    assert ic.on_joystick_event((3, 17, 0), 0.0) is None   # d-pad center


# ---------------------------------------------------------------------------
# Config.
# ---------------------------------------------------------------------------
def test_config_defaults():
    c = Config()
    assert c.repo == "/storage/rtr-remote"
    assert c.width == 320
    assert c.screen_height == 240
    # The d-pad defaults (analog axis/sign) and the un-calibrated flag.
    assert c.dpad_map["up"] == [17, -1]
    assert c.dpad_map["right"] == [16, 1]
    assert c.calibrated is False
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


def test_config_dpad_map_json_round_trip(tmp_path):
    c = Config()
    c.dpad_map["up"] = [17, -1]
    c.calibrated = True
    p = tmp_path / "cfg.json"
    c.to_json(p)
    # The d-pad map persists (axis/sign) and the calibrated flag survives.
    c2 = Config.from_json(p)
    assert c2.dpad_map["up"] == [17, -1]
    assert c2.calibrated is True


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
    assert data["width"] == 1024 and data["screen_height"] == 768
    assert data["font"].endswith("PressStart2P.ttf")
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
    # "PressStart2P.ttf" is not in the PAK fonts dir, so it falls back to the
    # module dir (rtr/remote/), where it ships with the code.
    assert os.path.basename(resolved) == "PressStart2P.ttf"
    assert "rtr" in resolved and "remote" in resolved


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
