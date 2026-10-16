import {build} from 'esbuild';
import {cp, mkdir, copyFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root = path.dirname(fileURLToPath(import.meta.url));
const output = process.argv[2] ? path.resolve(process.argv[2]) : path.join(root,'.runtime');
if(output!==path.join(root,'.runtime') && !output.startsWith(path.join(root,'.previews')+path.sep))
  throw new Error('预览构建仅允许 live2d/.previews 内');
await mkdir(output,{recursive:true});
await cp(path.join(root,'.assets'),output,{recursive:true});
await copyFile(path.join(root,'index.html'),path.join(output,'index.html'));
await build({entryPoints:[path.join(root,'main.ts')],bundle:true,
  outfile:path.join(output,'prototype.js'),platform:'browser',target:'safari16',
  format:'iife',sourcemap:false});
console.log('本机显示原型已构建；不作为 SDK 发布包。');
await build({entryPoints:[path.join(root,'chat-bridge.mjs')],bundle:true,
  outfile:path.join(output,'chat-bridge.js'),platform:'browser',target:'safari16',format:'iife'});
