"""Fixed sync-only process shim: finite duration for looped panel composition."""
SYNC_FFMPEG_PROXY = b'''#!/usr/bin/python3
import json, math, os, re, sys
args = sys.argv[1:]
duration = float(os.environ['REELBENCH_SYNC_DURATION'])
if not math.isfinite(duration) or not 0 < duration <= 1800: raise SystemExit('invalid trusted duration')
prefix = ['-v','error','-y','-i']
if args[:4] != prefix or args[4] not in ('source/original.mp4','source/silent.mp4'): raise SystemExit('unexpected compose input')
fixed = ['-loop','1','-i','output/panels/static.png','-loop','1','-i','output/panels/list-dim.png','-loop','1','-i','output/panels/list-lit.png','-filter_complex']
if args[5:18] != fixed: raise SystemExit('unexpected panel inputs')
f = args[18]
if len(f) > 65536 or not f.startswith('[0:v]scale=') or not f.endswith(('vstack=inputs=2[out]','hstack=inputs=2[out]')): raise SystemExit('unexpected compose filter')
cmd = os.getcwd() + '/output/panels/motion.cmd'
if "sendcmd=f='" + cmd + "'" not in f: raise SystemExit('unexpected motion command path')
clean = f.replace("sendcmd=f='" + cmd + "'", 'sendcmd=f=owned')
if not re.fullmatch(r'[A-Za-z0-9_@\\[\\]:;=,./-]+', clean): raise SystemExit('unexpected filter characters')
patterns = [
 r'\\[0:v\\]scale=\\d+:\\d+:flags=lanczos,setsar=1,fps=[0-9.]+\\[v\\]',
 r'\\[1:v\\]scale=\\d+:\\d+,setsar=1,fps=[0-9.]+,sendcmd=f=owned\\[base\\]',
 r'\\[2:v\\]fps=[0-9.]+,crop@win=w=\\d+:h=\\d+:x=\\d+:y=[0-9.]+\\[win\\]',
 r'\\[3:v\\]fps=[0-9.]+(?:,split=\\d+)?(?:\\[lit\\d+\\])+',
 r'\\[lit\\d+\\]crop@band\\d+=w=\\d+:h=\\d+:x=\\d+:y=[0-9.]+\\[band\\d+\\]',
 r'\\[base\\]\\[win\\]overlay@win=x=\\d+:y=[0-9.]+\\[p0\\]',
 r'\\[p\\d+\\]\\[band\\d+\\]overlay@band\\d+=x=\\d+:y=[0-9.]+\\[p\\d+\\]',
 r'\\[v\\]\\[p\\d+\\](?:vstack|hstack)=inputs=2\\[out\\]',
]
if any(not any(re.fullmatch(p, clause) for p in patterns) for clause in clean.split(';')): raise SystemExit('unexpected filter operation')
if args[19:21] != ['-map','[out]']: raise SystemExit('unexpected output map')
tail = args[21:]
if tail[:6] == ['-map','0:a','-c:a','aac','-b:a','160k']: tail = tail[6:]
if len(tail) != 12 or tail[:5] != ['-c:v','libx264','-preset','medium','-crf'] or not re.fullmatch(r'\\d+(?:\\.\\d+)?', tail[5]) or not 0 <= float(tail[5]) <= 51: raise SystemExit('unexpected video encoding')
if tail[6:11] != ['-pix_fmt','yuv420p','-movflags','+faststart','-shortest']: raise SystemExit('unexpected encoding suffix')
out = args[-1]
if out not in ('output/review.mp4','output/upstream.mp4') or os.path.lexists(out): raise SystemExit('unsafe output')
expanded=list(args)
expanded[18]=f.replace(cmd,'output/panels/motion.cmd')
actual = ['tools/bin/ffmpeg-real', *expanded[:-1], '-t', format(duration,'.6f'), out]
fd = os.open('output/compose-argv.json', os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
try:
    payload = json.dumps({'upstream_argv':['tools/bin/ffmpeg',*args], 'actual_argv':actual}, sort_keys=True).encode()
    while payload:
        n=os.write(fd,payload)
        if n <= 0: raise SystemExit('short provenance write')
        payload=payload[n:]
    os.fsync(fd)
finally: os.close(fd)
os.execve('tools/bin/ffmpeg-real', actual, {'PATH':'tools/bin','LANG':'C','LC_ALL':'C'})
'''
