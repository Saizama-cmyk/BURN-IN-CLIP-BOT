"""Blender scene for ClipBot / SPIKE OS brand art. Run headless:

    blender -b --factory-startup -P art/spike_scene.py -- <mode> <out_dir> [--fast]

modes: mark (1024 px still, transparent), splash (animated PNG frames, transparent),
       bg (1920x1080 backdrop), all.
The mark is an obsidian tile with a bevelled rim and a scope graticule, one emissive amber
signal trace that spikes, and a cyan peak bead. Same geometry as clipbot/icon.py.
"""
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

TRACE = [(0.17, 0.66), (0.29, 0.62), (0.38, 0.65), (0.46, 0.63), (0.53, 0.24), (0.60, 0.72),
         (0.67, 0.58), (0.75, 0.61), (0.83, 0.60)]
PEAK = 4
AMBER = (0.949, 0.663, 0.231)
CYAN = (0.31, 0.765, 0.851)
NAVY = (0.018, 0.018, 0.02)          # black leather now; name kept for the scene code
EMBER = (1.0, 0.54, 0.24)
THICK = 0.2
RADIUS = 0.46


def srgb(c):
    return tuple(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92 for v in c)


# ----------------------------------------------------------------------------- setup
def reset(res_x, res_y, samples, transparent=True):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.render.resolution_x, sc.render.resolution_y = res_x, res_y
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = transparent
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
    sc.render.image_settings.color_depth = "8"
    try:
        sc.view_settings.view_transform = "Standard"
        sc.view_settings.look = "Medium High Contrast"
    except TypeError:
        pass
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = kind
            prefs.refresh_devices()
            gpus = [d for d in prefs.devices if d.type == kind]
            if gpus:
                for d in prefs.devices:
                    d.use = d.type == kind
                sc.cycles.device = "GPU"
                print(f"[spike] rendering on {kind}: {[d.name for d in gpus]}")
                break
        except TypeError:
            continue
    world = bpy.data.worlds.new("w")
    sc.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    nt = world.node_tree
    tc, sep, ramp = (nt.nodes.new("ShaderNodeTexCoord"), nt.nodes.new("ShaderNodeSeparateXYZ"),
                     nt.nodes.new("ShaderNodeValToRGB"))
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])
    nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.3, (0.015, 0.015, 0.018, 1)
    els[1].position, els[1].color = 0.55, (1, 1, 1, 1)
    els.new(0.44).color = (0.3, 0.31, 0.34, 1)
    nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 1.0
    return sc


def mat(name, color, metallic=0.0, rough=0.4, coat=0.0, emit=None, strength=0.0, bump=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*srgb(color), 1)
    p.inputs["Metallic"].default_value = metallic
    p.inputs["Roughness"].default_value = rough
    p.inputs["Coat Weight"].default_value = coat
    p.inputs["Coat Roughness"].default_value = 0.04
    if emit is not None:
        p.inputs["Emission Color"].default_value = (*srgb(emit), 1)
        p.inputs["Emission Strength"].default_value = strength
    if bump:                                     # pebbled leather grain
        nt = m.node_tree
        vn, bn = nt.nodes.new("ShaderNodeTexVoronoi"), nt.nodes.new("ShaderNodeBump")
        vn.inputs["Scale"].default_value = 60
        bn.inputs["Strength"].default_value = bump
        nt.links.new(vn.outputs["Distance"], bn.inputs["Height"])
        nt.links.new(bn.outputs["Normal"], p.inputs["Normal"])
    return m


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def mesh_obj(name, bm, material):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(material)
    return link(bpy.data.objects.new(name, me))


def rounded_rect(bm, half, r, y0, y1, seg=14):
    """Rounded square in XZ, extruded between y0 (front) and y1 (back)."""
    pts = []
    for cx, cz, a0 in ((half - r, half - r, 0), (-(half - r), half - r, 90),
                       (-(half - r), -(half - r), 180), (half - r, -(half - r), 270)):
        for i in range(seg + 1):
            a = math.radians(a0 + 90 * i / seg)
            pts.append((cx + r * math.cos(a), cz + r * math.sin(a)))
    front = [bm.verts.new((x, y0, z)) for x, z in pts]
    back = [bm.verts.new((x, y1, z)) for x, z in pts]
    bm.faces.new(list(reversed(front)))
    bm.faces.new(back)
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new([front[i], front[j], back[j], back[i]])


