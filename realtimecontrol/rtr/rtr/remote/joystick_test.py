#!/usr/bin/env python3
import struct
import sys

device = sys.argv[1] if len(sys.argv) > 1 else "/dev/input/event3"

# Linux input_event: timeval + type + code + value
EVENT = struct.Struct("llHHI")

with open(device, "rb") as f:
    print("Reading", device)
    print("Press buttons; Ctrl-C exits.\n")

    while True:
        data = f.read(EVENT.size)
        if len(data) != EVENT.size:
            break

        sec, usec, event_type, code, value = EVENT.unpack(data)

        if event_type in (1, 3):  # EV_KEY or EV_ABS
            print(
                f"type={event_type:02d} "
                f"code={code:03d} "
                f"value={value}"
            )



