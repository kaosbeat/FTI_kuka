"""Tests for the runtime zone-groups feature.

Covers:
- ``find_safe_path_to_any()`` in ``rtr/flow.py`` (multi-target BFS over (zone, action) nodes);
- ``Brain`` group enable/clear: ``Cmd.ENABLE_GROUP`` finds a safe path to the group,
  stores the nav, switches to auto mode; ``Cmd.CLEAR_GROUP`` clears state;
- The manual-mode interlock: setting manual while a group is active clears the group;
- Group-restricted autonomy: ``_allowed_zones()`` returns only the active group's zones;
- MIDI group dispatch: a learned group key on the groups' in_port submits ENABLE_GROUP;
- MIDI group learn capture: arming a group learn captures on the groups' in_port.

Run directly (no pytest needed)::

    python tests/test_zone_groups.py

or under pytest::

    pytest tests/test_zone_groups.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ``rtr.core`` must load before ``rtr.brain.brain`` (a pre-existing circular
# import). Loading the package first resolves the cycle.
import rtr.core  # noqa: F401

from rtr.state.zones import Zones, validate_state_data
from rtr.state.machine import StateMachine
from rtr.brain.brain import Brain
from rtr.brain.config import validate_brain_config
from rtr.core.bus import StateBus
from rtr.core.commands import Cmd, Command, Event
from rtr.flow import find_safe_path_to_any
from rtr.io.midi import MidiInput, builtin_midi_data, validate_midi_data


# ---------------------------------------------------------------------------
# Zone table: three zones (a, b, c) with actions and next links.
# Zone a has two actions (a1, a2); zone b has one (b1); zone c has one (c1).
# Navigation: a1 -> b1, b1 -> c1, c1 -> a2.
# ---------------------------------------------------------------------------

def _zones():
    data = validate_state_data({"zones": {
        "a": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "a1": {
                    "poses": [[0] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                    "next": {"zone": "b", "action": "b1"},
                },
                "a2": {
                    "poses": [[1] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                },
            },
        },
        "b": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "b1": {
                    "poses": [[2] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                    "next": {"zone": "c", "action": "c1"},
                },
            },
        },
        "c": {
            "enabled": True,
            "safezone": [[-100, 100]] * 6,
            "speed": 20,
            "actions": {
                "c1": {
                    "poses": [[3] * 6],
                    "variable_axes": [],
                    "behavior": "calm",
                    "speed": 20,
                    "loop": True,
                    "next": {"zone": "a", "action": "a2"},
                },
            },
        },
    }})
    return Zones(data["zones"])


def _brain_config(groups=None):
    if groups is None:
        groups = {
            "g1": {"zones": ["a", "b"], "behaviors": ["calm"], "enabled": True},
            "g2": {"zones": ["c"], "behaviors": ["calm"], "enabled": True},
            "home": {"zones": [], "behaviors": [], "enabled": False},
            "hunt": {"zones": [], "behaviors": [], "enabled": False},
            "facefocus": {"zones": [], "behaviors": [], "enabled": False},
        }
    return validate_brain_config({
        "autonomy": {
            "enabled": True,
            "dwell_s": 30.0,
            "events": {"midi": False, "camera": False, "websocket": False},
        },
        "zone_groups": groups,
    })


def _make_brain(machine=None):
    zones = _zones()
    if machine is None:
        machine = StateMachine(zones, initial_zone="a")
    cfg = _brain_config()
    return Brain(machine, camera=None, config_loader=lambda: cfg), machine


# ---------------------------------------------------------------------------
# find_safe_path_to_any: multi-target BFS.
# ---------------------------------------------------------------------------

def test_find_safe_path_to_any_direct_target():
    """Starting at a node that is itself a target returns [start]."""
    z = _zones()
    table = z.table()
    targets = [("a", "a1")]
    path = find_safe_path_to_any(table, "a", "a1", targets)
    assert path == [("a", "a1")]


def test_find_safe_path_to_any_one_hop():
    """a1 -> b1 is a direct next link; b1 is a target."""
    z = _zones()
    table = z.table()
    targets = [("b", "b1")]
    path = find_safe_path_to_any(table, "a", "a1", targets)
    assert path is not None
    assert path[0] == ("a", "a1")
    assert path[-1] == ("b", "b1")
    assert len(path) == 2


def test_find_safe_path_to_any_two_hop():
    """a1 -> b1 -> c1: a two-hop path to c1."""
    z = _zones()
    table = z.table()
    targets = [("c", "c1")]
    path = find_safe_path_to_any(table, "a", "a1", targets)
    assert path is not None
    assert path[0] == ("a", "a1")
    assert path[-1] == ("c", "c1")
    assert len(path) == 3


def test_find_safe_path_to_any_no_path():
    """An unreachable target returns None."""
    z = _zones()
    table = z.table()
    targets = [("nonexistent", "x")]
    path = find_safe_path_to_any(table, "a", "a1", targets)
    assert path is None


def test_find_safe_path_to_any_multiple_targets():
    """When multiple targets are reachable, the shortest path wins."""
    z = _zones()
    table = z.table()
    targets = [("c", "c1"), ("b", "b1")]
    path = find_safe_path_to_any(table, "a", "a1", targets)
    assert path is not None
    # b1 is closer (1 hop) than c1 (2 hops), so the path should end at b1.
    assert path[-1] == ("b", "b1")
    assert len(path) == 2


def test_find_safe_path_to_any_disabled_zone_excluded():
    """A zone with enabled=False is not a valid target."""
    z = _zones()
    table = z.table()
    # Disable zone c in the table.
    table["c"]["enabled"] = False
    targets = [("c", "c1")]
    path = find_safe_path_to_any(table, "a", "a1", targets)
    assert path is None


# ---------------------------------------------------------------------------
# Brain group enable / clear.
# ---------------------------------------------------------------------------

def _setup_brain_at(brain, machine, zone="a", action="a1"):
    """Set the machine's current zone/action for the test."""
    machine.current_zone = zone
    machine.current_action = action


