"""Blender: 3D moodlet icons for the desktop pets, one set per finish.

    blender -b --factory-startup -P art/icons_scene.py -- <out_dir> [--fast]

Finishes: chrome (Blip, Moss, Nib), ember (Ember), pearl (Glitch). Every icon is an original
low-poly shape or extruded glyph. Output: <out_dir>/<finish>/<icon>.png, 96 px, transparent.
"""
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

SIZE = 96
ICONS = ("clip", "post", "reject", "spike", "trouble", "sleep", "hi", "love", "think", "star")
FINISHES = {
    "chrome": {"color": (0.9, 0.91, 0.94), "metal": 0.55, "rough": 0.2, "emit": None},
    "ember": {"color": (1.0, 0.62, 0.3), "metal": 1.0, "rough": 0.12, "emit": (1.0, 0.45, 0.1)},
    "pearl": {"color": (0.82, 0.76, 0.95), "metal": 0.5, "rough": 0.26, "emit": None},
}
ACCENT = {"reject": (0.95, 0.3, 0.3), "trouble": (1.0, 0.72, 0.2), "love": (1.0, 0.36, 0.52),
          "post": (0.4, 0.86, 0.6)}


def srgb(c):
    return tuple(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92 for v in c)


def reset(fast):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 8 if fast else 24
    sc.cycles.use_denoising = True
    sc.render.resolution_x = sc.render.resolution_y = SIZE
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


def mat(finish, accent=None):
    f = FINISHES[finish]
    m = bpy.data.materials.new(finish)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    base = accent or f["color"]
    p.inputs["Base Color"].default_value = (*srgb(base), 1)
    p.inputs["Metallic"].default_value = f["metal"]
    p.inputs["Roughness"].default_value = f["rough"]
    emit = f["emit"] or (accent and tuple(v * 0.6 for v in accent))
    if emit:
        p.inputs["Emission Color"].default_value = (*srgb(emit), 1)
        p.inputs["Emission Strength"].default_value = 0.5 if accent else 0.4
    return m


def obj_from(bm, m, name="o"):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(m)
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    for p in me.polygons:
        p.use_smooth = True
    b = o.modifiers.new("b", "BEVEL")
    b.width, b.segments = 0.04, 3
    return o


def prism(pts, depth, m, name="o"):
    """Extrude a 2D outline (XZ plane) toward the camera."""
    bm = bmesh.new()
    front = [bm.verts.new((x, -depth / 2, z)) for x, z in pts]
    back = [bm.verts.new((x, depth / 2, z)) for x, z in pts]
    bm.faces.new(list(reversed(front)))
    bm.faces.new(back)
    for i in range(len(pts)):
        j = (i + 1) % len(pts)
        bm.faces.new([front[i], front[j], back[j], back[i]])
    return obj_from(bm, m, name)


def bar(x0, z0, x1, z1, w, m):
    d = Vector((x1 - x0, z1 - z0))
    n = Vector((-d.y, d.x)).normalized() * w / 2
    return prism([(x0 + n.x, z0 + n.y), (x1 + n.x, z1 + n.y), (x1 - n.x, z1 - n.y), (x0 - n.x, z0 - n.y)], 0.3, m)


def glyph(text, m, size=1.6):
    cu = bpy.data.curves.new("t", "FONT")
    cu.body, cu.size, cu.extrude, cu.bevel_depth = text, size, 0.14, 0.03
    cu.align_x, cu.align_y = "CENTER", "CENTER"
    o = bpy.data.objects.new("t", cu)
    o.rotation_euler = (math.pi / 2, 0, 0)
    cu.materials.append(m)
    bpy.context.scene.collection.objects.link(o)
    return o


def ring(r, pts=40):
    return [(math.cos(i * 2 * math.pi / pts) * r, math.sin(i * 2 * math.pi / pts) * r) for i in range(pts)]


