"""Guarded, argv-only bridge to the pinned ReelBench video-shots script."""

from __future__ import annotations

import math
import os
import re
import json
import hashlib
import stat
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from scripts.bounded_process import BoundedProcessError, BoundedProcessResult, run_bounded
from scripts.reelbench_contracts import REELBENCH_VALIDATE_GATES, _PINNED_SHOTS_SCRIPTS
from scripts.trusted_media_tools import TrustedExecutable, TrustedMediaToolStore
from scripts import reelbench_workspace as workspace
from scripts.reelbench_exec_helper import HELPER_CODE
from scripts.reelbench_sync_ffmpeg import SYNC_FFMPEG_PROXY

# The upstream entrypoint is fixed by this module, not caller input.  Its
# descriptor launcher differs only in the one allowed pinned program name.
SYNC_HELPER_CODE = HELPER_CODE.replace("script/video-shots.mjs", "script/video-sync.mjs").replace(
    "os.execve('tools/node', argv, {'PATH': 'tools/bin', 'LANG': 'C', 'LC_ALL': 'C'})",
    "os.execve('tools/node', argv, {**{key: os.environ[key] for key in os.environ if key.startswith('REELBENCH_BROWSER_')}, **({'NODE_OPTIONS':'--import=./script/browser-lease.mjs'} if 'REELBENCH_BROWSER_BUNDLE_FD' in os.environ else {}), 'PATH': 'tools/bin', 'LANG': 'C', 'LC_ALL': 'C'})",
).replace('(8388608, 8388608)', '(2147483648, 2147483648)').replace("key.startswith('REELBENCH_BROWSER_')", "key.startswith(('REELBENCH_BROWSER_', 'REELBENCH_SYNC_'))")
# Node closes non-stdio descriptors in execFileSync. Bridge only the fixed
# browser proxy and preserve its two capability descriptors in their slots.
BROWSER_PRELOAD = b'''import cp from 'node:child_process';
import {syncBuiltinESMExports} from 'node:module';
const original = cp.execFileSync;
cp.execFileSync = function(file, args, options) {
  if(file !== 'tools/browser-proxy') return original.apply(this, arguments);
  const descriptors = ['REELBENCH_BROWSER_BUNDLE_FD','REELBENCH_BROWSER_EXECUTABLE_FD'].map(k => Number(process.env[k]));
  if(descriptors.some(n => !Number.isInteger(n) || n < 3 || n > 4096)) throw new Error('invalid browser lease');
  const configured = options?.stdio ?? 'pipe';
  const stdio = Array.isArray(configured) ? [...configured] : [configured,configured,configured];
  for(const fd of descriptors) { while(stdio.length <= fd) stdio.push('ignore'); stdio[fd] = fd; }
  return original.call(this, file, args, {...options, stdio});
};
syncBuiltinESMExports();
'''
PINNED_SYNC_SCRIPTS = {
    'video-sync.mjs': ('ad0386fbe90bdfbd8ede5d870dd1ffd477f386b5cdd9ceb7fec085c4edcd3b4d', 30541),
    'panel.css': ('e27dd1ad24d161c501cce6cf2fea3e0bda2b5a1a17285b38f688f33473f4ce9b', 6971),
    'panel.html': ('9729bf9572e3650699bdeec0a97f81992bfd5a4fe65c27ee771a7ff72b6977b6', 5537),
}
BROWSER_PROXY_CODE = b'''#!/usr/bin/python3
import hashlib, os, secrets, stat, subprocess, sys
from urllib.parse import unquote, urlsplit
bundle_fd = int(os.environ["REELBENCH_BROWSER_BUNDLE_FD"])
executable_fd = int(os.environ["REELBENCH_BROWSER_EXECUTABLE_FD"])
relative = os.environ["REELBENCH_BROWSER_RELATIVE"]
expected = os.environ["REELBENCH_BROWSER_SHA256"]
arguments = list(sys.argv[1:])
cwd = os.getcwd()
pages = [a for a in arguments if a.startswith('file://')]
if len(pages) != 1: raise SystemExit('expected one owned panel page')
url = urlsplit(pages[0])
if url.netloc or unquote(url.path) != cwd + '/output/panels/panel.html' or url.fragment not in ('measure','static','tall','tall-lit'): raise SystemExit('unexpected panel page')
owned = [os.open('.', os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)]
screen_parent = screen_temp = None
screen_name = target_name = None
try:
    for part in ('output','panels'):
        owned.append(os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=owned[-1]))
    page = os.open('panel.html',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=owned[-1])
    try:
        if os.fstat(page).st_size > 8388608: raise SystemExit('panel page exceeds byte bound')
        os.dup2(page,0)
    finally: os.close(page)
    arguments[arguments.index(pages[0])]='file:///dev/fd/0#'+url.fragment
    screenshots=[a for a in arguments if a.startswith('--screenshot=')]
    if url.fragment == 'measure':
        if screenshots or '--dump-dom' not in arguments: raise SystemExit('unexpected measure arguments')
    else:
        if len(screenshots)!=1: raise SystemExit('expected one screenshot')
        name={'static':'static.png','tall':'list-dim.png','tall-lit':'list-lit.png'}[url.fragment]
        if screenshots[0]!='--screenshot='+cwd+'/output/panels/'+name: raise SystemExit('unexpected screenshot path')
        screen_parent=os.dup(owned[-1])
        target_name=name
        try:
            os.stat(target_name,dir_fd=screen_parent,follow_symlinks=False)
            raise SystemExit('screenshot destination already exists')
        except FileNotFoundError: pass
        screen_name='.browser-'+secrets.token_hex(12)
        os.mkdir(screen_name,0o700,dir_fd=screen_parent)
        screen_temp=os.open(screen_name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=screen_parent)
        os.fsync(screen_parent)
        arguments[arguments.index(screenshots[0])]='--screenshot='+cwd+'/output/panels/'+screen_name+'/capture.png'
finally:
    for fd in reversed(owned): os.close(fd)
os.fchdir(bundle_fd)
parent = os.open("Contents", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    child = os.open("MacOS", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
finally: os.close(parent)
parent = child
try:
    name = relative.rsplit("/", 1)[-1]
    current = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        a, b = os.fstat(current), os.fstat(executable_fd)
        if (a.st_dev, a.st_ino, a.st_size, a.st_mode, a.st_uid) != (b.st_dev, b.st_ino, b.st_size, b.st_mode, b.st_uid): raise SystemExit("browser identity changed")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(current, 65536)
            if not chunk: break
            digest.update(chunk)
        if digest.hexdigest() != expected: raise SystemExit("browser digest changed")
    finally: os.close(current)
    os.fchdir(parent)
    completed=subprocess.call([relative,*arguments],executable='./'+name,env={"PATH":"/usr/bin:/bin", "LANG":"C", "LC_ALL":"C"},close_fds=True)
    if completed: raise SystemExit(completed)
    if screen_temp is not None:
        def identity(info): return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        directory=os.fstat(screen_temp)
        entry=os.stat(screen_name,dir_fd=screen_parent,follow_symlinks=False)
        if (directory.st_dev,directory.st_ino)!=(entry.st_dev,entry.st_ino): raise SystemExit('screenshot temp directory changed')
        source=os.open('capture.png',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=screen_temp)
        try:
            before=os.fstat(source)
            if not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid() or stat.S_IMODE(before.st_mode)!=0o600 or not 8<=before.st_size<=67108864 or os.pread(source,8,0)!=b'\\x89PNG\\r\\n\\x1a\\n': raise SystemExit('invalid bounded screenshot')
            out=os.open(target_name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=screen_parent)
            digest=hashlib.sha256()
            try:
                total=0
                while True:
                    chunk=os.read(source,1048576)
                    if not chunk: break
                    total+=len(chunk)
                    if total>67108864: raise SystemExit('screenshot exceeds byte bound')
                    digest.update(chunk)
                    while chunk:
                        n=os.write(out,chunk)
                        if n<=0: raise SystemExit('short screenshot write')
                        chunk=chunk[n:]
                if identity(before)!=identity(os.fstat(source)) or identity(before)!=identity(os.stat('capture.png',dir_fd=screen_temp,follow_symlinks=False)): raise SystemExit('screenshot identity changed')
                os.fsync(out)
            finally: os.close(out)
            os.fsync(screen_parent)
        finally: os.close(source)
finally:
    os.close(parent)
    if screen_temp is not None:
        directory=os.fstat(screen_temp)
        entry=os.stat(screen_name,dir_fd=screen_parent,follow_symlinks=False)
        if (directory.st_dev,directory.st_ino)!=(entry.st_dev,entry.st_ino): raise SystemExit('screenshot cleanup identity changed')
        try: os.unlink('capture.png',dir_fd=screen_temp)
        except FileNotFoundError: pass
        os.fsync(screen_temp)
        os.close(screen_temp)
        os.rmdir(screen_name,dir_fd=screen_parent)
        os.fsync(screen_parent)
    if screen_parent is not None: os.close(screen_parent)
'''


