"""Test: Perform config loader (rtr/perform.py).

Covers:
- builtin_perform_config returns the fallback shape.
- validate_perform_config fills in defaults for missing keys.
- validate_perform_config rejects bad types (non-int base_beats, non-positive bpm,
  non-positive velocity, non-int multipliers).
- load_perform_config reads a valid file and falls back on a missing file.

All tests are headless: no robot, no hardware.

Run directly (no pytest needed)::

    python tests/test_perform_config.py

or under pytest::

    pytest tests/test_perform_config.py
"""

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rtr.perform import (
    builtin_perform_config,
    load_perform_config,
    validate_perform_config,
)


def test_builtin_perform_config_shape():
    """The built-in config has all the expected fields with sane defaults."""
    cfg = builtin_perform_config()
    assert cfg["default_base_beats"] == 4
    assert cfg["internal_bpm"] == 130.0
    assert cfg["clock_timeout"] == 1.0
    assert cfg["velocity_max_deg_s"] == 30.0
    assert cfg["multipliers"] == [0.0625, 0.125, 0.25, 0.5, 1, 2, 4, 8, 16]


def test_builtin_returns_copy():
    """builtin_perform_config returns a fresh copy (mutations don't leak)."""
    a = builtin_perform_config()
    a["internal_bpm"] = 999.0
    b = builtin_perform_config()
    assert b["internal_bpm"] == 130.0


def test_validate_fills_defaults():
    """An empty dict validates to the built-in defaults."""
    cfg = validate_perform_config({})
    assert cfg["default_base_beats"] == 4
    assert cfg["internal_bpm"] == 130.0
    assert cfg["clock_timeout"] == 1.0
    assert cfg["velocity_max_deg_s"] == 30.0
    assert cfg["multipliers"] == [0.0625, 0.125, 0.25, 0.5, 1, 2, 4, 8, 16]


def test_validate_partial():
    """Only the provided fields change; the rest keep their defaults."""
    cfg = validate_perform_config({"internal_bpm": 120.0, "multipliers": [1, 2, 4]})
    assert cfg["internal_bpm"] == 120.0
    assert cfg["multipliers"] == [1, 2, 4]
    assert cfg["default_base_beats"] == 4
    assert cfg["velocity_max_deg_s"] == 30.0


def test_validate_rejects_non_object():
    """A non-dict raises ValueError."""
    try:
        validate_perform_config("not-a-dict")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_validate_rejects_bad_base_beats():
    """default_base_beats must be a non-negative int (bools and negatives rejected)."""
    for bad in (-1, 1.5, True, "4"):
        try:
            validate_perform_config({"default_base_beats": bad})
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_validate_rejects_bad_internal_bpm():
    """internal_bpm must be a positive number."""
    for bad in (0, -120.0, "fast"):
        try:
            validate_perform_config({"internal_bpm": bad})
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_validate_rejects_bad_clock_timeout():
    """clock_timeout must be a non-negative number."""
    for bad in (-1.0, "never"):
        try:
            validate_perform_config({"clock_timeout": bad})
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_validate_rejects_bad_velocity():
    """velocity_max_deg_s must be a positive number."""
    for bad in (0, -360.0, "fast"):
        try:
            validate_perform_config({"velocity_max_deg_s": bad})
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_validate_rejects_bad_multipliers():
    """multipliers must be a non-empty list of positive numbers."""
    for bad in ([], [1, 2, -4], [0], [-4], "1x", None, [1, True]):
        try:
            validate_perform_config({"multipliers": bad})
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_validate_accepts_fractional_multipliers():
    """multipliers may be fractional (the divide steps: 1/16, 1/8, …)."""
    cfg = validate_perform_config({"multipliers": [0.0625, 0.125, 0.25, 0.5, 1, 2, 4, 8, 16]})
    assert cfg["multipliers"] == [0.0625, 0.125, 0.25, 0.5, 1, 2, 4, 8, 16]


def test_load_from_file():
    """load_perform_config reads and validates a file on disk."""
    data = {"internal_bpm": 100.0, "multipliers": [1, 2, 4, 8, 16]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        cfg = load_perform_config(path)
        assert cfg["internal_bpm"] == 100.0
        assert cfg["default_base_beats"] == 4  # filled from default
    finally:
        os.unlink(path)


def test_load_missing_file_raises():
    """A missing file raises OSError (the caller falls back to built-in)."""
    try:
        load_perform_config("/nonexistent/perform.json")
        assert False, "expected OSError"
    except OSError:
        pass


def main():
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {name}: {e}")
    if failures:
        print(f"\n{failures} TEST(S) FAILED")
        return 1
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
