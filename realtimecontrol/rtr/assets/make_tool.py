#!/usr/bin/env python3
"""Generate a starter ``tool.glb`` (glTF 2.0 binary) with no dependencies.

Blender is not available in this environment, so this stdlib-only script writes a
valid GLB directly. It is the source of truth the user then edits in their own
Blender (open ``tool.glb`` -> edit -> ``File > Export > glTF 2.0 (.glb)`` over the
same file).

The scene is authored **Y-up** (the glTF/Blender-export convention): the box's long
axis is +Y. The client's Robot3D loader rotates the loaded scene +90 deg about X
(maps +Y -> +Z) and attaches it to the tool node, so it reproduces the old white
placeholder box (0.09 x 0.09 x 0.14 m) at the $FLANGE. The box is centered on the
origin, so the tool pivot (the tool readout point) sits at its middle.

Replace this box with your own end-effector design in Blender; keep it Y-up and
centered on the origin so it stays attached to the same point.
"""

import json
import os
import struct

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool.glb")

# The box is authored Y-up: long axis (0.14 m) along +Y, so after the client's
# +90 deg about X rotation it reads as the 0.09 x 0.09 x 0.14 placeholder box.
W, H, D = 0.09, 0.14, 0.09  # x, y, z in the GLB's local (Y-up) frame


def make_box(name, color):
    """A box centered at the origin with dimensions (W, H, D)."""
    o = {"name": name, "positions": [], "normals": [], "indices": [], "color": color}
    hw, hh, hd = W / 2.0, H / 2.0, D / 2.0
    # 8 corners: (x, y, z) with x = +/-hw, y = +/-hh, z = +/-hd
    C = {
        "000": (-hw, -hh, -hd), "100": (hw, -hh, -hd), "110": (hw, -hh, hd), "010": (-hw, -hh, hd),
        "001": (-hw, hh, -hd),  "101": (hw, hh, -hd),  "111": (hw, hh, hd),  "011": (-hw, hh, hd),
    }
    # 6 faces: (4 corners, outward normal) -- vertices duplicated per face
    faces = [
        (["000", "100", "110", "010"], (0, -1, 0)),  # bottom
        (["001", "101", "111", "011"], (0, 1, 0)),   # top
        (["000", "010", "011", "001"], (-1, 0, 0)),  # left
        (["100", "110", "111", "101"], (1, 0, 0)),   # right
        (["000", "001", "101", "100"], (0, 0, -1)),  # back
        (["010", "110", "111", "011"], (0, 0, 1)),   # front
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
    # A light-gray tool body (mirrors the old white placeholder box, 0xd7dbe2).
    return [make_box("tool", [0.84, 0.86, 0.89])]


# ---------------------------------------------------------------------------
# glTF assembly (same packing as make_environment.py).
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
                                      "metallicFactor": 0.0, "roughnessFactor": 0.6},
            "doubleSided": True,
        })
        meshes.append({"name": obj["name"], "primitives": [{
            "attributes": {"POSITION": pos_acc, "NORMAL": len(accessors) - 2},
            "indices": len(accessors) - 1, "material": len(materials) - 1}]})
        nodes.append({"name": obj["name"], "mesh": len(meshes) - 1})

    total = len(buf)
    gltf = {
        "asset": {"version": "2.0", "generator": "make_tool.py"},
        "scene": 0,
        "scenes": [{"name": "tool", "nodes": list(range(len(nodes))) }],
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