def box(bm, center, size):
    r = bmesh.ops.create_cube(bm, size=1.0)
    for v in r["verts"]:
        v.co = Vector((center[0] + v.co.x * size[0], center[1] + v.co.y * size[1],
                       center[2] + v.co.z * size[2]))


# ----------------------------------------------------------------------------- the mark
def build_mark():
    """Returns (root empty, trace curve data, peak bead, trace material)."""
    root = link(bpy.data.objects.new("mark", None))
    front = -THICK / 2

    chrome = mat("chrome", (0.94, 0.95, 0.97), metallic=1, rough=0.06)
    bm = bmesh.new()
    rounded_rect(bm, 1.0, RADIUS, front + 0.02, THICK / 2)
    frame = mesh_obj("frame", bm, chrome)
    fb = frame.modifiers.new("bevel", "BEVEL")
    fb.width, fb.segments, fb.limit_method = 0.06, 6, "ANGLE"
    frame.parent = root
    for poly in frame.data.polygons:
        poly.use_smooth = True
    bm = bmesh.new()
    rounded_rect(bm, 0.86, RADIUS * 0.8, front - 0.005, front + 0.03)
    tile = mesh_obj("tile", bm, mat("leather", NAVY, rough=0.45, coat=0.4, bump=0.3))
    bev = tile.modifiers.new("bevel", "BEVEL")
    bev.width, bev.segments, bev.limit_method = 0.045, 6, "ANGLE"
    tile.parent = root
    for poly in tile.data.polygons:
        poly.use_smooth = True

    # engraved graticule, faintly lit
    bm = bmesh.new()
    for i in range(1, 8):
        c = -1 + i * 0.25
        box(bm, (c, front - 0.001, 0), (0.005, 0.002, 1.44))
        box(bm, (0, front - 0.001, c), (1.44, 0.002, 0.005))
    bm.free()
    for cxz in ((-0.72, 0.72), (0.72, 0.72), (-0.72, -0.72), (0.72, -0.72)):   # corner studs
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.045, location=(cxz[0], front - 0.02, cxz[1]))
        bpy.context.active_object.data.materials.append(chrome)
        bpy.context.active_object.parent = root

    # the signal trace: a round emissive tube proud of the face (rebuilt per splash frame)
    tmat = mat("trace", (0.95, 0.96, 0.98), metallic=1, rough=0.05, emit=EMBER, strength=0.0)
    me = bpy.data.meshes.new("trace")
    me.materials.append(tmat)
    trace = link(bpy.data.objects.new("trace", me))
    trace.parent = root
    set_trace(me, 1.0, front - 0.05)

    u, v = TRACE[PEAK]
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.1, segments=48, ring_count=24,
                                         location=(u * 2 - 1, front - 0.07, 1 - v * 2))
    bead = bpy.context.active_object
    bpy.ops.object.shade_smooth()
    bead.data.materials.append(mat("bead", EMBER, rough=0.15, emit=EMBER, strength=1.4))
    bead.parent = root
    return root, me, bead, tmat


def polyline_upto(pts, frac):
    """The first ``frac`` (by length) of a polyline."""
    segs = [(a, b, (b - a).length) for a, b in zip(pts, pts[1:])]
    left = sum(L for _, _, L in segs) * frac
    out = [pts[0]]
    for a, b, L in segs:
        if left <= 0:
            break
        out.append(b if left >= L else a.lerp(b, left / L))
        left -= L
    return out


def set_trace(me, frac, y, radius=0.05):
    """Tube = cylinders along the segments + spheres at every joint (clean round miters)."""
    pts = [Vector((u * 2 - 1, y, 1 - v * 2)) for u, v in TRACE]
    pts = polyline_upto(pts, max(0.0, min(1.0, frac)))
    bm = bmesh.new()
    if frac > 0.0005:
        for p in pts:
            bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=radius,
                                      matrix=__import__("mathutils").Matrix.Translation(p))
        for a, b in zip(pts, pts[1:]):
            d = b - a
            if d.length < 1e-5:
                continue
            rot = Vector((0, 0, 1)).rotation_difference(d.normalized()).to_matrix().to_4x4()
            m = __import__("mathutils").Matrix.Translation((a + b) / 2) @ rot
            bmesh.ops.create_cone(bm, cap_ends=False, segments=32, radius1=radius,
                                  radius2=radius, depth=d.length, matrix=m)
    for f in bm.faces:
        f.smooth = True
    bm.to_mesh(me)
    bm.free()


