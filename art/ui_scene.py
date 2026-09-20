"""Blender: every dashboard icon and status light as a small 3D render.

    blender -b --factory-startup -P art/ui_scene.py -- <out_dir> [--fast]

Icons are original extruded outlines (no fonts, no borrowed marks) in two finishes:
chrome (resting) and ember (active / selected). Status lights are glass domes in a chrome
bezel. Output: <out>/icons/<finish>/<name>.png (128 px) and <out>/leds/<state>.png (96 px).
"""
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

FINISH = {
    "chrome": {"color": (0.9, 0.91, 0.94), "metal": 0.6, "rough": 0.18, "emit": None},
    "ember": {"color": (1.0, 0.6, 0.28), "metal": 0.9, "rough": 0.14, "emit": (1.0, 0.42, 0.1)},
}
LEDS = {"live": (0.35, 0.95, 0.55), "warn": (1.0, 0.72, 0.2), "error": (1.0, 0.25, 0.25),
        "idle": (0.25, 0.26, 0.3), "ember": (1.0, 0.42, 0.08)}


def srgb(c):
    return tuple(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92 for v in c)


def reset(size, fast):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 8 if fast else 28
    sc.cycles.use_denoising = True
    sc.render.resolution_x = sc.render.resolution_y = size
    sc.render.film_transparent = True
    sc.render.image_settings.color_mode = "RGBA"
    sc.view_settings.view_transform = "Standard"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = kind
            prefs.refresh_devices()
            if any(d.type == kind for d in prefs.devices):
                for d in prefs.devices:
                    d.use = d.type == kind
                sc.cycles.device = "GPU"
                break
        except TypeError:
            continue
    w = bpy.data.worlds.new("w")
    sc.world = w
    w.use_nodes = True
    nt = w.node_tree
    tc, sep, ramp = (nt.nodes.new("ShaderNodeTexCoord"), nt.nodes.new("ShaderNodeSeparateXYZ"),
                     nt.nodes.new("ShaderNodeValToRGB"))
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])
    nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.3, (0.02, 0.02, 0.025, 1)
    els[1].position, els[1].color = 0.5, (1, 1, 1, 1)
    els.new(0.42).color = (0.35, 0.36, 0.4, 1)
    nt.links.new(ramp.outputs["Color"], nt.nodes["Background"].inputs["Color"])
    return sc


def material(name, color, metal=0.0, rough=0.4, emit=None, strength=0.0, glass=False):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*srgb(color), 1)
    p.inputs["Metallic"].default_value = metal
    p.inputs["Roughness"].default_value = rough
    if glass:
        p.inputs["Transmission Weight"].default_value = 0.6
        p.inputs["Coat Weight"].default_value = 1.0
    if emit:
        p.inputs["Emission Color"].default_value = (*srgb(emit), 1)
        p.inputs["Emission Strength"].default_value = strength
    return m


def finish_mat(f):
    d = FINISH[f]
    return material(f, d["color"], d["metal"], d["rough"], d["emit"], 0.45 if d["emit"] else 0)


def link(o):
    bpy.context.scene.collection.objects.link(o)
    return o


def solid(bm, m):
    me = bpy.data.meshes.new("m")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(m)
    o = link(bpy.data.objects.new("o", me))
    for p in me.polygons:
        p.use_smooth = True
    b = o.modifiers.new("b", "BEVEL")
    b.width, b.segments, b.limit_method = 0.035, 3, "ANGLE"
    return o


def outline(pts, depth, m, holes=()):
    """Extrude a closed outline (XZ plane, facing -Y). Holes are cut with a boolean."""
    bm = bmesh.new()
    f = [bm.verts.new((x, -depth / 2, z)) for x, z in pts]
    b = [bm.verts.new((x, depth / 2, z)) for x, z in pts]
    bm.faces.new(list(reversed(f)))
    bm.faces.new(b)
    for i in range(len(pts)):
        j = (i + 1) % len(pts)
        bm.faces.new([f[i], f[j], b[j], b[i]])
    o = solid(bm, m)
    for h in holes:
        cutter = outline(h, depth * 3, m)
        mod = o.modifiers.new("h", "BOOLEAN")
        mod.operation, mod.object = "DIFFERENCE", cutter
        cutter.hide_render = True
    return o


