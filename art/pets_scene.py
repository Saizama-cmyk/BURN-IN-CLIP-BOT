"""Blender: the five ClipBot desktop pets, modelled and animated in code, rendered as frames.

    blender -b --factory-startup -P art/pets_scene.py -- <out_dir> [species,...] [--fast]
    extra flags: --acts=idle,walk  --samples=N  --size=N  --frames=N (cap frames/action)

Style: polished sterling chrome, black pebbled leather, gunmetal studs and our own four-point
"spike star" hardware. Every pet shares one tiny rig (root, body, eyes, arms, feet) so all
sixteen actions work for all of them; the species differ in shape and accessories.
Frames land in <out_dir>/<species>/<action>/NNN.png (art/build_pets.py packs them).
"""
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

SIZE = 320                     # sprite frame, px (the app reads the size from the manifest)
FPS = 12
SAMPLES = 320
SAMPLES_FAST = 8
TAU = 2 * math.pi
# looping actions carry ~1.5x the frames they used to, so motion reads smoothly at 12-15 fps
ACTIONS = {"idle": 24, "walk": 18, "look": 12, "hop": 10, "spin": 18, "wave": 12, "dance": 24,
           "stretch": 18, "cheer": 12, "sad": 12, "surprised": 8, "sleep": 24, "love": 10,
           "held": 8, "fall": 4, "land": 6}
SPECIES = ("blip", "ember", "moss", "nib", "glitch")


def srgb(c):
    return tuple(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92 for v in c)


def arg(prefix, default):
    for a in sys.argv:
        if a.startswith(prefix):
            return type(default)(a[len(prefix):])
    return default


def put(obj, name, value):
    """Set an optional socket/property only when this Blender build has it."""
    if hasattr(obj, "inputs") and not hasattr(obj, name):
        if name in obj.inputs:
            obj.inputs[name].default_value = value
    elif hasattr(obj, name):
        setattr(obj, name, value)


# ------------------------------------------------------------------------------------ scene
def reset(fast):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    cy = sc.cycles
    cy.samples = arg("--samples=", SAMPLES_FAST if fast else SAMPLES)
    put(cy, "use_adaptive_sampling", True)
    put(cy, "adaptive_threshold", 0.005)
    put(cy, "use_denoising", True)
    put(cy, "caustics_reflective", False)      # chrome next to emitters would otherwise fizz
    put(cy, "caustics_refractive", False)
    put(cy, "max_bounces", 8)
    put(cy, "glossy_bounces", 6)
    put(cy, "transmission_bounces", 4)
    put(cy, "transparent_max_bounces", 8)
    sc.render.use_persistent_data = True
    sc.render.resolution_x = sc.render.resolution_y = arg("--size=", SIZE)
    put(sc.render, "filter_width", 1.2)        # crisper highlights than the 1.5 default
    sc.render.film_transparent = True          # straight RGBA: no matte colour in the fringe
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    put(sc.render.image_settings, "compression", 100)
    sc.view_settings.view_transform = "Standard"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    picked = None
    for kind in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = kind
            prefs.refresh_devices()
            if any(d.type == kind for d in prefs.devices):
                for d in prefs.devices:
                    d.use = d.type == kind
                sc.cycles.device = "GPU"
                picked = kind
                break
        except TypeError:
            continue
    try:
        cy.denoiser = "OPTIX" if picked == "OPTIX" else "OPENIMAGEDENOISE"
    except TypeError:
        pass
    put(cy, "denoising_use_gpu", True)
    # studio sky: bright top, soft horizon band, dark floor, so chrome has something to reflect
    w = bpy.data.worlds.new("w")
    sc.world = w
    w.use_nodes = True
    nt = w.node_tree
    bg = nt.nodes["Background"]
    tc, sep, ramp = nt.nodes.new("ShaderNodeTexCoord"), nt.nodes.new("ShaderNodeSeparateXYZ"), \
        nt.nodes.new("ShaderNodeValToRGB")
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])
    nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    ramp.color_ramp.elements[0].position, ramp.color_ramp.elements[0].color = 0.30, (0.010, 0.010, 0.013, 1)
    ramp.color_ramp.elements[1].position, ramp.color_ramp.elements[1].color = 0.52, (1, 1, 1, 1)
    for pos, col in ((0.44, (0.05, 0.05, 0.07, 1)), (0.487, (0.30, 0.31, 0.36, 1)),
                     (0.66, (0.55, 0.57, 0.66, 1))):
        ramp.color_ramp.elements.new(pos).color = col
    nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 1.15
    return sc


