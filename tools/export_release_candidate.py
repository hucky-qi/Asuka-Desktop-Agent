"""Explicit source allowlist export on existing public history; never push or copy data.
Public docs/notices/model assets come from --base. Runtime source comes from this
project (or --source). Maintain the public docs in the reviewed base before export.
"""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess
P=Path(__file__).resolve().parents[1]

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--base',type=Path,default=P/'.release/current-preview',help='Reviewed local public Git checkout; never private project history')
 parser.add_argument('--source',type=Path,default=P)
 parser.add_argument('--output',type=Path,required=True)
 args=parser.parse_args();base=args.base.resolve();source=args.source.resolve();dest=args.output.resolve()
 if dest.exists():parser.error('Output exists; preserve it and select another path.')
 if not (base/'.git').exists():parser.error('Supply an existing reviewed public Git checkout with --base.')
 manifest=json.loads((source/'tools/release_manifest.json').read_text());paths={r['path'] for r in manifest['files']}
 if len(paths)!=len(manifest['files']):parser.error('Duplicate manifest paths.')
 for row in manifest['files']:
  path=Path(row['path'])
  if path.is_absolute() or '..' in path.parts or row['origin'] not in ('source','base'):parser.error('Unsafe manifest entry.')
  if path.name=='.env' or any(x in path.parts for x in ('.runtime','.venv','node_modules','__pycache__')):parser.error('Runtime files are prohibited.')
  if any((source/path).parents[i].is_symlink() for i in range(len(path.parts)-1)):parser.error('Symlink parent refused.')
 subprocess.run(['git','clone','--local','--no-hardlinks',str(base),str(dest)],check=True,capture_output=True)
 # Remove stale tracked source paths ONLY inside the newly created export.
 tracked=subprocess.check_output(['git','ls-files','-z'],cwd=dest).split(b'\0')
 for name in tracked:
  if name and name.decode() not in paths:(dest/name.decode()).unlink()
 for row in manifest['files']:
  target=dest/row['path'];src=(source if row['origin']=='source' else base)/row['path']
  if not src.is_file() or src.is_symlink():raise ValueError('Missing or symlink source: '+row['path'])
  target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target)
 replacements={
  'backend/providers/baidu_config.py':[("ENV_PATH=PROJECT.parent.parent/'.env'","ENV_PATH=PROJECT/'.env'")],
  'backend/settings.py':[('WORKSPACE = PROJECT.parent / "workspace" if (PROJECT.parent / "workspace").is_dir() else PROJECT / "workspace"','WORKSPACE = PROJECT / "workspace"')],
  'macos/AsukaLauncher.swift':[('root.deletingLastPathComponent().appendingPathComponent(".venv/bin/python")','root.appendingPathComponent(".venv/bin/python")')],
 }
 for name,pairs in replacements.items():
  path=dest/name;text=path.read_text()
  for private,public in pairs:
   if private in text:text=text.replace(private,public)
   elif public not in text:raise ValueError('Portability contract changed: '+name)
  path.write_text(text)
 subprocess.run(['git','remote','set-url','origin','https://github.com/hucky-qi/Asuka-Desktop-Agent.git'],cwd=dest,check=True)
 print(json.dumps({'files':len(paths),'output_created':True,'history_base':subprocess.check_output(['git','rev-parse','HEAD'],cwd=dest,text=True).strip(),'private_runtime_copied':False,'pushed':False}))
if __name__=='__main__':main()