def rect(x0, z0, x1, z1):
    return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]


def rrect(x0, z0, x1, z1, r, seg=6):
    pts = []
    for cx, cz, a0 in ((x1 - r, z0 + r, -90), (x1 - r, z1 - r, 0), (x0 + r, z1 - r, 90), (x0 + r, z0 + r, 180)):
        for i in range(seg + 1):
            a = math.radians(a0 + 90 * i / seg)
            pts.append((cx + r * math.cos(a), cz + r * math.sin(a)))
    return pts


def circle(cx, cz, r, n=40):
    return [(cx + math.cos(2 * math.pi * i / n) * r, cz + math.sin(2 * math.pi * i / n) * r) for i in range(n)]


def arc_band(cx, cz, r0, r1, a0, a1, n=24):
    outer = [(cx + math.cos(math.radians(a0 + (a1 - a0) * i / n)) * r1, cz + math.sin(math.radians(a0 + (a1 - a0) * i / n)) * r1) for i in range(n + 1)]
    inner = [(cx + math.cos(math.radians(a1 - (a1 - a0) * i / n)) * r0, cz + math.sin(math.radians(a1 - (a1 - a0) * i / n)) * r0) for i in range(n + 1)]
    return outer + inner


def bar(x0, z0, x1, z1, w):
    d = Vector((x1 - x0, z1 - z0))
    n = Vector((-d.y, d.x)).normalized() * w / 2
    return [(x0 + n.x, z0 + n.y), (x1 + n.x, z1 + n.y), (x1 - n.x, z1 - n.y), (x0 - n.x, z0 - n.y)]


D = 0.34      # extrusion depth


def icon(name, m):
    if name == "live":
        outline(circle(0, 0, 0.18), D, m)
        for r in (0.42, 0.72):
            outline(arc_band(0, 0, r - 0.11, r, -50, 50), D, m)
            outline(arc_band(0, 0, r - 0.11, r, 130, 230), D, m)
    elif name == "clips":
        outline(rrect(-0.5, -0.85, 0.5, 0.85, 0.12), D, m, holes=[[(-0.14, -0.28), (-0.14, 0.28), (0.26, 0)]])
    elif name == "growth":
        pts = [(-0.85, -0.55), (-0.35, -0.05), (0.0, -0.3), (0.55, 0.3)]
        for a, b in zip(pts, pts[1:]):
            outline(bar(*a, *b, 0.17), D, m)
        outline([(0.28, 0.45), (0.8, 0.62), (0.68, 0.1)], D, m)
        outline(rect(-0.9, -0.88, 0.9, -0.74), D, m)
    elif name == "system":
        for z in (-0.45, 0.25):
            outline(rrect(-0.85, z - 0.25, 0.85, z + 0.25, 0.08), D, m, holes=[circle(-0.55, z, 0.07, 16), rect(-0.2, z - 0.05, 0.6, z + 0.05)])
    elif name == "studio":
        outline(rrect(-0.85, -0.75, 0.85, 0.2, 0.08), D, m, holes=[[(-0.12, -0.5), (-0.12, 0.0), (0.25, -0.25)]])
        top = [(-0.85, 0.3), (0.85, 0.52), (0.85, 0.68), (-0.85, 0.46)]
        outline(top, D, m)
    elif name == "settings":
        for z, x in ((0.55, 0.3), (0.0, -0.35), (-0.55, 0.15)):
            outline(rect(-0.85, z - 0.06, 0.85, z + 0.06), D, m)
            outline(circle(x, z, 0.19), D * 1.3, m)
    elif name == "setup":
        for z in (0.55, 0.0, -0.55):
            outline(rect(-0.25, z - 0.08, 0.85, z + 0.08), D, m)
        for z in (0.55, 0.0):
            outline(bar(-0.85, z, -0.68, z - 0.15, 0.12) , D, m)
            outline(bar(-0.7, z - 0.15, -0.42, z + 0.15, 0.12), D, m)
        outline(circle(-0.62, -0.55, 0.14), D, m)
    elif name == "lock":
        outline(rrect(-0.6, -0.8, 0.6, 0.15, 0.12), D, m, holes=[circle(0, -0.3, 0.1, 16)])
        outline(arc_band(0, 0.15, 0.3, 0.45, 0, 180), D * 0.8, m)
    elif name == "pause":
        outline(rrect(-0.5, -0.75, -0.15, 0.75, 0.08), D, m)
        outline(rrect(0.15, -0.75, 0.5, 0.75, 0.08), D, m)
    elif name == "play":
        outline([(-0.45, -0.72), (-0.45, 0.72), (0.72, 0)], D, m)
    elif name == "folder":
        outline([(-0.85, -0.7), (0.85, -0.7), (0.85, 0.4), (0.0, 0.4), (-0.18, 0.62), (-0.85, 0.62)], D, m)
    elif name == "refresh":
        outline(arc_band(0, 0, 0.5, 0.7, 40, 330), D, m)
        outline([(0.35, 0.75), (0.85, 0.62), (0.52, 0.22)], D, m)
    elif name == "send":
        outline([(-0.85, -0.1), (0.85, 0.75), (0.2, -0.8), (0.0, -0.15)], D, m)
    elif name == "download":
        outline([(-0.12, 0.85), (0.12, 0.85), (0.12, -0.05), (0.42, -0.05), (0, -0.5), (-0.42, -0.05), (-0.12, -0.05)], D, m)
        outline(rect(-0.8, -0.85, 0.8, -0.68), D, m)
    elif name == "copy":
        outline(rrect(-0.3, -0.85, 0.75, 0.3, 0.1), D, m)
        outline(rrect(-0.8, -0.35, 0.25, 0.8, 0.1), D * 0.6, m).location.y = 0.25
    elif name == "search":
        outline(arc_band(-0.15, 0.15, 0.42, 0.6, 0, 360), D, m)
        outline(bar(0.25, -0.25, 0.75, -0.75, 0.22), D, m)


