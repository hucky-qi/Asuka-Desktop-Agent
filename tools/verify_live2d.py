"""Validate the public model allowlist without model services or user data."""
from pathlib import Path
import argparse,hashlib,json,struct,zlib
P=Path(__file__).resolve().parents[1];ROOT=P/'live2d/models/Asuka'
def validate():
 manifest=json.loads((ROOT/'manifest.json').read_text());m=ROOT/manifest['model'];refs=json.loads(m.read_text())['FileReferences']
 expected={m.name,refs['Moc'],*refs['Textures']}
 for k in ('DisplayInfo','Physics','Pose','UserData'):
  if refs.get(k):expected.add(refs[k])
 for group in refs.get('Motions',{}).values():
  for entry in group:
   expected.add(entry['File'])
   if entry.get('Sound'):expected.add(entry['Sound'])
 for entry in refs.get('Expressions',[]):expected.add(entry['File'])
 assert expected=={f['path'] for f in manifest['files']},'Manifest/reference mismatch'
 for row in manifest['files']:
  f=(ROOT/row['path']).resolve()
  assert f.is_relative_to(ROOT) and f.is_file() and not (ROOT/row['path']).is_symlink(),'Unsafe/missing reference'
  data=f.read_bytes();assert len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256'],'Changed model bytes'
  if f.suffix=='.json':
   obj=json.loads(data)
   assert '/Users/' not in json.dumps(obj) and ':\\Users\\' not in json.dumps(obj),'Private path'
  elif f.suffix=='.png':
   assert data.startswith(b'\x89PNG\r\n\x1a\n')
   pos=8;chunks=[]
   while pos<len(data):
    n=struct.unpack('>I',data[pos:pos+4])[0];kind=data[pos+4:pos+8];payload=data[pos+8:pos+8+n]
    assert zlib.crc32(kind+payload)&0xffffffff==struct.unpack('>I',data[pos+8+n:pos+12+n])[0]
    if kind==b'IHDR':assert list(struct.unpack('>II',payload[:8]))==manifest['texture_size']
    chunks.append(kind.decode());pos+=12+n
   assert not set(chunks)&{'tEXt','zTXt','iTXt','eXIf'},'Unreviewed image metadata'
  elif f.suffix=='.moc3':assert data[:4]==b'MOC3','Invalid moc3 header'
 cfg=json.loads((P/'live2d/asuka-model-config.json').read_text());assert cfg['fallbackModel'] is None
 assert cfg['scale']==.88 and cfg['tailOutputGain']==1.6
 return {'files':len(expected),'references_and_hashes':'PASS','texture_size':manifest['texture_size'],'texture_text_metadata':'none','runtime_config':'PASS','sdk_bundled':False}
if __name__=='__main__':print(json.dumps(validate()))
