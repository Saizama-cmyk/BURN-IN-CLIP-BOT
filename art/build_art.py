"""Render and package the original BURN-IN Blender identity.

python art/build_art.py --render [--fast]
Without --render, package the last frames in art/out_burnin/.
Blender is required only to rebuild the art; ffmpeg encodes the 2-second splash.
"""
from __future__ import annotations
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT/'art'/'out_burnin'
STATIC = ROOT/'clipbot'/'dashboard'/'static'/'art'
ASSETS = ROOT/'assets'
BRAND = ROOT/'brandkit'
FPS = 24
GROUND = '#101113'
INK = '#f3f0e9'
MUTED = '#a8a6a1'
EMBER = '#ff894f'
logger = logging.getLogger('clipbot.art')


def blender_exe():
    return os.environ.get('BLENDER') or shutil.which('blender') or r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'


def font(size, mono=False):
    filename = 'consola.ttf' if mono else 'bahnschrift.ttf'
    path = Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts'/filename
    return ImageFont.truetype(str(path),size)


def brandkit(mark, backdrop):
    BRAND.mkdir(exist_ok=True)
    # The actual Blender mark, with clear margins for circular platform crops.
    logo = mark.resize((512,512),Image.Resampling.LANCZOS)
    logo.save(BRAND/'logo-transparent-512.png',optimize=True)
    logo.resize((150,150),Image.Resampling.LANCZOS).save(BRAND/'youtube-watermark-150.png',optimize=True)
    for name, background in [('avatar-solid-1080.png',Image.new('RGB',(1080,1080),GROUND)),
                             ('avatar-1080.png',ImageOps.fit(backdrop,(1080,1080)))]:
        canvas=background.convert('RGBA')
        if name == 'avatar-1080.png':
            canvas=Image.blend(canvas,Image.new('RGBA',canvas.size,GROUND),.64)
        icon=mark.resize((780,780),Image.Resampling.LANCZOS)
        canvas.alpha_composite(icon,(150,150))
        canvas.convert('RGB').save(BRAND/name,optimize=True)
    sizes={'youtube-banner-2560x1440.jpg':(2560,1440), 'x-header-1500x500.jpg':(1500,500),
           'facebook-cover-1640x624.jpg':(1640,624), 'linkedin-banner-1584x396.jpg':(1584,396),
           'github-social-preview-1280x640.jpg':(1280,640)}
    for name,(width,height) in sizes.items():
        canvas=ImageOps.fit(backdrop,(width,height)).convert('RGBA')
        canvas=Image.blend(canvas,Image.new('RGBA',canvas.size,GROUND),.78)
        # YouTube content stays inside a 1546x423 centered safe area.
        safe_width = 1420 if name.startswith('youtube-') else int(width*.82)
        safe_height = 340 if name.startswith('youtube-') else int(height*.68)
        start=(width-safe_width)//2
        icon_size=min(safe_height,290 if name.startswith('youtube-') else int(height*.62))
        x=start
        y=(height-icon_size)//2
        canvas.alpha_composite(mark.resize((icon_size,icon_size),Image.Resampling.LANCZOS),(x,y))
        draw=ImageDraw.Draw(canvas)
        text_x=x+icon_size+int(width*.032)
        title_size=min(110,int(safe_height*.32))
        title_y=height//2-int(title_size*.82)
        draw.text((text_x,title_y),'BURN-IN',font=font(title_size),fill=INK)
        copy_y=title_y+int(title_size*1.34)
        draw.text((text_x,copy_y),'YOUR STREAM. THE MOMENT. THE CUT.',font=font(max(18,int(title_size*.23)),True),fill=EMBER)
        draw.text((text_x,copy_y+int(title_size*.42)),'Local AI clipping for Windows',font=font(max(19,int(title_size*.25))),fill=MUTED)
        canvas.convert('RGB').save(BRAND/name,quality=91,optimize=True)
    canvas=Image.blend(ImageOps.fit(backdrop,(1280,720)),Image.new('RGB',(1280,720),GROUND),.36).convert('RGBA')
    canvas.alpha_composite(mark.resize((165,165),Image.Resampling.LANCZOS),(1055,44))
    draw=ImageDraw.Draw(canvas)
    draw.rectangle((0,500,1280,720),fill=GROUND)
    draw.rectangle((58,542,65,658),fill=EMBER)
    draw.text((89,535),'YOUR NEXT MOMENT',font=font(62),fill=INK)
    draw.text((91,619),'Replace this title with the clip headline.',font=font(24),fill=MUTED)
    canvas.convert('RGB').save(BRAND/'thumbnail-template-1280x720.jpg',quality=91,optimize=True)


def main():
    logging.basicConfig(level=logging.INFO,format='%(message)s')
    if '--render' in sys.argv:
        subprocess.run([str(blender_exe()),'-b','--factory-startup','-P',str(ROOT/'art'/'burnin_scene.py'),
                        '--','all',str(OUT)]+(['--fast'] if '--fast' in sys.argv else []),check=True)
    STATIC.mkdir(parents=True,exist_ok=True)
    ASSETS.mkdir(exist_ok=True)
    mark=Image.open(OUT/'mark.png').convert('RGBA')
    mark.save(ASSETS/'clipbot-3d.png',optimize=True)
    for name,size in [('mark.png',512),('mark-96.png',96)]:
        mark.resize((size,size),Image.Resampling.LANCZOS).save(STATIC/name,optimize=True)
    backdrop=Image.open(OUT/'bg.png').convert('RGB')
    Image.blend(backdrop,Image.new('RGB',backdrop.size,GROUND),.45).save(STATIC/'bg.jpg',quality=86,optimize=True)
    frames=sorted((OUT/'splash').glob('f_*.png'))
    if len(frames)!=48:
        raise RuntimeError(f'Expected 48 splash frames; found {len(frames)}')
    Image.open(frames[-1]).save(STATIC/'splash-end.png',optimize=True)
    subprocess.run([shutil.which('ffmpeg') or 'ffmpeg','-y','-hide_banner','-loglevel','error',
                    '-framerate',str(FPS),'-start_number','1','-i',str(OUT/'splash'/'f_%04d.png'),
                    '-c:v','libvpx-vp9','-pix_fmt','yuva420p','-b:v','0','-crf','30',
                    '-auto-alt-ref','0','-row-mt','1',str(STATIC/'splash.webm')],check=True)
    brandkit(mark,backdrop)
    sys.path.insert(0,str(ROOT))
    from clipbot.icon import write_assets
    write_assets(ASSETS)
    for path in sorted(STATIC.iterdir()):
        if path.is_file(): logger.info('%s: %.1f KB',path.name,path.stat().st_size/1024)

if __name__ == '__main__':
    main()