MAX_STDOUT_BYTES = 8 * 1024 * 1024
MAX_STDERR_BYTES = 1024 * 1024
MAX_ACTION_SECONDS = 120.0
MAX_TITLE_CHARS = 512
_GATE_MARK = {"✅": "PASS", "❌": "FAIL", "⊘": "SKIPPED"}
_GATE_LINE = re.compile(r"^(?P<mark>[✅❌⊘])\s+(?P<evidence>.+)$")
_ENGLISH_GATE_LABELS = {
    "timeline": "Timeline is continuous", "duration": "Durations add up", "numbering": "Shot numbering",
    "size": "Shot size vocabulary", "category": "Category vocabulary", "camera": "Camera vocabulary",
    "transition": "Transition vocabulary", "frame-text": "Frame description is checkable",
    "dedup": "No duplicate descriptions", "subjects": "Subjects reconcile with cast",
    "category-evidence": "Categories carry evidence", "motion": "Camera vs. measured motion",
    "boundary": "Boundaries come from detection", "frames": "Keyframes present",
    "rhythm": "Rhythm annotation is checkable",
}


class ReelBenchAdapterError(RuntimeError):
    """The pinned ReelBench script could not be run or returned unsafe output."""


@dataclass(frozen=True)
class ReelBenchAdapterResult:
    """Bounded output and immutable argv evidence from one upstream invocation."""

    action: str
    argv: list[str]
    shell: bool
    stdout: str
    stderr: str
    returncode: int
    gates: tuple[dict[str, str], ...] = ()
    tool_identities: tuple[dict[str, str | int], ...] = ()
    script_manifest: tuple[dict[str, str | int], ...] = ()


