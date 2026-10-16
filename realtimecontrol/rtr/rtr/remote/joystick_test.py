#!/usr/bin/env python3
"""Read raw Linux input events and print them with human names.

KEY events (type=1) are buttons; ABS events (type=3) are axes (the d-pad is
reported here as two axes with signed -1/0/1 values). Prints a running
"state" line of the active (non-zero) ABS axes so a d-pad capture is readable.
"""
import struct
import sys

device = sys.argv[1] if len(sys.argv) > 1 else "/dev/input/event3"

# Linux input_event: timeval + type + code + value
EVENT = struct.Struct("llHHI")

KEY_NAMES = {
    15: "UP", 16: "DOWN", 17: "LEFT", 18: "RIGHT",
    29: "A/enter", 30: "B/q", 8: "X/h",
    172: "HOME", 304: "A", 305: "B", 307: "X", 308: "Y",
    310: "L1", 311: "R1",
}
ABS_NAMES = {
    0: "X", 1: "Y", 2: "Z", 3: "RX", 4: "RY",
    5: "THROTTLE", 16: "WHEEL", 17: "TRACKBALL",
}


def signed(v: int) -> int:
    """Interpret a 16-bit ABS value as signed (4294967295 == -1)."""
    return v - 0x10000 if v > 0x7FFF else v


def main() -> None:
    with open(device, "rb") as f:
        print("Reading", device, "- press controls; Ctrl-C exits.\n")
        state = {}  # abs code -> last signed value
        while True:
            data = f.read(EVENT.size)
            if len(data) != EVENT.size:
                break
            _sec, _usec, et, code, val = EVENT.unpack(data)
            if et == 1:
                name = KEY_NAMES.get(code, f"KEY{code}")
                print(f"KEY  code={code:3d} ({name:9}) press={val}")
            elif et == 3:
                s = signed(val)
                state[code] = s
                name = ABS_NAMES.get(code, f"ABS{code}")
                active = {ABS_NAMES.get(c, f"ABS{c}"): v
                           for c, v in sorted(state.items()) if v != 0}
                print(f"ABS  code={code:3d} ({name:9}) val={s:4}   "
                      f"active={active}")


if __name__ == "__main__":
    main()