def mat(name, color, metal=0.0, rough=0.4, emit=None, strength=0.0, bump=0.0, coat=0.0,
        rough_var=0.0, rough_scale=9.0, micro=0.0, sheen=0.0, grain=120.0):
    """Principled material plus optional pebble grain, micro-polish bump and roughness drift."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    p = nt.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*srgb(color), 1)
    p.inputs["Metallic"].default_value = metal
    p.inputs["Roughness"].default_value = rough
    put(p, "Coat Weight", coat)
    put(p, "Coat Roughness", 0.06)
    put(p, "Sheen Weight", sheen)
    put(p, "Sheen Roughness", 0.35)
    if emit:
        p.inputs["Emission Color"].default_value = (*srgb(emit), 1)
        p.inputs["Emission Strength"].default_value = strength
    if rough_var:                              # polish is never perfectly even
        n = nt.nodes.new("ShaderNodeTexNoise")
        n.inputs["Scale"].default_value = rough_scale
        n.inputs["Detail"].default_value = 5
        mr = nt.nodes.new("ShaderNodeMapRange")
        mr.inputs["From Min"].default_value = 0.32
        mr.inputs["From Max"].default_value = 0.68
        mr.inputs["To Min"].default_value = max(0.005, rough - rough_var)
        mr.inputs["To Max"].default_value = rough + rough_var
        nt.links.new(n.outputs["Fac"], mr.inputs["Value"])
        nt.links.new(mr.outputs["Result"], p.inputs["Roughness"])
    normal = None
    if bump:                                   # pebbled leather grain at two scales
        v, b = nt.nodes.new("ShaderNodeTexVoronoi"), nt.nodes.new("ShaderNodeBump")
        v.inputs["Scale"].default_value = grain
        b.inputs["Strength"].default_value = bump
        b.inputs["Distance"].default_value = 0.012
        nt.links.new(v.outputs["Distance"], b.inputs["Height"])
        v2, b2 = nt.nodes.new("ShaderNodeTexVoronoi"), nt.nodes.new("ShaderNodeBump")
        v2.inputs["Scale"].default_value = grain * 0.32
        b2.inputs["Strength"].default_value = bump * 0.5
        b2.inputs["Distance"].default_value = 0.02
        nt.links.new(v2.outputs["Distance"], b2.inputs["Height"])
        nt.links.new(b2.outputs["Normal"], b.inputs["Normal"])
        normal = b
    if micro:                                  # fine hand-polish swirl on the metal
        n2, b3 = nt.nodes.new("ShaderNodeTexNoise"), nt.nodes.new("ShaderNodeBump")
        n2.inputs["Scale"].default_value = 260
        n2.inputs["Detail"].default_value = 2
        b3.inputs["Strength"].default_value = micro
        b3.inputs["Distance"].default_value = 0.0015
        nt.links.new(n2.outputs["Fac"], b3.inputs["Height"])
        if normal:
            nt.links.new(normal.outputs["Normal"], b3.inputs["Normal"])
        normal = b3
    if normal:
        nt.links.new(normal.outputs["Normal"], p.inputs["Normal"])
    return m


CHROME = GUN = LEATHER = EYE = DARK = None


def materials():
    global CHROME, GUN, LEATHER, EYE, DARK
    CHROME = mat("chrome", (0.95, 0.96, 0.98), metal=1, rough=0.055, rough_var=0.035, micro=0.18)
    GUN = mat("gunmetal", (0.30, 0.31, 0.35), metal=1, rough=0.24, rough_var=0.07, micro=0.25)
    LEATHER = mat("leather", (0.014, 0.014, 0.017), rough=0.40, bump=0.26, coat=0.24,
                  rough_var=0.09, rough_scale=26)
    EYE = mat("eye", (1, 1, 1), emit=(1, 1, 1), strength=4.0)
    DARK = mat("dark", (0.012, 0.012, 0.014), rough=0.30, coat=0.4)


def link(o):
    bpy.context.scene.collection.objects.link(o)
    return o


def empty(name, parent=None, loc=(0, 0, 0)):
    o = link(bpy.data.objects.new(name, None))
    o.parent, o.location = parent, loc
    return o


def bevel(o, width, segments=4):
    b = o.modifiers.new("b", "BEVEL")
    b.width, b.segments = width, segments
    put(b, "harden_normals", True)
    return o


def mesh(name, bm, material, parent, smooth=True, subsurf=0):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(material)
    o = link(bpy.data.objects.new(name, me))
    o.parent = parent
    for p in me.polygons:
        p.use_smooth = smooth
    if subsurf:
        m = o.modifiers.new("s", "SUBSURF")
        m.levels = m.render_levels = subsurf
    return o


def sphere(name, r, material, parent, loc=(0, 0, 0), scale=(1, 1, 1), seg=48, rings=24, subsurf=0):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=rings, radius=r)
    o = mesh(name, bm, material, parent, subsurf=subsurf)
    o.location, o.scale = loc, scale
    return o


def cube(name, size, material, parent, loc=(0, 0, 0), bevel_w=0.0, subsurf=0, seg=4):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    for v in bm.verts:
        v.co = Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]))
    o = mesh(name, bm, material, parent, smooth=bool(subsurf or bevel_w), subsurf=subsurf)
    o.location = loc
    if bevel_w:
        bevel(o, bevel_w, seg)
    return o


def torus(name, R, r, material, parent, loc=(0, 0, 0), rot=(0, 0, 0), seg=56, rings=14, arc=1.0):
    """Band / rim / chain link. ``arc`` < 1 leaves it open (a strap over the top)."""
    bm = bmesh.new()
    n = max(4, int(seg * arc))
    span = TAU * arc
    cols = []
    for i in range(n):
        a = span * (i / n if arc >= 1 else i / (n - 1))
        row = []
        for j in range(rings):
            b = j * TAU / rings
            row.append(bm.verts.new(((R + r * math.cos(b)) * math.cos(a),
                                     (R + r * math.cos(b)) * math.sin(a), r * math.sin(b))))
        cols.append(row)
    for i in range(n if arc >= 1 else n - 1):
        a0, a1 = cols[i % n], cols[(i + 1) % n]
        for j in range(rings):
            bm.faces.new([a0[j], a1[j], a1[(j + 1) % rings], a0[(j + 1) % rings]])
    o = mesh(name, bm, material, parent)
    o.location, o.rotation_euler = loc, rot
    return o


def stud(name, r, material, parent, loc, rot=(0, 0, 0), kind="pyr"):
    """Our hardware: a squat four-sided pyramid stud, or a domed rivet on a seat ring."""
    if kind == "pyr":
        bm = bmesh.new()
        base = [bm.verts.new((x * r, y * r, 0)) for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        tip = bm.verts.new((0, 0, r * 1.15))
        bm.faces.new(list(reversed(base)))
        for i in range(4):
            bm.faces.new([base[i], base[(i + 1) % 4], tip])
        o = mesh(name, bm, material, parent, smooth=False)
        bevel(o, r * 0.22, 3)
    else:
        o = sphere(name, r, material, parent, scale=(1, 1, 0.72), seg=24, rings=12)
        torus(name + "_seat", r * 1.2, r * 0.16, material, parent, loc, rot, seg=24, rings=8)
    o.location, o.rotation_euler = loc, rot
    return o


def stud_ring(parent, n, radius, z, material, r=0.04, tilt=0.0, kinds=("pyr",), start=0.0,
              span=TAU):
    for i in range(n):
        a = start + (span * i / n if span >= TAU else span * i / max(1, n - 1))
        stud(f"stud{i}", r, material, parent, (math.cos(a) * radius, math.sin(a) * radius, z),
             (math.pi / 2 - tilt, 0, a + math.pi / 2), kinds[i % len(kinds)])


def surf(r, theta, phi):
    """A point on a sphere of radius r - keeps trim sitting on the skin, not in it."""
    return (r * math.sin(phi) * math.cos(theta), r * math.sin(phi) * math.sin(theta),
            r * math.cos(phi))


def stitches(parent, material, pts, r=0.016):
    for i, (x, y, z) in enumerate(pts):
        s = sphere(f"st{i}", r, material, parent, (x, y, z), (1.0, 1.0, 0.45), seg=12, rings=6)
        s.rotation_euler = (0, 0, i * 0.4)


def chain(parent, material, start, n, step, R=0.05, r=0.014):
    for i in range(n):
        torus(f"lnk{i}", R, r, material, parent, (start[0], start[1], start[2] - i * step),
              (math.pi / 2, 0, 0) if i % 2 else (0, math.pi / 2, 0), seg=22, rings=8)


def spike_star(name, r, material, parent, loc, rot=(math.pi / 2, 0, 0)):
    """Our hardware mark: a four-point star with flared tips and a round boss (original)."""
    bm = bmesh.new()
    pts = []
    for i in range(16):
        a = i * math.pi / 8
        k = (1.0, 0.34, 0.5, 0.34)[i % 4]
        pts.append((math.cos(a) * r * k, math.sin(a) * r * k))
    top = [bm.verts.new((x, y, r * 0.12)) for x, y in pts]
    bot = [bm.verts.new((x, y, -r * 0.12)) for x, y in pts]
    bm.faces.new(top)
    bm.faces.new(list(reversed(bot)))
    for i in range(len(pts)):
        j = (i + 1) % len(pts)
        bm.faces.new([top[i], bot[i], bot[j], top[j]])
    o = mesh(name, bm, material, parent, smooth=False)
    bevel(o, r * 0.07, 3)
    o.location, o.rotation_euler = loc, rot
    sphere(name + "_boss", r * 0.2, material, o, (0, 0, r * 0.15), seg=20, rings=10)
    torus(name + "_ring", r * 0.38, r * 0.05, material, o, (0, 0, r * 0.12), seg=28, rings=8)
    return o


def cuff(parent, R, material, loc, rot=(0, 0, 0)):
    """A chunky studded band - reads as a bracelet even at sprite size."""
    t = torus("cuff", R, R * 0.28, material, parent, loc, rot, seg=32, rings=10)
    stud("cuffstud", R * 0.30, material, t, (0, -R * 1.02, 0), (-math.pi / 2, 0, 0))
    return t


# ------------------------------------------------------------------------------------ species
def rig(root):
    """Shared limbs: eye pivots, shoulder pivots with arms, feet."""
    r = {"root": root}
    r["body"] = empty("bodyp", root)
    r["eyes"] = [empty("eyeL", r["body"]), empty("eyeR", r["body"])]
    r["arms"] = [empty("armL", r["body"]), empty("armR", r["body"])]
    r["feet"] = [empty("footL", root), empty("footR", root)]
    return r


def build(species):
    root = empty("root")
    r = rig(root)
    body, (eL, eR), (aL, aR), (fL, fR) = r["body"], r["eyes"], r["arms"], r["feet"]

    def limbs(shoulder_x, shoulder_z, arm_mat, foot_mat, foot_x=0.28, arm_len=0.3, toe=None):
        band = GUN if arm_mat is CHROME else CHROME
        for sgn, a in ((-1, aL), (1, aR)):
            a.location = (sgn * shoulder_x, 0, shoulder_z)
            sphere("arm", 0.085, arm_mat, a, (sgn * 0.05, 0, -arm_len * 0.62), (1, 1, 2.0))
            cuff(a, 0.092, band, (sgn * 0.05, 0, -arm_len * 1.02), (0, 0, 0))
        for sgn, f in ((-1, fL), (1, fR)):
            f.location = (sgn * foot_x, -0.05, 0.08)
            sphere("foot", 0.11, foot_mat, f, scale=(1.1, 1.4, 0.7))
            if toe:
                c = sphere("toe", 0.078, toe, f, (0, -0.08, -0.014), (1.05, 0.95, 0.6))
                c.rotation_euler = (0.25, 0, 0)

    if species == "blip":           # chrome CRT with a black glass face and pixel eyes
        glass = mat("glass", (0.008, 0.008, 0.011), rough=0.16, coat=0.35, rough_var=0.03,
                    emit=(0.20, 0.32, 0.58), strength=0.12)      # faint phosphor in the tube
        cube("shell", (1.1, 0.95, 0.9), CHROME, body, (0, 0, 0.72), bevel_w=0.14, seg=8)
        cube("bezel", (0.99, 0.05, 0.81), CHROME, body, (0, -0.465, 0.74), bevel_w=0.018, seg=5)
        cube("screen", (0.87, 0.03, 0.67), glass, body, (0, -0.487, 0.74), bevel_w=0.06, seg=5)
        for sgn, e in ((-1, eL), (1, eR)):       # pixel eyes sit just proud of the glass
            e.location = (sgn * 0.19, -0.508, 0.8)
            cube("px", (0.132, 0.016, 0.172), EYE, e, bevel_w=0.003, seg=2)
        for i in range(5):                       # studded trim above and below the face
            x = -0.36 + i * 0.18
            stud("topstud", 0.042, GUN, body, (x, -0.462, 1.135), (-math.pi / 2, 0, 0))
            stud("botstud", 0.042, GUN, body, (x, -0.462, 0.345), (-math.pi / 2, 0, 0))
        for sgn in (-1, 1):                      # vent ribs and an engraved star on the sides
            for i in range(5):
                cube("rib", (0.018, 0.46, 0.032), GUN, body,
                     (sgn * 0.556, 0.03, 0.50 + i * 0.115), bevel_w=0.007, seg=2)
            spike_star("sidestar", 0.115, GUN, body, (sgn * 0.558, 0.0, 1.0),
                       (0, sgn * math.pi / 2, 0))
        stalk = empty("ant", body, (0.22, 0, 1.17))
        cube("stalk", (0.036, 0.036, 0.34), GUN, stalk, (0, 0, 0.17), bevel_w=0.01, seg=2)
        for i in range(4):                       # coil at the base of the antenna
            torus("coil", 0.062, 0.016, CHROME, stalk, (0, 0, 0.05 + i * 0.045), seg=24, rings=8)
        spike_star("antstar", 0.13, CHROME, stalk, (0, 0, 0.4))
        for x in (-0.47, 0.47):                  # corner rivets on seats
            for z in (0.4, 1.06):
                stud("rivet", 0.038, GUN, body, (x, -0.478, z), (-math.pi / 2, 0, 0), "dome")
        cube("band", (1.16, 1.0, 0.10), CHROME, body, (0, 0, 0.30), bevel_w=0.022, seg=4)
        limbs(0.58, 0.72, GUN, LEATHER, toe=CHROME)
    elif species == "ember":        # chrome flame drop with a live core; floats, no feet
        hot = mat("hotchrome", (1, 0.74, 0.46), metal=1, rough=0.085, rough_var=0.04, micro=0.2)
        nt = hot.node_tree                       # emission hottest at the belly, cooling to the tip
        p = nt.nodes["Principled BSDF"]
        tc, sep = nt.nodes.new("ShaderNodeTexCoord"), nt.nodes.new("ShaderNodeSeparateXYZ")
        mr, ramp = nt.nodes.new("ShaderNodeMapRange"), nt.nodes.new("ShaderNodeValToRGB")
        bw = nt.nodes.new("ShaderNodeValToRGB")
        nt.links.new(tc.outputs["Object"], sep.inputs[0])
        nt.links.new(sep.outputs["Z"], mr.inputs["Value"])
        mr.inputs["From Min"].default_value = -0.5
        mr.inputs["From Max"].default_value = 1.1
        nt.links.new(mr.outputs["Result"], ramp.inputs["Fac"])
        nt.links.new(mr.outputs["Result"], bw.inputs["Fac"])
        ramp.color_ramp.elements[0].color = (*srgb((1, 0.30, 0.05)), 1)
        ramp.color_ramp.elements[1].position = 0.85
        ramp.color_ramp.elements[1].color = (*srgb((1, 0.86, 0.55)), 1)
        bw.color_ramp.elements[0].position, bw.color_ramp.elements[0].color = 0.05, (1.2, 1.2, 1.2, 1)
        bw.color_ramp.elements[1].position, bw.color_ramp.elements[1].color = 0.80, (0.16, 0.16, 0.16, 1)
        nt.links.new(ramp.outputs["Color"], p.inputs["Emission Color"])
        nt.links.new(bw.outputs["Color"], p.inputs["Emission Strength"])
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=32, radius=0.5)
        for v in bm.verts:
            a = math.atan2(v.co.y, v.co.x)
            if v.co.z > 0:
                k = v.co.z / 0.5
                v.co.x *= 1 - 0.75 * k ** 1.6
                v.co.y *= 1 - 0.75 * k ** 1.6
                v.co.z *= 1 + 1.1 * k ** 2
            ripple = 1 + 0.022 * math.sin(a * 7 + v.co.z * 5)     # molten surface waver
            v.co.x *= ripple
            v.co.y *= ripple
        o = mesh("flame", bm, hot, body, subsurf=2)
        o.location = (0, 0, 0.62)
        for sgn in (-1, 1):                      # little flame licks off the tip
            lick = sphere("lick", 0.07, hot, body, (sgn * 0.075, 0, 1.28), (0.5, 0.5, 2.0))
            lick.rotation_euler = (0, sgn * 0.5, 0)
        for sgn, e in ((-1, eL), (1, eR)):
            e.location = (sgn * 0.15, -0.44, 0.72)
            sphere("eye", 0.07, DARK, e, scale=(1, 0.6, 1.5))
            brow = cube("brow", (0.115, 0.05, 0.022), GUN, e, (0, -0.012, 0.085), bevel_w=0.008, seg=2)
            brow.rotation_euler = (0, sgn * 0.25, 0)
        core = empty("core", body, (0, -0.43, 0.44))              # the live core, behind a port
        sphere("lens", 0.085, mat("core", (1, 0.5, 0.15), emit=(1, 0.42, 0.10), strength=14),
               core, (0, -0.03, 0), (1, 0.55, 1))
        torus("port", 0.105, 0.024, GUN, core, (0, -0.05, 0), (math.pi / 2, 0, 0), seg=36, rings=10)
        for i in range(6):
            a = i * TAU / 6 + 0.5
            stud("portstud", 0.025, CHROME, core, (math.cos(a) * 0.145, 0.01, math.sin(a) * 0.145),
                 (-math.pi / 2, 0, 0))
        spike_star("shoulder", 0.09, GUN, body, (0.31, -0.36, 0.70), (math.pi / 2, 0, -0.6))
        torus("hemband", 0.315, 0.035, GUN, body, (0, 0, 0.24), seg=44, rings=10)
        stud_ring(body, 8, 0.325, 0.24, CHROME, 0.034, tilt=-0.8, kinds=("pyr", "dome"))
        limbs(0.43, 0.6, CHROME, CHROME, arm_len=0.24)
        for f in (fL, fR):
            f.hide_render = True
            for c in f.children:
                c.hide_render = True
    elif species == "moss":         # black leather blob, chrome sprout and a studded collar
        blob = sphere("blob", 0.55, LEATHER, body, (0, 0, 0.5), (1.1, 1.0, 0.85), subsurf=2)
        stitches(blob, GUN, [surf(0.558, math.radians(-118), math.radians(38 + i * 12))
                             for i in range(8)])
        leaf = empty("leaf", body, (0, 0, 0.98))
        cube("stem", (0.045, 0.045, 0.24), CHROME, leaf, (0, 0, 0.11), bevel_w=0.012, seg=3)
        for i in range(3):
            torus("stemring", 0.049, 0.012, GUN, leaf, (0, 0, 0.04 + i * 0.07), seg=22, rings=8)
        for sgn in (-1, 1):
            lf = sphere("leafs", 0.145, CHROME, leaf, (sgn * 0.135, 0, 0.235), (1.3, 0.35, 0.62))
            lf.rotation_euler = (0, sgn * 0.5, 0)
            cube("vein", (0.013, 0.013, 0.145), GUN, lf, (0, 0, 0), bevel_w=0.004, seg=2)
        top = sphere("leaftop", 0.1, CHROME, leaf, (0, 0.02, 0.36), (0.75, 0.3, 1.2))
        top.rotation_euler = (0.25, 0, 0)
        collar = empty("collarp", body, (0, 0, 0.345))  # oval, to match the squashed blob
        collar.scale = (1.1, 1.0, 1.0)
        torus("collar", 0.525, 0.058, GUN, collar, seg=56, rings=12)
        stud_ring(collar, 12, 0.538, 0.0, CHROME, 0.046, tilt=-0.32, kinds=("pyr", "dome"))
        tag = cube("tag", (0.15, 0.03, 0.19), CHROME, body, (0, -0.522, 0.175), bevel_w=0.01, seg=4)
        spike_star("tagstar", 0.055, GUN, tag, (0, -0.03, 0), (math.pi / 2, 0, 0))
        torus("tagloop", 0.036, 0.012, CHROME, body, (0, -0.519, 0.295), (math.pi / 2, 0, 0),
              seg=22, rings=8)
        for sgn, e in ((-1, eL), (1, eR)):
            e.location = (sgn * 0.19, -0.5, 0.6)
            sphere("eye", 0.085, EYE, e, scale=(1, 0.5, 1.2))
            torus("rim", 0.098, 0.016, CHROME, e, (0, 0.012, 0), (math.pi / 2, 0, 0), seg=28, rings=8)
            stud("brow", 0.03, GUN, e, (0, -0.02, 0.155), (-math.pi / 2, 0, 0))
        limbs(0.58, 0.45, LEATHER, CHROME, foot_x=0.3)
    elif species == "nib":          # round leather bird: chrome beak, chrome headphones
        sphere("bird", 0.52, LEATHER, body, (0, 0, 0.6), (1, 0.95, 1.05), subsurf=2)
        for sgn in (-1, 1):                      # folded wings, stitched along the edge
            wg = sphere("wing", 0.27, LEATHER, body, (sgn * 0.44, 0.05, 0.55), (0.32, 0.95, 1.05))
            wg.rotation_euler = (0, sgn * 0.3, 0)
            stitches(wg, GUN, [surf(0.275, (0 if sgn > 0 else math.pi) + sgn * 0.35,
                                    math.radians(55 + i * 18)) for i in range(5)], r=0.05)
        for sgn in (-1, 1):                      # chrome beak: two halves with a seam
            half = sphere("beak", 0.12, CHROME, body, (0, -0.52, 0.585 + sgn * 0.037),
                          (1, 1.4, 0.42))
            half.rotation_euler = (0.2 + sgn * 0.09, 0, 0)
        band = empty("band", body, (0, 0, 0.6))
        bm = bmesh.new()
        bmesh.ops.create_circle(bm, segments=48, radius=0.56)
        for v in bm.verts:
            v.co = Vector((v.co.x, 0, v.co.y))
        ring = mesh("bandm", bm, CHROME, band)
        ring.modifiers.new("t", "SKIN")
        ring.modifiers.new("s", "SUBSURF")
        for v in ring.data.skin_vertices[0].data:
            v.radius = (0.038, 0.038)
        pad = torus("pad", 0.52, 0.05, LEATHER, band, (0, 0, 0), (math.pi / 2, 0, 0), seg=48,
                    rings=12, arc=0.3)
        pad.rotation_euler = (math.pi / 2, math.radians(-124), 0)
        for sgn in (-1, 1):
            cup = sphere("cup", 0.17, GUN, band, (sgn * 0.54, 0, 0), (0.6, 1, 1))
            torus("cuprim", 0.166, 0.028, CHROME, cup, (sgn * 0.055, 0, 0),
                  (0, sgn * math.pi / 2, 0), seg=40, rings=10)
            torus("cuppad", 0.15, 0.042, LEATHER, cup, (-sgn * 0.085, 0, 0),
                  (0, sgn * math.pi / 2, 0), seg=40, rings=10)
            spike_star("cupstar", 0.08, CHROME, cup, (sgn * 0.125, 0, 0), (0, sgn * math.pi / 2, 0))
        chain(body, CHROME, (0.50, -0.22, 0.46), 5, 0.072)
        for i, (tall, x) in enumerate(((1.5, 0.05), (1.2, -0.07), (1.05, 0.16))):
            tuft = sphere("tuft", 0.095, LEATHER, body, (x, 0.02, 1.09), (0.62, 0.62, tall))
            tuft.rotation_euler = (0, 0.45 - i * 0.4, 0)
        for sgn, e in ((-1, eL), (1, eR)):
            e.location = (sgn * 0.2, -0.46, 0.76)
            sphere("eyew", 0.1, EYE, e)
            sphere("pupil", 0.05, DARK, e, (0, -0.07, 0))
            sphere("spark", 0.017, mat("spark", (1, 1, 1), emit=(1, 1, 1), strength=6), e,
                   (sgn * 0.032, -0.093, 0.038))
            torus("eyerim", 0.103, 0.014, CHROME, e, (0, 0.01, 0), (math.pi / 2, 0, 0),
                  seg=30, rings=8)
        limbs(0.5, 0.55, LEATHER, CHROME, toe=CHROME)
    elif species == "glitch":       # chrome sheet ghost with a wavy hem and slit eyes
        pearl = mat("pearl", (0.84, 0.80, 0.92), metal=1, rough=0.21, rough_var=0.06,
                    micro=0.15, coat=0.5)
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=32, radius=0.5)
        for v in bm.verts:
            a = math.atan2(v.co.y, v.co.x)
            if v.co.z < 0:                                        # the sheet flares into a skirt
                k = -v.co.z / 0.5
                rn = math.hypot(v.co.x, v.co.y)
                if rn > 1e-6:
                    rt = 0.5 * (1 + 0.25 * k - 0.70 * k ** 6) * (1 + 0.065 * math.sin(a * 8))
                    v.co.x *= rt / rn
                    v.co.y *= rt / rn
                v.co.z = -0.82 * k - 0.055 * k * math.sin(a * 8)   # ... with a wavy hem
        o = mesh("ghost", bm, pearl, body, subsurf=2)
        sol = o.modifiers.new("sol", "SOLIDIFY")                  # the sheet has real thickness
        sol.thickness, sol.offset = 0.035, 1.0
        put(sol, "use_rim", True)
        o.location = (0, 0, 0.82)
        violet = mat("violet", (0.72, 0.52, 1), emit=(0.80, 0.52, 1), strength=1.7)
        for sgn, e in ((-1, eL), (1, eR)):       # sat on the surface normal, tilted to match it
            nx, ny = sgn * 0.175, -0.461
            e.location = (nx * 1.035, ny * 1.035, 0.9)
            e.rotation_euler = (0, 0, sgn * 0.363)
            cube("socket", (0.25, 0.03, 0.105), DARK, e, (0, 0.012, 0), bevel_w=0.008, seg=3)
            cube("slit", (0.20, 0.03, 0.072), violet, e, (0, -0.013, 0), bevel_w=0.004, seg=2)
        torus("neck", 0.535, 0.03, CHROME, body, (0, 0, 0.62), seg=48, rings=10)
        stud_ring(body, 9, 0.545, 0.50, GUN, 0.036, tilt=-0.1, start=math.radians(200),
                  span=math.radians(140))
        spike_star("brand", 0.1, GUN, body, (0.28, -0.42, 0.55), (math.pi / 2, 0, 0.3))
        chain(body, CHROME, (-0.34, -0.40, 0.78), 4, 0.07)
        limbs(0.5, 0.75, CHROME, CHROME, arm_len=0.22)
        for f in (fL, fR):
            f.hide_render = True
            for c in f.children:
                c.hide_render = True
    return r


# ------------------------------------------------------------------------------------ motion
def ease(t):
    return t * t * (3 - 2 * t)


def pose(r, act, t, species):
    """Set the rig for action ``act`` at loop phase t in [0,1)."""
    body, root = r["body"], r["root"]
    float_kind = species in ("ember", "glitch")
    tau = TAU
    z, rz, ry, rx, sq, eye, arms, feet = 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, [(0, 0), (0, 0)], [0, 0]
    if float_kind:
        z += 0.06 + 0.04 * math.sin(tau * t)
    if act == "idle":
        sq = 1 + 0.025 * math.sin(tau * t)
        eye = 0.1 if 0.78 < t < 0.86 else 1
        arms = [(0.08 * math.sin(tau * t), 0)] * 2
    elif act == "walk":
        rz = math.radians(-38)
        z += 0.05 * abs(math.sin(tau * t))
        ry = math.radians(5) * math.sin(tau * t)
        feet = [0.12 * math.sin(tau * t), -0.12 * math.sin(tau * t)]
        arms = [(0.5 * math.sin(tau * t), 0), (-0.5 * math.sin(tau * t), 0)]
    elif act == "look":
        rz = math.radians(35) * math.sin(tau * t)
    elif act in ("hop", "cheer", "love"):
        k = math.sin(math.pi * t)
        z += (0.32 if act != "love" else 0.18) * k
        sq = 1 - 0.18 * (1 - k) ** 6 + 0.08 * k
        if act == "cheer":
            arms = [(-2.6, 0.3), (2.6, 0.3)]
        if act == "love":
            eye = 0.35
    elif act == "spin":
        rz = tau * ease(t)
        z += 0.12 * math.sin(math.pi * t)
    elif act == "wave":
        arms = [(0, 0), (2.4 + 0.5 * math.sin(2 * tau * t), 0.3)]
        rz = math.radians(8)
    elif act == "dance":
        ry = math.radians(12) * math.sin(tau * t * 2)
        z += 0.1 * abs(math.sin(tau * t * 2))
        arms = [(-2.2 * (0.5 + 0.5 * math.sin(tau * t * 2)), 0), (2.2 * (0.5 - 0.5 * math.sin(tau * t * 2)), 0)]
        rz = math.radians(20) * math.sin(tau * t)
    elif act == "stretch":
        k = math.sin(math.pi * t)
        sq = 1 + 0.22 * k
        arms = [(-2.8 * k, 0), (2.8 * k, 0)]
        eye = 1 - 0.8 * k
    elif act == "sad":
        rx = math.radians(14)
        sq = 0.94
        eye = 0.4
        arms = [(0.25, 0), (-0.25, 0)]
        z -= 0.02 * math.sin(tau * t)
    elif act == "surprised":
        k = math.sin(math.pi * min(1, t * 2))
        sq = 1 + 0.12 * k
        z += 0.18 * k
        eye = 1.5
        arms = [(-1.6, 0), (1.6, 0)]
    elif act == "sleep":
        sq = 1 + 0.04 * math.sin(tau * t)
        eye = 0.08
        rx = math.radians(10)
        ry = math.radians(6)
        z = z * 0.3
    elif act == "held":
        ry = math.radians(10) * math.sin(tau * t)
        z += 0.05
        sq = 1.08
        eye = 1.3
        arms = [(-1.2, 0), (1.2, 0)]
        feet = [0.06 * math.sin(tau * t), -0.06 * math.sin(tau * t)]
    elif act == "fall":
        sq = 1.12
        eye = 1.4
        arms = [(-2.7, 0), (2.7, 0)]
    elif act == "land":
        k = t
        sq = 0.72 + 0.28 * ease(k)
    root.location = (0, 0, z)
    root.rotation_euler = (0, 0, rz)
    body.rotation_euler = (rx, ry, 0)
    body.scale = (1 / math.sqrt(sq), 1 / math.sqrt(sq), sq)
    for e in r["eyes"]:
        e.scale = (1, 1, max(0.05, eye))
    for a, (swing, spread) in zip(r["arms"], arms):
        a.rotation_euler = (0, swing, spread * (1 if a.name.endswith("L") else -1))
    for f, fy in zip(r["feet"], feet):
        f.location.y = -0.05 + fy
        f.location.z = 0.08 + max(0.0, fy) * 0.5


# ------------------------------------------------------------------------------------ stage
def studio(aim):
    """Key / rim / fill / strip lights, plus a shadow-catcher disc for the contact shadow."""
    for name, loc, e, sx, sy, col in (
            ("key", (-3, -4, 5), 520, 3.0, 3.0, (1, 0.99, 0.97)),
            ("rim", (2.6, 3.6, 3.4), 900, 2.2, 2.2, (0.84, 0.89, 1.0)),
            ("fill", (4, -3, 1), 170, 3.0, 3.0, (0.95, 0.97, 1)),
            ("strip", (-1.8, -3.4, 4.2), 420, 0.14, 6.0, (1, 1, 1)),
            ("kick", (0, -3.2, -0.7), 70, 2.0, 2.0, (1, 0.96, 0.92))):
        L = bpy.data.lights.new(name, "AREA")
        L.energy, L.color = e, col
        L.shape = "RECTANGLE"
        L.size, L.size_y = sx, sy
        o = link(bpy.data.objects.new(name, L))
        o.location = loc
        k = o.constraints.new("TRACK_TO")
        k.target, k.track_axis, k.up_axis = aim, "TRACK_NEGATIVE_Z", "UP_Y"
    bm = bmesh.new()
    bmesh.ops.create_circle(bm, segments=48, radius=0.66, cap_ends=True)
    g = mesh("ground", bm, DARK, None, smooth=False)
    g.location = (0, 0, 0.001)
    put(g, "is_shadow_catcher", True)
    for flag in ("visible_glossy", "visible_diffuse", "visible_transmission",
                 "visible_volume_scatter"):
        put(g, flag, False)
    return g


def render_species(species, out: Path, fast: bool):
    sc = reset(fast)
    materials()
    r = build(species)
    aim = empty("aim", None, (0, 0, 0.92))
    cd = bpy.data.cameras.new("cam")
    cd.type, cd.ortho_scale = "ORTHO", 2.5
    cam = link(bpy.data.objects.new("cam", cd))
    cam.location = (1.6, -5.2, 2.2)
    c = cam.constraints.new("TRACK_TO")
    c.target, c.track_axis, c.up_axis = aim, "TRACK_NEGATIVE_Z", "UP_Y"
    sc.camera = cam
    studio(aim)
    acts = next((a.split("=", 1)[1].split(",") for a in sys.argv if a.startswith("--acts=")), ACTIONS)
    cap = arg("--frames=", 0)
    for act in acts:
        n = ACTIONS[act]
        folder = out / species / act
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(min(n, cap) if cap else n):
            pose(r, act, i / n, species)
            sc.render.filepath = str(folder / f"{i:03d}.png")
            bpy.ops.render.render(write_still=True)
        print(f"[pets] {species}/{act}: {n} frames", flush=True)


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:]
    out = Path(argv[0]).resolve()
    only = [a for a in argv[1:] if not a.startswith("--")]
    for sp in (only[0].split(",") if only else SPECIES):
        render_species(sp, out, "--fast" in argv)