def build(icon, finish):
    m = mat(finish, ACCENT.get(icon))
    if icon == "clip":                         # film frame with a play notch
        prism([(-0.8, -0.6), (0.8, -0.6), (0.8, 0.6), (-0.8, 0.6)], 0.3, m)
        prism([(-0.22, -0.3), (-0.22, 0.3), (0.32, 0.0)], 0.5, mat(finish, (0.05, 0.05, 0.06)))
    elif icon == "post":                       # chunky up arrow
        prism([(-0.25, -0.85), (0.25, -0.85), (0.25, 0.05), (0.62, 0.05), (0, 0.85), (-0.62, 0.05), (-0.25, 0.05)], 0.35, m)
    elif icon == "reject":
        bar(-0.62, -0.62, 0.62, 0.62, 0.34, m)
        bar(-0.62, 0.62, 0.62, -0.62, 0.34, m)
    elif icon == "spike":                      # flame drop
        pts = []
        for i in range(48):
            a = i * 2 * math.pi / 48
            x, z = math.sin(a) * 0.62, -math.cos(a) * 0.62 - 0.2
            if z > -0.2:
                k = (z + 0.2) / 1.2
                x *= max(0.0, 1 - k ** 0.9)
                z = -0.2 + (z + 0.2) * 1.9
            pts.append((x, z))
        prism(pts, 0.4, m)
    elif icon == "trouble":
        prism([(-0.85, -0.7), (0.85, -0.7), (0, 0.85)], 0.3, m)
        glyph("!", mat(finish, (0.05, 0.05, 0.06)), 1.1).location = (0, -0.2, -0.15)
    elif icon == "sleep":
        glyph("Z", m, 1.3).location = (-0.25, 0, -0.2)
        glyph("z", m, 0.8).location = (0.45, 0, 0.45)
    elif icon == "hi" or icon == "star":       # our four-point spike star
        pts = []
        for i in range(16):
            a = i * math.pi / 8 + math.pi / 2
            k = (1.0, 0.36, 0.5, 0.36)[i % 4]
            pts.append((math.cos(a) * 0.9 * k, math.sin(a) * 0.9 * k))
        prism(pts, 0.3, m)
    elif icon == "love":                       # heart from two circles and a point
        pts = [(math.cos(a) * 0.42 - 0.34, math.sin(a) * 0.42 + 0.22) for a in [i * math.pi / 20 for i in range(8, 36)]]
        pts = [(x, z) for x, z in pts if not (x > -0.34 and z < 0.22)]
        left = [(math.cos(i * math.pi / 24 + math.pi / 4) * 0.42 - 0.3, math.sin(i * math.pi / 24 + math.pi / 4) * 0.42 + 0.22) for i in range(0, 30)]
        right = [(-x, z) for x, z in reversed(left)]
        prism(right + [(0, -0.82)] + left, 0.35, m)
    elif icon == "think":
        prism(ring(0.72), 0.3, m)
        prism([(-0.5, -0.45), (-0.2, -0.6), (-0.7, -0.95)], 0.3, m)
        glyph("?", mat(finish, (0.05, 0.05, 0.06)), 0.9).location = (0, -0.2, -0.3)


def render(out, fast):
    for finish in FINISHES:
        for icon in ICONS:
            sc = reset(fast)
            build(icon, finish)
            cd = bpy.data.cameras.new("c")
            cd.type, cd.ortho_scale = "ORTHO", 2.3
            cam = bpy.data.objects.new("c", cd)
            bpy.context.scene.collection.objects.link(cam)
            cam.location, cam.rotation_euler = (0.9, -6, 0.9), (math.radians(82), 0, math.radians(8.5))
            sc.camera = cam
            for n, loc, e in (("k", (-3, -4, 4), 500), ("r", (3, 3, 3), 400)):
                L = bpy.data.lights.new(n, "AREA")
                L.energy, L.size = e, 3
                lo = bpy.data.objects.new(n, L)
                lo.location = loc
                lo.rotation_euler = (Vector((0, 0, 0)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
                bpy.context.scene.collection.objects.link(lo)
            d = out / finish
            d.mkdir(parents=True, exist_ok=True)
            sc.render.filepath = str(d / f"{icon}.png")
            bpy.ops.render.render(write_still=True)
        print(f"[icons] {finish} done", flush=True)


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:]
    render(Path(argv[0]).resolve(), "--fast" in argv)