def studio(target=(0, 0, 0)):
    def area(name, loc, energy, size, color):
        L = bpy.data.lights.new(name, "AREA")
        L.energy, L.size, L.color = energy, size, color
        o = link(bpy.data.objects.new(name, L))
        o.location = loc
        c = o.constraints.new("TRACK_TO")
        c.target = aim
        c.track_axis, c.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    aim = link(bpy.data.objects.new("aim", None))
    aim.location = target
    area("key", (-1.5, -6.0, 5.5), 1400, 5.0, (0.62, 0.76, 1.0))      # big softbox: top sheen
    area("rimL", (-3.5, 2.5, 2.0), 500, 2.5, (0.35, 0.75, 0.95))     # cyan edge light
    area("rimR", (3.5, 2.5, -1.5), 380, 2.5, (0.35, 0.75, 0.95))
    area("warm", (3.5, -3.0, -2.5), 160, 3.0, (1.0, 0.7, 0.4))       # warm fill low right
    return aim


def camera(loc, lens=100, ortho=None, aim=None):
    cd = bpy.data.cameras.new("cam")
    cd.lens = lens
    if ortho:
        cd.type, cd.ortho_scale = "ORTHO", ortho
    cam = link(bpy.data.objects.new("cam", cd))
    cam.location = loc
    c = cam.constraints.new("TRACK_TO")
    c.target = aim
    c.track_axis, c.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    bpy.context.scene.camera = cam
    return cam


def render_to(path):
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


# ----------------------------------------------------------------------------- modes
def do_mark(out: Path, fast: bool):
    reset(1024, 1024, 64 if fast else 256)
    build_mark()
    aim = studio()
    camera((0, -30, 0), ortho=2.12, aim=aim)
    render_to(out / "mark.png")


def ease_out(t):
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def back_out(t, s=2.2):
    t = min(1.0, max(0.0, t)) - 1
    return t * t * ((s + 1) * t + s) + 1


def do_splash(out: Path, fast: bool, frames=132):
    reset(560, 560, 16 if fast else 48)
    bpy.context.scene.render.use_persistent_data = True
    root, tme, bead, tmat = build_mark()
    aim = studio()
    cam = camera((0, -9.2, 0), lens=100, aim=aim)
    strength = tmat.node_tree.nodes["Principled BSDF"].inputs["Emission Strength"]
    gl = bpy.data.lights.new("glint", "AREA")
    gl.energy, gl.size, gl.shape, gl.size_y = 2500, 0.25, "RECTANGLE", 6
    glint = link(bpy.data.objects.new("glint", gl))
    glint.rotation_euler = (math.radians(90), 0, math.radians(25))
    folder = out / "splash"
    folder.mkdir(parents=True, exist_ok=True)
    step = 6 if fast else 1
    for f in range(0, frames, step):
        t = f / (frames - 1)
        k = ease_out(t / 0.45)                                        # fly in and settle
        wob = 0.0 if t < 0.45 else math.sin((t - 0.45) / 0.55 * math.pi * 2) * (1 - t) * 6
        root.rotation_euler = (math.radians(24 * (1 - k)), 0, math.radians(-50 * (1 - k) + wob))
        cam.location = (0, -9.2 - 5.0 * (1 - k), 0.8 * (1 - k))
        set_trace(tme, ease_out((t - 0.18) / 0.35), -THICK / 2 - 0.05)
        pop = back_out((t - 0.52) / 0.12) if t > 0.52 else 0.0
        bead.scale = (max(0.001, pop),) * 3
        strength.default_value = 2.5 * math.exp(-((t - 0.56) / 0.05) ** 2)   # trace flares as the ember lights
        glint.location = (-4 + 8 * ease_out((t - 0.62) / 0.3), -3, 0)        # light sweeps across the chrome
        render_to(folder / f"f_{f:03d}.png")


