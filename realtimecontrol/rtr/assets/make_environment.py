#!/usr/bin/env python3
"""Generate a starter ``environment.glb`` (glTF 2.0 binary) with no dependencies.

Blender is not available in this environment, so this stdlib-only script writes a
valid GLB directly. It is the source of truth the user then edits in their own
Blender (open ``environment.glb`` → edit → ``File > Export > glTF 2.0 (.glb)`` over
the same file).

The scene is authored **Y-up** (the glTF/Blender-export convention): the floor is the
XZ plane at y=0 and the walls rise in +Y. The client's Robot3D loader rotates the
loaded scene +90° about X (maps +Y → +Z) so it sits correctly in the Z-up robot scene.

Contents (meters):
- ``floor``     : 10 × 10 m plane at y=0 (dark gray),
- ``pedestal``  : cylinder r=0.22 h=0.48 at the origin (matches the schematic base),
- ``wall_x``    : ~8 m × 3 m wall along the back,
- ``wall_z``    : ~8 m × 3 m wall along the left,
so the robot at the origin is not boxed in (front + right stay open).
"""

import json
import math
import os
import struct

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "environment.glb")


# ---------------------------------------------------------------------------
# Geometry builders. Each returns {name, positions, normals, indices, color}.
# Indices are 0-based *within* the object (each object becomes its own primitive).
# ---------------------------------------------------------------------------

def _obj(name, color):
    return {"name": name, "positions": [], "normals": [], "indices": [], "color": color}


def make_floor():
    o = _obj("floor", [0.18, 0.19, 0.21])
    corners = [(-5, 0, -5), (5, 0, -5), (5, 0, 5), (-5, 0, 5)]  # 10 x 10 m at y=0
    for p in corners:
        o["positions"].append(list(p))
        o["normals"].append([0, 1, 0])
    o["indices"] = [0, 1, 2, 0, 2, 3]
    return o


def make_cylinder(r, h, n, color):
    """A cylinder of radius r, height h (y 0..h), n segments, at the origin."""
    o = _obj("pedestal", color)
    bottom, top = [], []
    for i in range(n):
        a = 2 * math.pi * i / n
        x, z = r * math.cos(a), r * math.sin(a)
        nx, nz = math.cos(a), math.sin(a)
        bottom.append(len(o["positions"]))
        o["positions"].append([x, 0, z]); o["normals"].append([nx, 0, nz])
        top.append(len(o["positions"]))
        o["positions"].append([x, h, z]); o["normals"].append([nx, 0, nz])
    for i in range(n):  # side quads
        i0, i1 = i, (i + 1) % n
        a, b, c, d = bottom[i0], bottom[i1], top[i1], top[i0]
        o["indices"].extend([a, b, c, a, c, d])
    tc = len(o["positions"])  # top cap fan (normal +Y)
    o["positions"].append([0, h, 0]); o["normals"].append([0, 1, 0])
    for i in range(n):
        i0, i1 = i, (i + 1) % n
        o["indices"].extend([tc, top[i0], top[i1]])
    bc = len(o["positions"])  # bottom cap fan (normal -Y)
    o["positions"].append([0, 0, 0]); o["normals"].append([0, -1, 0])
    for i in range(n):
        i0, i1 = i, (i + 1) % n
        o["indices"].extend([bc, bottom[i1], bottom[i0]])
    return o


def make_wall(name, color, axis, fixed, lo, hi, thick, height):
    """A thin wall box; ``axis`` is 'x' or 'z' (the wall's long direction)."""
    o = _obj(name, color)
    t = thick / 2.0
    if axis == "x":
        x0, x1, z0, z1 = fixed - t, fixed + t, lo, hi
    else:
        z0, z1, x0, x1 = fixed - t, fixed + t, lo, hi
    y0, y1 = 0.0, height
    C = {  # 8 canonical corners
        "d00": (x0, y0, z0), "d10": (x1, y0, z0), "d11": (x1, y0, z1), "d01": (x0, y0, z1),
        "u00": (x0, y1, z0), "u10": (x1, y1, z0), "u11": (x1, y1, z1), "u01": (x0, y1, z1),
    }
    faces = [  # (4 corners, outward normal) — vertices duplicated per face
        (["d00", "d10", "d11", "d01"], (0, -1, 0)),
        (["u00", "u01", "u11", "u10"], (0, 1, 0)),
        (["d00", "d01", "u01", "u00"], (-1, 0, 0)),
        (["d10", "d11", "u11", "u10"], (1, 0, 0)),
        (["d00", "d10", "u10", "u00"], (0, 0, -1)),
        (["d11", "d01", "u01", "u11"], (0, 0, 1)),
    ]
    for quad, nrm in faces:
        base = len(o["positions"])
        for cname in quad:
            o["positions"].append(list(C[cname]))
            o["normals"].append(list(nrm))
        a, b, c, d = base, base + 1, base + 2, base + 3
        o["indices"].extend([a, b, c, a, c, d])
    return o