ICONS = ("live", "clips", "growth", "system", "studio", "settings", "setup", "lock", "pause",
         "play", "folder", "refresh", "send", "download", "copy", "search")


def camera(scale):
    cd = bpy.data.cameras.new("c")
    cd.type, cd.ortho_scale = "ORTHO", scale
    cam = link(bpy.data.objects.new("c", cd))
    cam.location, cam.rotation_euler = (0.7, -6, 0.9), (math.radians(81), 0, math.radians(6.6))
    bpy.context.scene.camera = cam
    for n, loc, e in (("k", (-3, -4, 4), 520), ("r", (3, 3, 3), 420)):
        L = bpy.data.lights.new(n, "AREA")
        L.energy, L.size = e, 3
        lo = link(bpy.data.objects.new(n, L))
        lo.location = loc
        lo.rotation_euler = (Vector((0, 0, 0)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()


def main(out: Path, fast: bool):
    for f in FINISH:
        for name in ICONS:
            sc = reset(128, fast)
            icon(name, finish_mat(f))
            camera(2.25)
            (out / "icons" / f).mkdir(parents=True, exist_ok=True)
            sc.render.filepath = str(out / "icons" / f / f"{name}.png")
            bpy.ops.render.render(write_still=True)
        print(f"[ui] icons {f}", flush=True)
    for state, col in LEDS.items():
        sc = reset(96, fast)
        chrome = finish_mat("chrome")
        ring = bmesh.new()
        bmesh.ops.create_cone(ring, cap_ends=True, segments=48, radius1=0.62, radius2=0.62, depth=0.2)
        o = solid(ring, chrome)
        o.rotation_euler = (math.pi / 2, 0, 0)
        dome = bmesh.new()
        bmesh.ops.create_uvsphere(dome, u_segments=40, v_segments=20, radius=0.46)
        lit = state != "idle"
        d = solid(dome, material("led", col, rough=0.08, emit=col if lit else None, strength=1.6 if lit else 0, glass=True))
        d.scale = (1, 0.55, 1)
        d.location = (0, -0.12, 0)
        camera(1.6)
        (out / "leds").mkdir(parents=True, exist_ok=True)
        sc.render.filepath = str(out / "leds" / f"{state}.png")
        bpy.ops.render.render(write_still=True)
    print("[ui] leds", flush=True)


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:]
    main(Path(argv[0]).resolve(), "--fast" in argv)
