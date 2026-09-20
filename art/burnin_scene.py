"""Original BURN-IN edit-bracket identity. Render with Blender in background mode.

blender -b --factory-startup -P art/burnin_scene.py -- all art/out [--fast]
Modes: mark, splash, bg, all. The splash .blend is saved beside this script.
Geometry and lighting are deterministic; no external textures or fonts are required.
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

EMBER = (1.0, 0.537, 0.310)
INK = (0.953, 0.941, 0.914)
FPS = 24
FRAMES = 48


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
                print('[BURN-IN] Render device:', kind)
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
        node.inputs['Emission Color'].default_value = (*linear(color), 1)
        node.inputs['Emission Strength'].default_value = emission
    return mat


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


def light(name, location, energy, size, color=(1,1,1), target=(0,0,0)):
    data = bpy.data.lights.new(name, 'AREA')
    data.energy, data.size, data.color = energy, size, color
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    aim(obj, target)
    return obj


def camera(location, target=(0,0,0), ortho=2.8):
    data = bpy.data.cameras.new('Camera')
    data.type, data.ortho_scale = 'ORTHO', ortho
    obj = bpy.data.objects.new('Camera', data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    aim(obj, target)
    bpy.context.scene.camera = obj
    return obj


def mark():
    root = bpy.data.objects.new('BURN-IN | edit interval', None)
    bpy.context.collection.objects.link(root)
    silver = material('Warm satin aluminum', INK, .72, .27)
    ember = material('Ember playhead', EMBER, .2, .27, .22)
    # A pair of generous edit brackets encloses one hot playhead.
    for side in (-1, 1):
        # One extruded concave polygon per bracket avoids overlap seams at the elbows.
        outline = [(-.80,-.79),(-.18,-.79),(-.18,-.55),(-.56,-.55),
                   (-.56,.55),(-.18,.55),(-.18,.79),(-.80,.79)]
        if side > 0:
            outline = [(-x,z) for x,z in reversed(outline)]
        vertices = [(x,y,z) for y in (-.13,.13) for x,z in outline]
        count = len(outline)
        faces = [tuple(reversed(range(count))),tuple(range(count,2*count))]
        faces += [(i,(i+1)%count,(i+1)%count+count,i+count) for i in range(count)]
        mesh = bpy.data.meshes.new('Continuous bracket geometry')
        mesh.from_pydata(vertices,[],faces)
        mesh.update()
        obj = bpy.data.objects.new('In bracket' if side < 0 else 'Out bracket',mesh)
        bpy.context.collection.objects.link(obj)
        obj.data.materials.append(silver)
        obj.parent = root
        bevel = obj.modifiers.new('Continuous machined edge','BEVEL')
        bevel.width,bevel.segments = .045,5
        obj.modifiers.new('Weighted face normals','WEIGHTED_NORMAL')
    playhead = block('Hot frame', (0,-.025,0), (.19,.31,1.36), ember, .035, root)
    return root, playhead


def studio():
    light('Softbox', (-3,-4,5), 520, 5)
    light('Edge strip', (3,1,3), 740, 3)
    light('Ember bounce', (1,-3,-2), 85, 2, (1,.65,.43))


def render(path):
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def do_mark(out, fast):
    setup(1024, 1024, 32 if fast else 64)
    mark()
    studio()
    camera((.12,-7,.20), ortho=2.05)
    render(out/'mark.png')


def do_splash(out, fast):
    scene = setup(480,480,12 if fast else 24)
    root, playhead = mark()
    studio()
    camera((.12,-7,.20), ortho=2.05)
    scene.frame_start, scene.frame_end = 1, FRAMES
    for frame, rotation, scale, zscale in ((1,-.28,.88,.04),(20,.025,1.01,1.),(36,0,1.,1.),(48,0,1.,1.)):
        root.rotation_euler = (.08 if frame == 1 else 0,0,rotation)
        root.scale = (scale,)*3
        root.keyframe_insert('rotation_euler', frame=frame)
        root.keyframe_insert('scale', frame=frame)
        playhead.scale = (1,1,zscale)
        playhead.keyframe_insert('scale',frame=frame)
    scene.frame_set(FRAMES)
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(__file__).resolve().parent/'burnin.blend'))
    frames = out/'splash'
    frames.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(frames/'f_')
    bpy.ops.render.render(animation=True)


def do_bg(out, fast):
    setup(1920,1080,32 if fast else 64,False)
    graphite = material('Graphite work surface',(.065,.068,.075),.35,.42)
    rail = material('Timeline lane',(.13,.14,.15),.4,.32)
    clip = material('Trimmed clip',(.23,.235,.24),.55,.34)
    dark = material('Inactive cut',(.08,.085,.09),.2,.45)
    orange = material('Selected frame',EMBER,.3,.33,.15)
    ruler = material('Time ruler',(.44,.43,.40),.5,.4)
    block('Editing desk',(0,0,-.3),(60,40,.5),graphite,.15)
    for row in range(3):
        y = row*.76-1.45
        block('Timeline channel',(-.2,y,0),(9,.62,.08),rail,.05)
        for start,width in ((-4.25,1.25),(-2.82,2.15),(-.5,1.60),(1.28,2.40)):
            block('Clip segment',(start+width/2,y,.095),(width,.43,.12),clip if row != 1 else dark,.04)
    for index in range(43):
        x=-4.6+index*.22
        block('Timecode tick',(x,1.32,.02),(.015,.16 if index%5 == 0 else .08,.025),ruler,.004)
    block('Playhead across lanes',(1.58,-.50,.21),(.035,3.6,.07),orange,.009)
    root,_=mark()
    root.rotation_euler=(math.pi/2,0,0)
    root.location=(2.65,3.13,.43)
    root.scale=(1.05,)*3
    light('Overhead softbox',(-3,-1,8),1300,7,target=(0,0,0))
    light('End light',(6,5,4),950,5,(1,.76,.56),target=(1,1,0))
    camera((6,-8.5,8.5),target=(.15,.9,0),ortho=12.1)
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(__file__).resolve().parent/'burnin-desk.blend'))
    render(out/'bg.png')


if __name__ == '__main__':
    argv = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    mode, out = argv[0], Path(argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    fast = '--fast' in argv
    for name in ('mark','splash','bg') if mode == 'all' else (mode,):
        {'mark':do_mark,'splash':do_splash,'bg':do_bg}[name](out,fast)
