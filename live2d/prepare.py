"""Copy only licensed build inputs/runtime assets from the user's Web R5 SDK.

Generated SDK, Core and model assets stay ignored and are never a release package.
"""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def asset_paths(setting):
    refs = setting.get('FileReferences')
    if not isinstance(refs, dict) or not isinstance(refs.get('Moc'), str) or not refs['Moc']:
        raise ValueError('模型缺少有效的 FileReferences.Moc')
    if not isinstance(refs.get('Textures'), list) or not refs['Textures']:
        raise ValueError('模型至少需要一张纹理')
    paths = [refs['Moc'], *refs['Textures']]
    paths += [refs[k] for k in ('Physics', 'Pose', 'DisplayInfo', 'UserData') if refs.get(k)]
    motions = refs.get('Motions', {})
    expressions = refs.get('Expressions', [])
    if not isinstance(motions, dict) or any(not isinstance(items, list) for items in motions.values()) or not isinstance(expressions, list):
        raise ValueError('Motions 必须为动作分组列表，Expressions 必须为列表')
    entries = [m for items in motions.values() for m in items] + expressions
    if any(not isinstance(m, dict) or not isinstance(m.get('File'), str) for m in entries):
        raise ValueError('动作或表情条目缺少有效 File 路径')
    paths += [m['File'] for m in entries]
    paths += [m['Sound'] for items in motions.values() for m in items if m.get('Sound')]
    if any(not isinstance(p, str) or not p or '\\' in p or '://' in p or '?' in p or '#' in p or '%' in p for p in paths):
        raise ValueError('模型资源必须是非空本地相对路径，不能包含 URL 或转义字符')
    return sorted(set(paths))


def copy_model(source, destination):
    source = Path(source).resolve()
    try:
        setting = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'无法读取模型清单：{source.name}：{exc}') from exc
    if not isinstance(setting, dict) or setting.get('Version') != 3:
        raise ValueError('需要 model3.json Version 3')
    destination.mkdir(parents=True, exist_ok=True)
    for relative in asset_paths(setting):
        candidate = (source.parent / relative).resolve()
        target = (destination / relative).resolve()
        if not candidate.is_relative_to(source.parent) or not target.is_relative_to(destination.resolve()):
            raise ValueError('模型资源引用越界')
        if not candidate.is_file():
            raise ValueError(f'模型资源缺失：{relative}')
        if not candidate.stat().st_size:
            raise ValueError(f'模型资源为空：{relative}')
    # Validation completes before copying; no fake/placeholder model is created.
    for relative in asset_paths(setting):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source.parent / relative, target)
    shutil.copy2(source, destination / source.name)
    return source.name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sdk', type=Path, required=True)
    choice=parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--model', type=Path, help='用户自行确认权利的 model3.json')
    choice.add_argument('--asuka', action='store_true', help='使用随项目附带的非官方同人v7；先阅读models/Asuka/NOTICE.md')
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    private = ROOT/'.sdk'
    public = ROOT/'.assets'
    private.mkdir(exist_ok=True)
    public.mkdir(exist_ok=True)
    shutil.copytree(sdk/'Framework/src', private/'Framework', dirs_exist_ok=True)
    shutil.copytree(sdk/'Framework/Shaders/WebGL', public/'shaders', dirs_exist_ok=True)
    shutil.copy2(sdk/'Core/live2dcubismcore.min.js', public/'live2dcubismcore.min.js')
    for name in ('LICENSE.md', 'NOTICE.md', 'CHANGELOG.md'):
        shutil.copy2(sdk/name, private/name)
    for component in ('Core', 'Framework'):
        for path in (sdk/component).glob('*LICENSE*'):
            shutil.copy2(path, private/f'{component}-{path.name}')
    source = ROOT/'models/Asuka/Asuka_Tilt_Hair_Work.model3.json' if args.asuka else args.model
    folder = 'Asuka' if args.asuka else 'Custom'
    name = copy_model(source, public/'models'/folder)
    config = {'model': f'./models/{folder}/{name}', 'sample': False,
              'label': 'User supplied model', 'sdk': 'Cubism SDK for Web', 'fallbackModel': None}
    if args.asuka:
        config=json.loads((ROOT/'asuka-model-config.json').read_text())
        config['model']=f'./models/Asuka/{name}'
        config['fallbackModel']=None
    (public/'model-config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2))
    print('已准备本机资源；SDK/Core/构建产物保持Git忽略，未下载第三方资源。', config['label'])


if __name__ == '__main__':
    main()
