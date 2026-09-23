"""Original Ashvane identity: two leaning vanes cut by one hot slash. Render in background mode.

blender -b --factory-startup -P art/ashvane_scene.py -- all art/out_ashvane [--fast]
Modes: mark, splash, bg, all. The .blend files are saved beside this script.

The mark reads as an A (for Ashvane) and as a cut: two tapered satin-metal vanes lean into a
peak, and an ember slash passes clean through them where the A's crossbar would be. The splash
brings the vanes together, strikes the slash across and lets a few embers drift off as ash.
Geometry and lighting are deterministic; no external textures or fonts are required.
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

EMBER = (1.0, 0.537, 0.310)
INK = (0.953, 0.941, 0.914)
EMBER_DEEP = (1.0, 0.40, 0.14)   # the slash; AgX lifts it back toward EMBER on screen
ASH = (0.16, 0.155, 0.15)
FPS = 24
FRAMES = 48

# Outlines in the camera's X/Z plane, unit square about the origin. Shared with clipbot/icon.py,
# which draws the same silhouette flat for 16-32 px icons.
LEFT_VANE = [(-.80, -.80), (-.50, -.80), (-.035, .80), (-.20, .80)]
RIGHT_VANE = [(-x, z) for x, z in reversed(LEFT_VANE)]
SLASH_TILT = math.radians(11)
SLASH = [(-.74, -.07), (.48, -.07), (.80, 0.), (.48, .07), (-.74, .07)]
SLASH_AT = (0., -.17)


def linear(color):
    return tuple(((c + .055) / 1.055) ** 2.4 if c > .04045 else c / 12.92 for c in color)


def setup(width, height, samples=48, transparent=True):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.render.use_persistent_data = True
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = transparent
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA' if transparent else 'RGB'
    scene.render.fps = FPS
    scene.view_settings.view_transform = 'AgX'
    scene.world = bpy.data.worlds.new('Studio world')
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs[0].default_value = (.20, .20, .20, 1)
    scene.world.node_tree.nodes['Background'].inputs[1].default_value = .45
    prefs = bpy.context.preferences.addons['cycles'].preferences
    for kind in ('OPTIX', 'CUDA'):
        try:
            prefs.compute_device_type = kind
            prefs.refresh_devices()
            if any(device.type == kind for device in prefs.devices):
                for device in prefs.devices:
                    device.use = device.type == kind
                scene.cycles.device = 'GPU'
                print('[Ashvane] Render device:', kind)
                break
        except (TypeError, RuntimeError):
            continue
    return scene


def material(name, color, metal=0., rough=.35, emission=0.):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    node = mat.node_tree.nodes['Principled BSDF']
    node.inputs['Base Color'].default_value = (*linear(color), 1)
    node.inputs['Metallic'].default_value = metal
    node.inputs['Roughness'].default_value = rough
    if emission:
        node.inputs['Emission Color'].default_value = (*linear(EMBER if color == ASH else color), 1)
        node.inputs['Emission Strength'].default_value = emission
    return mat


def prism(name, outline, depth, mat, bevel, parent=None, y0=0.):
    """Extrude an X/Z outline along Y as one closed mesh (no seams at the corners)."""
    vertices = [(x, y, z) for y in (y0 - depth / 2, y0 + depth / 2) for x, z in outline]
    n = len(outline)
    faces = [tuple(reversed(range(n))), tuple(range(n, 2 * n))]
    faces += [(i, (i + 1) % n, (i + 1) % n + n, i + n) for i in range(n)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    obj.parent = parent
    mod = obj.modifiers.new('Machined edge', 'BEVEL')
    mod.width, mod.segments = bevel, 5
    obj.modifiers.new('Weighted face normals', 'WEIGHTED_NORMAL')
    return obj


def block(name, location, scale, mat, bevel=.04, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    mod = obj.modifiers.new('Machined edge', 'BEVEL')
    mod.width, mod.segments = bevel, 4
    obj.modifiers.new('Weighted face normals', 'WEIGHTED_NORMAL')
    obj.parent = parent
    return obj


def aim(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat('-Z', 'Y').to_euler()


def light(name, location, energy, size, color=(1, 1, 1), target=(0, 0, 0)):
    data = bpy.data.lights.new(name, 'AREA')
    data.energy, data.size, data.color = energy, size, color
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    aim(obj, target)
    return obj


def camera(location, target=(0, 0, 0), ortho=2.8):
    data = bpy.data.cameras.new('Camera')
    data.type, data.ortho_scale = 'ORTHO', ortho
    obj = bpy.data.objects.new('Camera', data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    aim(obj, target)
    bpy.context.scene.camera = obj
    return obj


def mark():
    root = bpy.data.objects.new('Ashvane | vanes and slash', None)
    bpy.context.collection.objects.link(root)
    silver = material('Warm satin aluminum', INK, .74, .26)
    ember = material('Ember slash', EMBER_DEEP, .2, .28, .3)
    left = prism('Left vane', LEFT_VANE, .26, silver, .04, root)
    right = prism('Right vane', RIGHT_VANE, .26, silver, .04, root)
    # The slash is its own pivot so the splash can strike it across from its left end.
    pivot = bpy.data.objects.new('Slash pivot', None)
    bpy.context.collection.objects.link(pivot)
    pivot.parent = root
    left_end = SLASH[0][0]
    pivot.location = (left_end * math.cos(SLASH_TILT) + SLASH_AT[0], 0,
                      left_end * math.sin(SLASH_TILT) + SLASH_AT[1])
    pivot.rotation_euler = (0, -SLASH_TILT, 0)
    slash = prism('Hot slash', [(x - left_end, z) for x, z in SLASH], .42, ember, .025, pivot, -.02)
    return root, left, right, pivot, slash


def studio():
    light('Softbox', (-3, -4, 5), 520, 5)
    light('Edge strip', (3, 1, 3), 740, 3)
    light('Ember bounce', (1, -3, -2), 85, 2, (1, .65, .43))


def render(path):
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def do_mark(out, fast):
    setup(1024, 1024, 32 if fast else 64)
    mark()
    studio()
    camera((.12, -7, .20), ortho=2.05)
    render(out / 'mark.png')


def embers(parent, count=9):
    """Small glowing flakes that lift off the slash and fade out: the ash in the name."""
    hot = material('Ash flake', ASH, 0., .6, 6.)
    flakes = []
    for i in range(count):
        t = i / (count - 1)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=.022 + .012 * ((i * 7) % 3),
                                              location=(-.55 + 1.2 * t, -.22, -.25 + .21 * t))
        flake = bpy.context.object
        flake.name = f'Ember flake {i + 1}'
        flake.data.materials.append(hot)
        flake.parent = parent
        flakes.append(flake)
    return flakes


def do_splash(out, fast):
    scene = setup(480, 480, 12 if fast else 24)
    root, left, right, pivot, _ = mark()
    studio()
    camera((.12, -7, .20), ortho=2.05)
    scene.frame_start, scene.frame_end = 1, FRAMES
    # Vanes glide in from the sides and lock into the peak.
    for frame, dx, spin in ((1, .55, .30), (18, 0., 0.), (FRAMES, 0., 0.)):
        left.location = (-dx, 0, -dx * .3)
        right.location = (dx, 0, -dx * .3)
        left.rotation_euler = (0, spin, 0)
        right.rotation_euler = (0, -spin, 0)
        for obj in (left, right):
            obj.keyframe_insert('location', frame=frame)
            obj.keyframe_insert('rotation_euler', frame=frame)
    # Then the slash strikes across, a touch past full length, and settles.
    for frame, length in ((1, .001), (17, .001), (25, 1.04), (30, 1.), (FRAMES, 1.)):
        pivot.scale = (length, 1, 1)
        pivot.keyframe_insert('scale', frame=frame)
    root.rotation_euler = (0, 0, 0)
    for frame, turn in ((1, -.22), (22, .02), (34, 0.), (FRAMES, 0.)):
        root.rotation_euler = (0, 0, turn)
        root.keyframe_insert('rotation_euler', frame=frame)
    # Embers lift off where the slash passed and are gone before the final (still) frame.
    for i, flake in enumerate(embers(root)):
        start = 24 + i % 4
        base = flake.location.copy()
        for frame, lift, size in ((1, 0., 0.), (start - 1, 0., 0.), (start, 0., 1.),
                                  (start + 10, .35 + .05 * (i % 3), .6), (44, .55, 0.), (FRAMES, .55, 0.)):
            flake.location = (base.x + .04 * lift * ((i % 2) * 2 - 1), base.y, base.z + lift)
            flake.scale = (size,) * 3
            flake.keyframe_insert('location', frame=frame)
            flake.keyframe_insert('scale', frame=frame)
    scene.frame_set(FRAMES)
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(__file__).resolve().parent / 'ashvane.blend'))
    frames = out / 'splash'
    frames.mkdir(parents=True, exist_ok=True)
    for old in frames.glob('f_*.png'):
        old.unlink()
    scene.render.filepath = str(frames / 'f_')
    bpy.ops.render.render(animation=True)


def do_bg(out, fast):
    setup(1920, 1080, 32 if fast else 64, False)
    graphite = material('Graphite work surface', (.065, .068, .075), .35, .42)
    rail = material('Timeline lane', (.13, .14, .15), .4, .32)
    clip = material('Trimmed clip', (.23, .235, .24), .55, .34)
    dark = material('Inactive cut', (.08, .085, .09), .2, .45)
    orange = material('Selected frame', EMBER, .3, .33, .15)
    ruler = material('Time ruler', (.44, .43, .40), .5, .4)
    block('Editing desk', (0, 0, -.3), (60, 40, .5), graphite, .15)
    for row in range(3):
        y = row * .76 - 1.45
        block('Timeline channel', (-.2, y, 0), (9, .62, .08), rail, .05)
        for start, width in ((-4.25, 1.25), (-2.82, 2.15), (-.5, 1.60), (1.28, 2.40)):
            block('Clip segment', (start + width / 2, y, .095), (width, .43, .12), clip if row != 1 else dark, .04)
    for index in range(43):
        x = -4.6 + index * .22
        block('Timecode tick', (x, 1.32, .02), (.015, .16 if index % 5 == 0 else .08, .025), ruler, .004)
    block('Playhead across lanes', (1.58, -.50, .21), (.035, 3.6, .07), orange, .009)
    root = mark()[0]
    # Stood up on the desk, turned toward the camera, so it reads as the A it is.
    root.rotation_euler = (0, 0, math.radians(16))
    root.location = (2.9, 2.6, .80 * 1.05 - .05)
    root.scale = (1.05,) * 3
    light('Overhead softbox', (-3, -1, 8), 1300, 7, target=(0, 0, 0))
    light('End light', (6, 5, 4), 950, 5, (1, .76, .56), target=(1, 1, 0))
    camera((6, -8.5, 8.5), target=(.15, .9, 0), ortho=12.1)
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(__file__).resolve().parent / 'ashvane-desk.blend'))
    render(out / 'bg.png')


if __name__ == '__main__':
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    mode, out = argv[0], Path(argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    fast = '--fast' in argv
    for name in ('mark', 'splash', 'bg') if mode == 'all' else (mode,):
        {'mark': do_mark, 'splash': do_splash, 'bg': do_bg}[name](out, fast)