def test_enable_group_finds_path_and_sets_state():
    """ENABLE_GROUP switches to auto, finds a path, and sets active_group."""
    brain, machine = _make_brain()
    _setup_brain_at(brain, machine, "a", "a1")
    brain.on_command(Command(cmd=Cmd.ENABLE_GROUP, payload={"group": "g2"}), [0.0] * 6)
    assert brain.active_group == "g2"
    assert len(brain.group_nav) > 0
    assert brain.group_nav_index == 0


def test_enable_group_invalid_name_clears():
    """Enabling a non-existent group reverts active_group to None."""
    brain, machine = _make_brain()
    _setup_brain_at(brain, machine, "a", "a1")
    brain.on_command(Command(cmd=Cmd.ENABLE_GROUP, payload={"group": "nonexistent"}), [0.0] * 6)
    assert brain.active_group is None
    assert len(brain.group_nav) == 0


def test_clear_group_resets_state():
    """CLEAR_GROUP resets active_group, group_nav, and group_nav_index."""
    brain, machine = _make_brain()
    _setup_brain_at(brain, machine, "a", "a1")
    brain.on_command(Command(cmd=Cmd.ENABLE_GROUP, payload={"group": "g2"}), [0.0] * 6)
    assert brain.active_group == "g2"
    brain.on_command(Command(cmd=Cmd.CLEAR_GROUP, payload={}), [0.0] * 6)
    assert brain.active_group is None
    assert len(brain.group_nav) == 0
    assert brain.group_nav_index == 0


# ---------------------------------------------------------------------------
# Manual-mode interlock.
# ---------------------------------------------------------------------------

def test_manual_mode_clears_active_group():
    """Setting control mode to manual while a group is active clears the group."""
    brain, machine = _make_brain()
    _setup_brain_at(brain, machine, "a", "a1")
    brain.on_command(Command(cmd=Cmd.ENABLE_GROUP, payload={"group": "g2"}), [0.0] * 6)
    assert brain.active_group == "g2"
    brain.on_command(Command(cmd=Cmd.SET_CONTROL_MODE, payload={"mode": "manual"}), [0.0] * 6)
    assert brain.active_group is None


def test_auto_mode_preserves_active_group():
    """Setting control mode to auto while a group is active keeps the group."""
    brain, machine = _make_brain()
    _setup_brain_at(brain, machine, "a", "a1")
    brain.on_command(Command(cmd=Cmd.ENABLE_GROUP, payload={"group": "g2"}), [0.0] * 6)
    assert brain.active_group == "g2"
    brain.on_command(Command(cmd=Cmd.SET_CONTROL_MODE, payload={"mode": "auto"}), [0.0] * 6)
    assert brain.active_group == "g2"


# ---------------------------------------------------------------------------
# Group-restricted autonomy.
# ---------------------------------------------------------------------------

def test_allowed_zones_returns_group_zones_when_active():
    """When a group is active, _allowed_zones returns only that group's zones."""
    brain, machine = _make_brain()
    _setup_brain_at(brain, machine, "a", "a1")
    brain.on_command(Command(cmd=Cmd.ENABLE_GROUP, payload={"group": "g1"}), [0.0] * 6)
    allowed = brain._allowed_zones()
    assert set(allowed) == {"a", "b"}