class ReelBenchAdapter:
    """Run unchanged ``video-shots.mjs`` only against one private project root."""

    def __init__(
        self,
        *,
        project_root: Path,
        shots_script: Path,
        sync_script: Path | None = None,
        tools: Mapping[str, TrustedExecutable],
        tool_store: TrustedMediaToolStore | None = None,
        runner: Callable[..., BoundedProcessResult] = run_bounded,
    ) -> None:
        self._project_root = Path(project_root).resolve(strict=True)
        if not self._project_root.is_dir() or self._project_root.is_symlink():
            raise ValueError("project root must be a private non-symlink directory")
        self._shots_script = Path(shots_script).resolve(strict=True)
        if not self._shots_script.is_file() or self._shots_script.is_symlink() or self._shots_script.name != "video-shots.mjs":
            raise ValueError("shots script must be the packaged pinned entrypoint")
        self._sync_script = Path(sync_script).resolve(strict=True) if sync_script is not None else (
            self._shots_script.parents[2] / "dreamina-video-sync" / "scripts" / "video-sync.mjs"
        ).resolve(strict=True)
        if not self._sync_script.is_file() or self._sync_script.is_symlink() or self._sync_script.name != "video-sync.mjs":
            raise ValueError("sync script must be the packaged pinned entrypoint")
        if set(tools) != {"node", "ffmpeg", "ffprobe"}:
            raise ValueError("ReelBench requires exactly trusted node, ffmpeg, and ffprobe tools")
        if any(tool.kind != kind or not Path(tool.source_path).is_absolute() or tool.size_bytes < 1 for kind, tool in tools.items()):
            raise ValueError("trusted ReelBench tool identities are invalid")
        if tool_store is None:
            raise ValueError("real ReelBench execution requires descriptor-verified trusted media tools")
        self._tools = dict(tools)
        self._tool_store = tool_store
        self._runner = runner
        self._ENGLISH_GATE_LABELS = _ENGLISH_GATE_LABELS
        self._workspace = ContextVar("reelbench_workspace", default=None)
        self._sync_duration = ContextVar('reelbench_sync_duration', default=None)

    @contextmanager
    def execution_workspace(self, descriptor: int, consumed=(), *, workflow: str = "shots", browser_lease=None):
        """Stage tools once for the complete action; release every acquired handle."""
        if workflow not in {"shots", "sync"}:
            raise ValueError("unknown pinned ReelBench workflow")
        if browser_lease is not None and workflow != "sync":
            raise ValueError("browser lease is only valid for the pinned sync workflow")
        staged = {}
        token = None
        manifest = {item["workspace_path"]: {key: item[key] for key in ("size_bytes", "sha256")} for item in consumed}
        try:
            identities = self.tool_identities
            staged = self._tool_store.load_required(("node", "ffmpeg", "ffprobe"))
            identities = []
            for kind in ("node", "ffmpeg", "ffprobe"):
                target = "tools/node" if kind == "node" else f"tools/bin/{kind}"
                if workflow == 'sync' and kind == 'ffmpeg': target = 'tools/bin/ffmpeg-real'
                tool = staged[kind]
                path = Path(tool.staged_path)
                parent = workspace.open_absolute(path.parent)
                try:
                    record, info = workspace.copy(parent, path.name, descriptor, target,
                        maximum=256 * 1024 * 1024, expected={"sha256": tool.sha256, "size_bytes": path.stat().st_size}, mode=0o500, quota=True)
                finally:
                    workspace._close_owned([parent])
                identities.append({"kind": kind, "source_path": target, "owner_uid": info.st_uid,
                    "mode": stat.S_IMODE(info.st_mode), "device": info.st_dev, "inode": info.st_ino, **record})
                manifest[target] = {**record, "inode": info.st_ino, "device": info.st_dev, "mode": stat.S_IMODE(info.st_mode)}
            if workflow == 'sync':
                if (len(SYNC_FFMPEG_PROXY), hashlib.sha256(SYNC_FFMPEG_PROXY).hexdigest()) != (3046, 'eb91010e15e7cb6b16aae3a1489e207893577e10cf864214fc1372eb90c8e293'):
                    raise ReelBenchAdapterError('sync media proxy differs from independent pin')
                workspace.write(descriptor, 'tools/bin/ffmpeg', SYNC_FFMPEG_PROXY, mode=0o500, quota=True)
                manifest['tools/bin/ffmpeg'] = {'size_bytes':len(SYNC_FFMPEG_PROXY), 'sha256':hashlib.sha256(SYNC_FFMPEG_PROXY).hexdigest()}
            selected_script = self._shots_script if workflow == "shots" else self._sync_script
            script_parent = workspace.open_absolute(selected_script.parent)
            try:
                script = workspace.read(script_parent, selected_script.name)
            finally:
                workspace._close_owned([script_parent])
            lock_parent = workspace.open_absolute(self._shots_script.parents[3] / "upstream")
            try:
                lock = json.loads(workspace.read(lock_parent, "reelbench.lock.json"))
            finally:
                workspace._close_owned([lock_parent])
            lock_key = f"video-{workflow}/scripts/{selected_script.name}"
            expected = lock["files"][lock_key]["packaged_sha256"]
            if hashlib.sha256(script).hexdigest() != expected:
                raise ReelBenchAdapterError("packaged ReelBench script differs from its lock")
            if workflow == "shots" and (hashlib.sha256(script).hexdigest(), len(script)) != _PINNED_SHOTS_SCRIPTS['script/video-shots.mjs']:
                raise ValueError('packaged script differs from independent pinned digest')
            if workflow == 'sync' and (hashlib.sha256(script).hexdigest(), len(script)) != PINNED_SYNC_SCRIPTS[selected_script.name]:
                raise ValueError('packaged sync script differs from independent pinned digest')
            script_target = f"script/{selected_script.name}"
            workspace.write(descriptor, script_target, script, mode=0o400, quota=True)
            manifest[script_target] = {"size_bytes": len(script), "sha256": hashlib.sha256(script).hexdigest()}
            asset_names = ("report.css", "report.js") if workflow == "shots" else ("panel.css", "panel.html")
            for filename in asset_names:
                asset_parent = workspace.open_absolute(selected_script.parent)
                try:
                    content = workspace.read(asset_parent, filename)
                finally:
                    workspace._close_owned([asset_parent])
                expected = lock["files"][f"video-{workflow}/scripts/{filename}"]["packaged_sha256"]
                if hashlib.sha256(content).hexdigest() != expected:
                    raise ReelBenchAdapterError("packaged report asset differs from its lock")
                if workflow == "shots" and (hashlib.sha256(content).hexdigest(), len(content)) != _PINNED_SHOTS_SCRIPTS['script/' + filename]:
                    raise ValueError('packaged report asset differs from independent pinned digest')
                if workflow == 'sync' and (hashlib.sha256(content).hexdigest(), len(content)) != PINNED_SYNC_SCRIPTS[filename]:
                    raise ValueError('packaged sync asset differs from independent pinned digest')
                workspace.write(descriptor, "script/" + filename, content, mode=0o400, quota=True)
                manifest["script/" + filename] = {"size_bytes": len(content), "sha256": expected}
            if browser_lease is not None:
                if (len(BROWSER_PRELOAD), hashlib.sha256(BROWSER_PRELOAD).hexdigest()) != (819, '4e9cb955ef03a99a883e2f4760431f5ba373a3e0b50fe51b9e1c29fa65e2f2c3'):
                    raise ReelBenchAdapterError('browser preload differs from independent pin')
                if (len(BROWSER_PROXY_CODE), hashlib.sha256(BROWSER_PROXY_CODE).hexdigest()) != (5989, '015a6782cf5ff2d8b6612c868f57fbef49d616ac812c1f6478c186acf25dfbf7'):
                    raise ReelBenchAdapterError('browser proxy differs from independent pin')
                workspace.write(descriptor, 'script/browser-lease.mjs', BROWSER_PRELOAD, mode=0o400, quota=True)
                manifest['script/browser-lease.mjs'] = {'size_bytes': len(BROWSER_PRELOAD), 'sha256': hashlib.sha256(BROWSER_PRELOAD).hexdigest()}
                workspace.write(descriptor, "tools/browser-proxy", BROWSER_PROXY_CODE, mode=0o500, quota=True)
                with workspace.directory(descriptor, "tools") as tools_fd:
                    info = os.stat("browser-proxy", dir_fd=tools_fd, follow_symlinks=False)
                manifest["tools/browser-proxy"] = {"size_bytes": len(BROWSER_PROXY_CODE), "sha256": hashlib.sha256(BROWSER_PROXY_CODE).hexdigest(), "inode": info.st_ino, "device": info.st_dev, "mode": stat.S_IMODE(info.st_mode)}
            token = self._workspace.set((descriptor, identities, manifest, workflow, browser_lease))
            yield
        finally:
            primary = sys.exc_info()[1]
            if token is not None:
                self._workspace.reset(token)
            cleanup_error = None
            for tool in staged.values():
                try:
                    self._tool_store.release(tool)
                except BaseException as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            if cleanup_error is not None:
                workspace.cleanup_failure(primary, cleanup_error)

    @property
    def tool_identities(self) -> list[dict[str, str | int]]:
        """Return closed executable identities for a versioned evidence receipt."""
        return [{"kind": kind, **self._tools[kind].to_record()} for kind in sorted(self._tools)]

    def seed(self, *, source: Path, shots: Path, track: Path, threshold: float, title: str) -> ReelBenchAdapterResult:
        """Run the unchanged seed command with fixed flag ordering."""
        self._bounded_number("threshold", threshold, 0.05, 0.80)
        self._bounded_text("title", title, MAX_TITLE_CHARS)
        source_path, shots_path, track_path = self._paths(source, shots, track)
        return self._run(
            "seed",
            [str(source_path), "--threshold", self._number(threshold), "--min", "0.30", "--track", str(track_path), "--title", title],
        )

    def frames(self, *, shots: Path, source: Path, frames_dir: Path) -> ReelBenchAdapterResult:
        """Extract bounded private keyframes using the pinned upstream command."""
        shots_path, source_path, frames_path = self._paths(shots, source, frames_dir)
        return self._run("frames", [str(shots_path), "--video", str(source_path), "--dir", str(frames_path), "--width", "480"])

    def sheet(self, *, shots: Path, frames_dir: Path, sheets_dir: Path, pick: str) -> ReelBenchAdapterResult:
        """Build one deterministic A or B contact-sheet collection."""
        if pick not in {"a", "b"}:
            raise ValueError("sheet pick must be a or b")
        shots_path, frames_path, sheets_path = self._paths(shots, frames_dir, sheets_dir)
        return self._run("sheet", [str(shots_path), "--dir", str(frames_path), "--out", str(sheets_path), "--pick", pick, "--cols", "5", "--rows", "5"])

    def validate(self, *, shots: Path, track: Path, frames_dir: Path) -> ReelBenchAdapterResult:
        """Run upstream gates without collapsing PASS, FAIL, or SKIPPED semantics."""
        shots_path, track_path, frames_path = self._paths(shots, track, frames_dir)
        result = self._run("validate", [str(shots_path), "--track", str(track_path), "--frames", str(frames_path), "--lang", "en"], allow_failure=True)
        gates = self._parse_gates(result.stdout)
        failed = any(gate["status"] == "FAIL" for gate in gates)
        if result.returncode not in {0, 1} or (result.returncode == 0 and failed) or (result.returncode == 1 and not failed):
            raise ReelBenchAdapterError(f"validate exited unexpectedly: {result.returncode}")
        return ReelBenchAdapterResult(result.action, result.argv, result.shell, result.stdout, result.stderr, result.returncode, gates, result.tool_identities, result.script_manifest)

    def render(self, *, shots: Path, track: Path, frames_dir: Path, source: Path, mode: str) -> ReelBenchAdapterResult:
        """Render the unchanged Markdown or offline HTML report to bounded stdout."""
        if mode not in {"md", "html"}:
            raise ValueError("render mode must be md or html")
        shots_path, track_path, frames_path, source_path = self._paths(shots, track, frames_dir, source)
        return self._run("render", [str(shots_path), f"--{mode}", "--track", str(track_path), "--frames", str(frames_path), "--video", str(source_path)])

    def sync_plan(self, *, shots: Path, source: Path) -> ReelBenchAdapterResult:
        """Run the immutable upstream sync layout planner in its owned workspace."""
        shots_path, source_path = self._paths(shots, source)
        return self._run("plan", [str(shots_path), "--video", str(source_path)], workflow="sync")

    def sync_panels(self, *, shots: Path, source: Path, panels_dir: Path, browser: str) -> ReelBenchAdapterResult:
        """Render the upstream's three review panels using an already verified browser identity."""
        if browser != "tools/browser-proxy":
            raise ValueError("sync browser must be the fixed staged proxy")
        shots_path, source_path, panels_path = self._paths(shots, source, panels_dir)
        return self._run("panels", [str(shots_path), "--video", str(source_path), "--out", str(panels_path), "--chrome", browser], workflow="sync")

    def sync_export(self, *, shots: Path, source: Path, panels_dir: Path, output: Path, browser: str) -> ReelBenchAdapterResult:
        """Run the unchanged one-shot upstream export with fixed, service-owned paths."""
        if browser != "tools/browser-proxy":
            raise ValueError("sync browser must be the fixed staged proxy")
        shots_path, source_path, panels_path, output_path = self._paths(shots, source, panels_dir, output)
        return self._run("export", [str(shots_path), "--video", str(source_path), "--panels", str(panels_path), "-o", str(output_path), "--chrome", browser], workflow="sync")

    def _run(self, action: str, rest: Sequence[str], *, allow_failure: bool = False, workflow: str = "shots") -> ReelBenchAdapterResult:
        active = self._workspace.get()
        if active is None:
            raise ReelBenchAdapterError("production execution requires an owned action workspace")
        if active[3] != workflow:
            raise ReelBenchAdapterError("pinned workflow does not match its staged workspace")
        identities = tuple(active[1] if active else self.tool_identities)
        script_name = "video-shots.mjs" if workflow == "shots" else "video-sync.mjs"
        logical = ["tools/node", "script/" + script_name, action, *rest]
        descriptor = active[0]
        manifest = dict(active[2])
        # Bound every input available to the unchanged upstream script. Output
        # files from earlier commands are included before the next command.
        for root in (("output", "source") if workflow == 'sync' else ("output",)):
            try:
                manifest.update({f"{root}/{key}": value for key, value in workspace.inventory(descriptor, root,
                    max_bytes=4608 * 1024**2 if workflow == 'sync' else 2 * 1024**3, file_maximum=2 * 1024**3).items()})
            except FileNotFoundError:
                pass
        argv = ["/usr/bin/python3", "-I", "-c", HELPER_CODE if workflow == "shots" else SYNC_HELPER_CODE, str(descriptor),
                json.dumps(manifest, sort_keys=True), *logical]
        environment = {"PATH": "tools/bin", "LANG": "C", "LC_ALL": "C"}
        lease = active[4]
        if lease is not None:
            environment.update(lease.proxy_environment)
        if workflow == 'sync' and self._sync_duration.get() is not None:
            environment['REELBENCH_SYNC_DURATION'] = str(self._sync_duration.get())
        try:
            result = self._runner(argv, env=environment, timeout_seconds=1800 if workflow == 'sync' and action == 'export' else MAX_ACTION_SECONDS,
                stdout_cap=MAX_STDOUT_BYTES, stderr_cap=MAX_STDERR_BYTES,
                pass_fds=(active[0], *(lease.pass_fds if lease is not None else ())), monitor=lambda: workspace.check_sync_action_quota(active[0]) if workflow == 'sync' else workspace.check_action_quota(active[0]))
        except (BoundedProcessError, OSError) as exc:
            raise ReelBenchAdapterError(f"{action} did not complete safely") from exc
        if not isinstance(result, BoundedProcessResult):
            raise ReelBenchAdapterError("bounded runner returned an invalid result")
        if result.returncode != 0 and not allow_failure:
            raise ReelBenchAdapterError(f"{action} failed with exit {result.returncode}: {result.stderr[-2000:]}")
        if active:
            prefix = f"/dev/fd/{active[0]}/"
            logical = [item.removeprefix(prefix) for item in logical]
        return ReelBenchAdapterResult(action, logical if active else list(argv), False,
                                      result.stdout, result.stderr, result.returncode, (), identities,
                                      tuple({"path": key, **value} for key, value in active[2].items() if key.startswith("script/") or (workflow == 'sync' and key in {'tools/bin/ffmpeg', 'tools/browser-proxy'})))

    def sync_media(self, kind: str, arguments: Sequence[str]) -> ReelBenchAdapterResult:
        """Execute a service-built probe or media command with the same staged identities."""
        active = self._workspace.get()
        if active is None or active[3] != 'sync' or kind not in {'ffmpeg', 'ffprobe'}:
            raise ReelBenchAdapterError('media execution requires a sync workspace')
        manifest = dict(active[2])
        for directory in ('source', 'inputs', 'output'):
            try:
                manifest.update({directory + '/' + k: v for k, v in workspace.inventory(active[0], directory,
                    max_bytes=4608 * 1024**2, file_maximum=2 * 1024**3).items()})
            except FileNotFoundError:
                pass
        executable = 'tools/bin/ffmpeg-real' if kind == 'ffmpeg' else 'tools/bin/ffprobe'
        helper = HELPER_CODE.replace("if argv[:2] != ['tools/node', 'script/video-shots.mjs']:",
            "if argv[0] != '" + executable + "':").replace("os.execve('tools/node', argv,", "os.execve('" + executable + "', argv,").replace('(8388608, 8388608)', '(2147483648, 2147483648)')
        logical = [executable, *arguments]
        result = self._runner(['/usr/bin/python3', '-I', '-c', helper, str(active[0]), json.dumps(manifest), *logical],
            env={'PATH': 'tools/bin', 'LANG': 'C', 'LC_ALL': 'C'}, timeout_seconds=1800,
            stdout_cap=32 * 1024**2, stderr_cap=MAX_STDERR_BYTES, pass_fds=(active[0],),
            monitor=lambda: workspace.check_sync_action_quota(active[0]))
        if result.returncode != 0:
            raise ReelBenchAdapterError('trusted ' + kind + ' failed: ' + result.stderr[-1000:])
        return ReelBenchAdapterResult(kind, logical, False, result.stdout, result.stderr, result.returncode,
            (), tuple(active[1]), tuple({'path': key, **value} for key, value in active[2].items() if key.startswith('script/') or key in {'tools/bin/ffmpeg', 'tools/browser-proxy'}))

    def set_sync_duration(self, duration: float) -> None:
        """Bind the probed duration to this context's fixed composition proxy."""
        if self._workspace.get() is None or self._workspace.get()[3] != 'sync':
            raise ReelBenchAdapterError('sync duration needs an active workspace')
        self._bounded_number('duration', duration, .001, 1800)
        self._sync_duration.set(duration)

    def _paths(self, *values: Path) -> tuple[Path, ...]:
        return tuple(self._project_path(value) for value in values)

    def _project_path(self, value: Path) -> Path:
        path = Path(value)
        active = self._workspace.get()
        if active:
            prefix = Path(f"/dev/fd/{active[0]}")
            try:
                relative = path.relative_to(prefix)
                workspace.components(str(relative))
            except ValueError as exc:
                raise ValueError("action path must belong to its pinned workspace") from exc
            return relative
        if not path.is_absolute():
            raise ValueError("ReelBench paths must be absolute")
        candidate = path.resolve(strict=False)
        try:
            candidate.relative_to(self._project_root)
        except ValueError as exc:
            raise ValueError("ReelBench paths must be service-created project paths") from exc
        if path.is_symlink():
            raise ValueError("ReelBench paths may not be symlinks")
        return candidate

    @staticmethod
    def _bounded_number(label: str, value: float, lower: float, upper: float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= float(value) <= upper:
            raise ValueError(f"{label} must be finite and between {lower} and {upper}")

    @staticmethod
    def _bounded_text(label: str, value: str, maximum: int) -> None:
        if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
            raise ValueError(f"{label} is invalid")

    @staticmethod
    def _number(value: float) -> str:
        return f"{float(value):.2f}"

    @staticmethod
    def _parse_gates(stdout: str) -> tuple[dict[str, str], ...]:
        found: list[dict[str, str]] = []
        for line in stdout.splitlines():
            matched = _GATE_LINE.fullmatch(line)
            if matched is not None:
                found.append({"status": _GATE_MARK[matched.group("mark")], "evidence": matched.group("evidence")[:2048]})
        if len(found) != len(REELBENCH_VALIDATE_GATES):
            raise ReelBenchAdapterError("validate output did not contain exactly 15 gate lines")
        receipts = tuple({"name": name, **entry} for name, entry in zip(REELBENCH_VALIDATE_GATES, found, strict=True))
        if any(not (entry["evidence"] == _ENGLISH_GATE_LABELS[entry["name"]] or entry["evidence"].startswith(_ENGLISH_GATE_LABELS[entry["name"]] + "　")) for entry in receipts):
            raise ReelBenchAdapterError("validate gate labels are unknown, duplicated, or reordered")
        return receipts


__all__ = ["ReelBenchAdapter", "ReelBenchAdapterError", "ReelBenchAdapterResult"]