def do_bg(out: Path, fast: bool):
    reset(1920, 1080, 32 if fast else 160, transparent=False)
    sc = bpy.context.scene
    wbg = sc.world.node_tree.nodes["Background"]
    wbg.inputs["Strength"].default_value = 0.08
    # a field of dark server-rack pillars, heights from layered waves; a few lit status caps
    cols, rows, pitch = 70, 46, 0.34
    bm, lit_a, lit_c = bmesh.new(), bmesh.new(), bmesh.new()
    rnd = 1
    for i in range(cols):
        for j in range(rows):
            x, y = (i - cols / 2) * pitch, j * pitch
            h = (0.35 + 0.22 * math.sin(i * 0.37) * math.cos(j * 0.29)
                 + 0.12 * math.sin(i * 0.11 + j * 0.23) + 0.08 * math.sin(i * 1.7 + j * 0.9))
            h = max(0.08, h)
            box(bm, (x, y, h / 2), (pitch * 0.86, pitch * 0.86, h))
            rnd = (rnd * 1103515245 + 12345) & 0x7FFFFFFF
            pick = rnd % 1000
            if pick < 12:
                box(lit_a if pick < 9 else lit_c, (x, y, h + 0.004),
                    (pitch * 0.5, pitch * 0.5, 0.008))
    field = mesh_obj("field", bm, mat("rack", (0.05, 0.05, 0.055), metallic=0.9, rough=0.3))
    bev = field.modifiers.new("bevel", "BEVEL")
    bev.width, bev.segments = 0.012, 2
    mesh_obj("litA", lit_a, mat("la", EMBER, emit=EMBER, strength=5))
    mesh_obj("litC", lit_c, mat("lc", (1, 1, 1), emit=(1, 1, 1), strength=3))

    # the signal: a glowing trace hovering over the field, spiking in the right third
    cu = bpy.data.curves.new("sig", "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("POLY")
    xs = [x * 0.25 for x in range(-44, 45)]
    sp.points.add(len(xs) - 1)
    for p, x in zip(sp.points, xs):
        base = 1.25 + 0.05 * math.sin(x * 2.3) + 0.03 * math.sin(x * 5.1)
        spike = 1.9 * math.exp(-((x - 3.1) / 0.28) ** 2) - 0.7 * math.exp(-((x - 3.75) / 0.2) ** 2)
        p.co = (x, 5.2 + 0.02 * x, base + spike, 1)
    cu.bevel_depth, cu.bevel_resolution = 0.022, 4
    cu.materials.append(mat("sig", (1, 1, 1), metallic=1, rough=0.05, emit=(1, 1, 1), strength=0.6))
    link(bpy.data.objects.new("sig", cu))

    aim = link(bpy.data.objects.new("aim", None))
    aim.location = (1.2, 6.5, 0.9)
    cam = camera((-1.4, -3.2, 2.1), lens=32, aim=aim)
    cam.data.dof.use_dof = True
    cam.data.dof.focus_object = aim
    cam.data.dof.aperture_fstop = 1.2
    for name, loc, e, s, col in (("back", (4, 16, 3.5), 2600, 10, (0.85, 0.88, 0.95)),
                                 ("glow", (3.1, 5.2, 2.6), 350, 1.5, (1.0, 0.65, 0.3)),
                                 ("top", (-4, 2, 7), 120, 8, (0.5, 0.65, 0.95))):
        L = bpy.data.lights.new(name, "AREA")
        L.energy, L.size, L.color = e, s, col
        o = link(bpy.data.objects.new(name, L))
        o.location = loc
        c = o.constraints.new("TRACK_TO")
        c.target = aim
        c.track_axis, c.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    render_to(out / "bg.png")


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    mode, out = argv[0], Path(argv[1]).resolve()
    fast = "--fast" in argv
    out.mkdir(parents=True, exist_ok=True)
    for m in (("mark", "splash", "bg") if mode == "all" else (mode,)):
        {"mark": do_mark, "splash": do_splash, "bg": do_bg}[m](out, fast)