def test_allowed_zones_returns_enabled_zones_when_no_group():
    """When no group is active, _allowed_zones returns all enabled zones."""
    brain, machine = _make_brain()
    allowed = brain._allowed_zones()
    assert "a" in allowed
    assert "b" in allowed
    assert "c" in allowed


# ---------------------------------------------------------------------------
# autonomy_info exposes active_group.
# ---------------------------------------------------------------------------

def test_autonomy_info_includes_active_group():
    """autonomy_info() returns the active_group field."""
    brain, machine = _make_brain()
    _setup_brain_at(brain, machine, "a", "a1")
    info = brain.autonomy_info()
    assert "active_group" in info
    assert info["active_group"] is None
    brain.on_command(Command(cmd=Cmd.ENABLE_GROUP, payload={"group": "g1"}), [0.0] * 6)
    info = brain.autonomy_info()
    assert info["active_group"] == "g1"


# ---------------------------------------------------------------------------
# MIDI group dispatch.
# ---------------------------------------------------------------------------

def test_midi_group_dispatch_sends_enable_group():
    """A learned group key on the groups' in_port submits ENABLE_GROUP."""
    bus = StateBus()
    midi = MidiInput(bus, in_port=None, enabled=False)
    # Set up a group mapping.
    midi._data = {
        "enabled": True,
        "zones": {},
        "nav": {"in_port": None},
        "capture": {"in_port": None, "key": None},
        "groups": {"in_port": None, "g1": "note:0:60"},
    }
    # Simulate a note-on on the default port (in_port=None → default).
    # Feed the message directly (bypassing rtmidi).
    msg = [0x90, 60, 100]  # note-on ch0, note 60, velocity 100
    midi._on_message(None, (msg, 0.0))
    # Read back the command from the bus.
    cmds = list(bus.drain_commands())
    assert any(c.cmd == Cmd.ENABLE_GROUP and c.payload.get("group") == "g1"
               for c in cmds)


def test_midi_group_dispatch_port_scoped():
    """A group key on the wrong port is not dispatched."""
    bus = StateBus()
    midi = MidiInput(bus, in_port=None, enabled=False)
    midi._data = {
        "enabled": True,
        "zones": {},
        "nav": {"in_port": None},
        "capture": {"in_port": None, "key": None},
        "groups": {"in_port": 5, "g1": "note:0:60"},
    }
    # Feed the message on port 0 (not the groups' port 5).
    msg = [0x90, 60, 100]
    midi._on_message(0, (msg, 0.0))
    cmds = list(bus.drain_commands())
    assert not any(c.cmd == Cmd.ENABLE_GROUP for c in cmds)


def test_midi_group_learn_capture():
    """Arming a group learn captures the key and submits MIDI_LEARN with group."""
    bus = StateBus()
    midi = MidiInput(bus, in_port=None, enabled=False)
    midi._data = {
        "enabled": True,
        "zones": {},
        "nav": {"in_port": None},
        "capture": {"in_port": None, "key": None},
        "groups": {"in_port": None, "g1": None},
    }
    midi.start_learn(group="g1")
    assert midi.is_learning
    msg = [0x90, 64, 100]  # note-on ch0, note 64
    midi._on_message(None, (msg, 0.0))
    cmds = list(bus.drain_commands())
    learn_cmds = [c for c in cmds if c.cmd == Cmd.MIDI_LEARN]
    assert len(learn_cmds) == 1
    assert learn_cmds[0].payload.get("group") == "g1"
    assert learn_cmds[0].payload.get("key") == "note:0:64"
    assert not midi.is_learning


# ---------------------------------------------------------------------------
# MIDI data validation for the groups section.
# ---------------------------------------------------------------------------

def test_validate_midi_data_accepts_groups():
    """The groups section with valid keys passes validation."""
    data = {
        "enabled": True,
        "zones": {},
        "nav": {"in_port": None},
        "capture": {"in_port": None, "key": None},
        "groups": {"in_port": None, "g1": "note:0:60", "g2": "cc:7:42"},
    }
    validate_midi_data(data)  # must not raise


def test_validate_midi_data_rejects_bad_group_key():
    """A group with an invalid key fails validation."""
    data = {
        "enabled": True,
        "zones": {},
        "nav": {"in_port": None},
        "capture": {"in_port": None, "key": None},
        "groups": {"in_port": None, "g1": "badkey"},
    }
    try:
        validate_midi_data(data)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_builtin_midi_data_has_groups():
    """builtin_midi_data() includes a groups section with in_port."""
    data = builtin_midi_data()
    assert "groups" in data
    assert data["groups"]["in_port"] is None


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
