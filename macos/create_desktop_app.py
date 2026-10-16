"""Reproducibly create this project's quiet macOS desktop entry (no data copied)."""
from pathlib import Path
import argparse,plistlib,subprocess,sys,os,shutil,time
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from swift_build import compile_helper
BUILD=PROJECT/'.runtime/desktop-app-build'

def create(icon_source,destination):
    icon_source=Path(icon_source).expanduser().resolve() if icon_source else None;destination=Path(destination).expanduser()
    if destination.exists():
        try:owned=plistlib.loads((destination/'Contents/Info.plist').read_bytes())
        except (OSError,ValueError):raise ValueError('现有应用无法识别，未覆盖')
        if owned.get('CFBundleIdentifier')!='local.filechat.asuka.launcher' or owned.get('AsukaProjectPath')!=str(PROJECT):raise ValueError('现有应用不属于本项目，未覆盖')
    BUILD.mkdir(parents=True,exist_ok=True)
    launcher=BUILD/'AsukaLauncher';converter=BUILD/'IconConverter'
    compile_helper(PROJECT/'macos/AsukaLauncher.swift',launcher);compile_helper(PROJECT/'macos/Icon.swift',converter)
    if icon_source:
        png=BUILD/'AsukaIcon.png';subprocess.run([str(converter),str(icon_source),str(png)],check=True)
        iconset=BUILD/'Asuka.iconset';iconset.mkdir(exist_ok=True)
        for points in (16,32,128,256,512):
            for scale in (1,2):
                pixels=points*scale;name=f'icon_{points}x{points}'+('@2x' if scale==2 else '')+'.png'
                subprocess.run(['sips','-z',str(pixels),str(pixels),str(png),'--out',str(iconset/name)],check=True,capture_output=True)
        icns=BUILD/'Asuka.icns';subprocess.run(['iconutil','-c','icns',str(iconset),'-o',str(icns)],check=True)
    # Stage wholly in-project; the only external write is the authorized app.
    stage=BUILD/'明日香助手.app'
    if stage.exists():shutil.rmtree(stage)
    contents=stage/'Contents';(contents/'MacOS').mkdir(parents=True);(contents/'Resources').mkdir()
    shutil.copy2(launcher,contents/'MacOS/AsukaLauncher')
    if icon_source:shutil.copy2(icns,contents/'Resources/Asuka.icns')
    metadata={'CFBundleName':'明日香助手','CFBundleDisplayName':'明日香助手','CFBundleIdentifier':'local.filechat.asuka.launcher','CFBundleExecutable':'AsukaLauncher','CFBundlePackageType':'APPL','CFBundleVersion':'1','CFBundleShortVersionString':'1.0','LSUIElement':True,'NSHighResolutionCapable':True,'LSMinimumSystemVersion':'13.0','AsukaProjectPath':str(PROJECT),'NSCalendarsFullAccessUsageDescription':'仅在你询问时读取本机日历；只有你核对预览并明确确认后才创建或修改单次事件并读回核对。不删除或上传日历。','NSCalendarsUsageDescription':'读取本机日历并在明确确认后创建或修改单次事件；不删除或上传。'}
    if icon_source:metadata['CFBundleIconFile']='Asuka.icns'
    (contents/'Info.plist').write_bytes(plistlib.dumps(metadata))
    # Ad-hoc local signing; no certificate purchase, distribution or notarization.
    subprocess.run(['codesign','--force','--sign','-',str(stage)],check=True,capture_output=True)
    if destination.exists():
        backups=BUILD/'previous';backups.mkdir(exist_ok=True)
        shutil.copytree(destination,backups/(str(time.time_ns())+'.app'))
    shutil.copytree(stage,destination,dirs_exist_ok=True)
    return destination

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--icon',default=None);parser.add_argument('--output',default=str(Path.home()/'Desktop/明日香助手.app'));args=parser.parse_args()
    try:print(create(args.icon,args.output))
    except (OSError,ValueError,subprocess.CalledProcessError):parser.exit(1,'生成入口失败；未更改已有桌面应用。请检查素材、swiftc及项目构建目录。\n')
