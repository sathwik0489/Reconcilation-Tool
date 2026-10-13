import {cpSync,existsSync} from 'node:fs';
import {spawn} from 'node:child_process';
if(!existsSync('.next/standalone/server.js'))throw new Error('Run npm run build before npm start.');
cpSync('public','.next/standalone/public',{recursive:true});
cpSync('.next/static','.next/standalone/.next/static',{recursive:true});
const server=spawn(process.execPath,['.next/standalone/server.js'],{stdio:'inherit',env:{...process.env,HOSTNAME:process.env.HOSTNAME||'127.0.0.1',PORT:process.env.PORT||'3000'}});
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>server.kill(signal));
server.on('exit',code=>process.exit(code||0));
