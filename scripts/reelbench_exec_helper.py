"""Fixed descriptor-bound launcher for staged ReelBench Node execution."""
from __future__ import annotations

# The launcher is passed to fixed system Python with -I -c. No mutable helper
# pathname or caller-selected interpreter participates in production execution.
HELPER_CODE = r'''
import hashlib, json, os, resource, stat, sys
fd = int(sys.argv[1])
os.fchdir(fd)
os.close(fd)
os.umask(0o077)
resource.setrlimit(resource.RLIMIT_FSIZE, (8388608, 8388608))
manifest = json.loads(sys.argv[2])
for path, expected in manifest.items():
    parts = path.split('/')
    if path.startswith('/') or any(p in ('', '.', '..') for p in parts):
        raise SystemExit('unsafe workspace input')
    parent = os.open('.', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(source)
            if not stat.S_ISREG(info.st_mode) or info.st_size != expected['size_bytes']:
                raise SystemExit('workspace input identity changed')
            observed = {'inode': info.st_ino, 'device': info.st_dev, 'mode': stat.S_IMODE(info.st_mode)}
            if any(key in expected and expected[key] != observed[key] for key in observed):
                raise SystemExit('workspace executable identity changed')
            digest = hashlib.sha256()
            while True:
                chunk = os.read(source, 1048576)
                if not chunk:
                    break
                digest.update(chunk)
            if digest.hexdigest() != expected['sha256']:
                raise SystemExit('workspace input digest changed')
        finally:
            os.close(source)
    finally:
        os.close(parent)
argv = sys.argv[3:]
if argv[:2] != ['tools/node', 'script/video-shots.mjs']:
    raise SystemExit('unsafe executable argv')
os.execve('tools/node', argv, {'PATH': 'tools/bin', 'LANG': 'C', 'LC_ALL': 'C'})
'''