def build_scene():
    return [
        make_floor(),
        make_cylinder(0.22, 0.48, 24, [0.32, 0.35, 0.40]),
        make_wall("wall_x", [0.30, 0.33, 0.38], "x", fixed=-4.9, lo=-4.0, hi=4.0, thick=0.15, height=3.0),
        make_wall("wall_z", [0.30, 0.33, 0.38], "z", fixed=-4.9, lo=-4.0, hi=4.0, thick=0.15, height=3.0),
    ]


# ---------------------------------------------------------------------------
# glTF assembly.
# ---------------------------------------------------------------------------

def pack(objects):
    """Pack objects into (gltf_dict, bin_bytes)."""
    buf = bytearray()
    buffer_views, accessors, meshes, nodes, materials = [], [], [], [], []

    def align4():
        buf.extend(b"\x00" * ((4 - len(buf) % 4) % 4))

    for obj in objects:
        nverts, nidx = len(obj["positions"]), len(obj["indices"])
        # positions (float32)
        pos_off = len(buf)
        for (x, y, z) in obj["positions"]:
            buf.extend(struct.pack("<fff", x, y, z))
        buffer_views.append({"buffer": 0, "byteOffset": pos_off,
                             "byteLength": nverts * 12, "target": 34962})
        pos_acc = len(accessors)
        accessors.append({"bufferView": len(buffer_views) - 1, "componentType": 5126,
                          "count": nverts, "type": "VEC3"})
        # normals (float32)
        nrm_off = len(buf)
        for (x, y, z) in obj["normals"]:
            buf.extend(struct.pack("<fff", x, y, z))
        buffer_views.append({"buffer": 0, "byteOffset": nrm_off,
                             "byteLength": nverts * 12, "target": 34962})
        accessors.append({"bufferView": len(buffer_views) - 1, "componentType": 5126,
                          "count": nverts, "type": "VEC3"})
        # indices (uint16)
        idx_off = len(buf)
        for i in obj["indices"]:
            buf.extend(struct.pack("<H", i))
        buffer_views.append({"buffer": 0, "byteOffset": idx_off,
                             "byteLength": nidx * 2, "target": 43954})
        accessors.append({"bufferView": len(buffer_views) - 1, "componentType": 5121,
                          "count": nidx, "type": "SCALAR"})
        align4()

        r, g, b = obj["color"]
        materials.append({
            "name": obj["name"],
            "pbrMetallicRoughness": {"baseColorFactor": [r, g, b, 1.0],
                                      "metallicFactor": 0.0, "roughnessFactor": 0.9},
            "doubleSided": True,
        })
        meshes.append({"name": obj["name"], "primitives": [{
            "attributes": {"POSITION": pos_acc, "NORMAL": len(accessors) - 2},
            "indices": len(accessors) - 1, "material": len(materials) - 1}]})
        nodes.append({"name": obj["name"], "mesh": len(meshes) - 1})

    total = len(buf)
    gltf = {
        "asset": {"version": "2.0", "generator": "make_environment.py"},
        "scene": 0,
        "scenes": [{"name": "environment", "nodes": list(range(len(nodes)))}],
        "nodes": nodes, "meshes": meshes, "materials": materials,
        "accessors": accessors, "bufferViews": buffer_views,
        "buffers": [{"byteLength": total}],
    }
    return gltf, bytes(buf)


def main():
    objects = build_scene()
    gltf, bin_data = pack(objects)

    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("ascii")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)  # pad JSON chunk to 4
    bin_bytes = bin_data + b"\x00" * ((4 - len(bin_data) % 4) % 4)  # pad BIN to 4

    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    out = bytearray()
    out += struct.pack("<III", 0x46546C67, 2, total)  # magic 'glTF', version 2, length
    out += struct.pack("<I4s", len(json_bytes), b"JSON")
    out += json_bytes
    out += struct.pack("<I4s", len(bin_bytes), b"BIN")
    out += bin_bytes

    with open(OUT, "wb") as f:
        f.write(bytes(out))
    print(f"wrote {OUT} ({len(out)} bytes, {len(objects)} objects)")


if __name__ == "__main__":
    main()
