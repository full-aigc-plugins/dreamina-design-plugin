"""Enrollment and race-safe staging for trusted local media executables."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from scripts.native_approval import NativeApprovalProvider
from scripts.bounded_process import BoundedProcessError, run_bounded

MEDIA_TOOL_KINDS = frozenset({"ffmpeg", "ffprobe", "whisper", "narration", "node", "browser"})
_LEGACY_FIELDS = frozenset({"source_path", "owner_uid", "sha256"})
_FULL_FIELDS = _LEGACY_FIELDS | frozenset({"mode", "device", "inode", "size_bytes"})
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_CODESIGN = "/usr/bin/codesign"


class TrustedMediaToolError(PermissionError):
    """A media executable or its protected trust record is invalid."""


@dataclass(frozen=True)
class TrustedMediaTool:
    kind: str
    source_path: str
    sha256: str
    staged_path: str


@dataclass(frozen=True)
class TrustedExecutable:
    """An exact executable identity reverified immediately before use."""
    kind: str; source_path: str; owner_uid: int; mode: int; device: int; inode: int; size_bytes: int; sha256: str

    def to_record(self) -> dict[str, str | int]:
        return {"source_path": self.source_path, "owner_uid": self.owner_uid, "mode": self.mode,
                "device": self.device, "inode": self.inode, "size_bytes": self.size_bytes, "sha256": self.sha256}


@dataclass(frozen=True)
class BrowserApplicationPolicy:
    identifier: str
    team_id: str
    designated_requirement: str


@dataclass(frozen=True)
class BrowserSignature:
    identifier: str
    team_id: str
    designated_requirement: str


@dataclass(frozen=True)
class BrowserLaunchContext:
    """Audit metadata only; execution is owned by BrowserLaunchHandle."""
    executable: TrustedExecutable
    signature: BrowserSignature
    verified_at: str


class BrowserLaunchHandle:
    """One-shot descriptor-owned browser launcher; it never exposes a pathname."""
    _HELPER = """import hashlib,os,sys
bundle_fd, executable_fd = int(sys.argv[1]), int(sys.argv[2])
relative, expected_digest = sys.argv[3], sys.argv[4]
os.fchdir(bundle_fd)
directory = os.open('Contents', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    macos = os.open('MacOS', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
finally:
    os.close(directory)
os.fchdir(macos)
os.close(macos)
name = relative.split('/')[-1]
candidate = os.open(name, os.O_RDONLY | os.O_NOFOLLOW)
try:
    current, pinned = os.fstat(candidate), os.fstat(executable_fd)
    identity = lambda s: (s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if identity(current) != identity(pinned): raise RuntimeError('browser identity changed')
    digest = hashlib.sha256()
    while True:
        chunk = os.read(candidate, 65536)
        if not chunk: break
        digest.update(chunk)
    if digest.hexdigest() != expected_digest or identity(current) != identity(os.fstat(candidate)):
        raise RuntimeError('browser bytes changed')
finally:
    os.close(candidate)
os.close(bundle_fd)
os.close(executable_fd)
os.execve('./' + name, [relative, *sys.argv[5:]], {'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'})
"""
    def __init__(self, descriptor: int, bundle_descriptor: int, context: BrowserLaunchContext, relative_executable: str) -> None:
        self._descriptor = descriptor; self._bundle_descriptor = bundle_descriptor; self.context = context; self._relative_executable = relative_executable; self._used = False

    def __enter__(self):
        if self._used: raise TrustedMediaToolError("browser launch handle has already been used or closed")
        return self
    def __exit__(self, *_): self.close()
    def __del__(self):
        try: self.close()
        except OSError: pass

    def spawn(self, args: list[str], *, timeout_seconds: float = 30, stdout_cap: int = 1024 * 1024, stderr_cap: int = 1024 * 1024) -> subprocess.CompletedProcess[str]:
        if self._used: raise TrustedMediaToolError("browser launch handle has already been used")
        self._used = True
        try:
            if not isinstance(args, list) or not all(isinstance(arg, str) and "\0" not in arg for arg in args):
                raise TrustedMediaToolError("browser arguments must be fixed strings")
            result = run_bounded(["/usr/bin/python3", "-I", "-c", self._HELPER, str(self._bundle_descriptor), str(self._descriptor), self._relative_executable, self.context.executable.sha256, *args], env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}, timeout_seconds=timeout_seconds, stdout_cap=stdout_cap, stderr_cap=stderr_cap, pass_fds=(self._bundle_descriptor, self._descriptor))
            if result.returncode != 0: raise TrustedMediaToolError("browser helper failed")
            return subprocess.CompletedProcess([], result.returncode, result.stdout, result.stderr)
        except (BoundedProcessError, OSError) as exc:
            raise TrustedMediaToolError("browser launch failed") from exc
        finally: self.close()

    def close(self) -> None:
        self._used = True
        descriptor, bundle_descriptor = getattr(self, "_descriptor", -1), getattr(self, "_bundle_descriptor", -1)
        self._descriptor = self._bundle_descriptor = -1
        try:
            if descriptor >= 0: os.close(descriptor)
        finally:
            if bundle_descriptor >= 0: os.close(bundle_descriptor)


BROWSER_APPLICATION_POLICIES: Mapping[str, BrowserApplicationPolicy] = {
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome": BrowserApplicationPolicy(
        "com.google.Chrome", "EQHXZ8M8AV",
        '(identifier "com.google.Chrome" or identifier "com.google.Chrome.beta" or identifier "com.google.Chrome.dev" or identifier "com.google.Chrome.canary") and anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */ and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */ and certificate leaf[subject.OU] = EQHXZ8M8AV'),
    "/Applications/Chromium.app/Contents/MacOS/Chromium": BrowserApplicationPolicy(
        "org.chromium.Chromium", "EQHXZ8M8AV", 'identifier "org.chromium.Chromium" and certificate leaf[subject.OU] = EQHXZ8M8AV'),
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge": BrowserApplicationPolicy(
        "com.microsoft.edgemac", "UBF8T346G9", 'identifier "com.microsoft.edgemac" and certificate leaf[subject.OU] = UBF8T346G9'),
}
_MACOS_BROWSER_PATHS = frozenset(BROWSER_APPLICATION_POLICIES)


class TrustedMediaToolStore:
    """Persist approved identities and stage non-browser tools from verified descriptors."""
    def __init__(self, path: Path | None = None, staging_root: Path | None = None) -> None:
        self.path = path or Path.home() / ".config" / "codex-dreamina-design" / "trusted-media-tools.json"
        self._staging_root = Path(staging_root) if staging_root else None

    def enroll(self, kind: str, path: Path, approval_provider=None) -> dict[str, str | int]:
        """Approve a tool, retaining the legacy dictionary return contract."""
        self._validate_kinds({kind})
        executable = self._inspect_source(kind, Path(path))
        if kind == "browser": self._verify_browser_signature(executable.source_path)
        provider = approval_provider or NativeApprovalProvider()
        provider.confirm_media_tool_enrollment(kind=kind, path=executable.source_path, owner_uid=executable.owner_uid, sha256=executable.sha256)
        tools = self._load_config(allow_missing=True)
        record = executable.to_record() if kind in {"node", "browser"} else {"source_path": executable.source_path, "owner_uid": executable.owner_uid, "sha256": executable.sha256}
        tools[kind] = record
        self._atomic_write({"version": 1, "tools": tools})
        return record.copy()

    def resolve_verified(self, kind: str) -> TrustedExecutable:
        """Return a typed current identity; browser launch additionally requires its dedicated seam."""
        self._validate_kinds({kind})
        enrolled = self._record_identity(kind, self._required_record(kind))
        current = self._inspect_source(kind, Path(enrolled.source_path))
        if not self._same_identity(enrolled, current): raise TrustedMediaToolError(f"trusted {kind} identity changed after enrollment")
        return current

    def reverify_browser_for_launch(self) -> BrowserLaunchHandle:
        """Return an owned one-use launch capability, never a raw launch pathname."""
        return self.reverify_browser_for_launch_handle()

    def reverify_browser_for_launch_handle(self) -> BrowserLaunchHandle:
        """Create the one-shot, descriptor-bound browser launch handle for the sync service."""
        enrolled = self._record_identity("browser", self._required_record("browser"))
        descriptor, executable = self._open_verified_fd("browser", enrolled)
        bundle_fd = None
        try:
            bundle = Path(executable.source_path).parents[2]
            bundle_fd = os.open(bundle, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            bundle_identity = os.fstat(bundle_fd)
            signature = self._verify_browser_signature(executable.source_path)
            self._assert_final_path_identity(Path(executable.source_path), os.fstat(descriptor))
            if not _same_stat_identity(bundle_identity, bundle.lstat()):
                raise TrustedMediaToolError("browser bundle changed during signature verification")
            context = BrowserLaunchContext(executable, signature, datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
            return BrowserLaunchHandle(descriptor, bundle_fd, context, f"Contents/MacOS/{Path(executable.source_path).name}")
        except BaseException:
            try: os.close(descriptor)
            finally:
                if bundle_fd is not None: os.close(bundle_fd)
            raise

    def load_required(self, kinds: Iterable[str]) -> dict[str, TrustedMediaTool]:
        """Stage non-browser executables from the very descriptor verified and hashed."""
        required = set(kinds); self._validate_kinds(required)
        if "browser" in required: raise TrustedMediaToolError("browser launch requires reverify_browser_for_launch")
        loaded: dict[str, TrustedMediaTool] = {}
        try:
            for kind in sorted(required):
                enrolled = self._record_identity(kind, self._required_record(kind))
                descriptor, executable = self._open_verified_fd(kind, enrolled)
                try: loaded[kind] = self._stage_from_verified_fd(descriptor, executable)
                finally: os.close(descriptor)
            return loaded
        except Exception:
            for tool in loaded.values(): self.release(tool)
            raise

    @staticmethod
    def release(tool: TrustedMediaTool) -> None: shutil.rmtree(Path(tool.staged_path).parent, ignore_errors=True)

    @staticmethod
    def _validate_kinds(kinds: set[str]) -> None:
        if not kinds <= MEDIA_TOOL_KINDS: raise TrustedMediaToolError("unsupported media tool kind: " + ", ".join(sorted(kinds - MEDIA_TOOL_KINDS)))

    def _required_record(self, kind: str) -> dict[str, str | int]:
        try: return self._load_config()[kind]
        except KeyError as exc: raise TrustedMediaToolError(f"required media tool is not enrolled: {kind}") from exc

    def _inspect_source(self, kind: str, source: Path) -> TrustedExecutable:
        canonical, source_stat = self._validate_source_path(kind, source)
        digest, opened_stat = _digest_opened_file(canonical)
        if (opened_stat.st_dev, opened_stat.st_ino) != (source_stat.st_dev, source_stat.st_ino): raise TrustedMediaToolError("media tool changed during enrollment")
        return self._identity(kind, canonical, opened_stat, digest)

    def _open_verified_fd(self, kind: str, expected: TrustedExecutable) -> tuple[int, TrustedExecutable]:
        canonical, source_stat = self._validate_source_path(kind, Path(expected.source_path))
        try: descriptor = os.open(canonical, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc: raise TrustedMediaToolError("media tool could not be opened safely") from exc
        try:
            before = os.fstat(descriptor)
            if (before.st_dev, before.st_ino) != (source_stat.st_dev, source_stat.st_ino): raise TrustedMediaToolError("media tool changed before trusted open")
            current = self._identity(kind, canonical, before, _digest_descriptor(descriptor))
            after = os.fstat(descriptor)
            if not _same_stat_identity(before, after): raise TrustedMediaToolError("media tool changed while being hashed")
            if not self._same_identity(expected, current): raise TrustedMediaToolError(f"trusted {kind} identity changed after enrollment")
            self._assert_final_path_identity(canonical, after)
            return descriptor, current
        except Exception:
            os.close(descriptor); raise

    def _validate_source_path(self, kind: str, source: Path) -> tuple[Path, os.stat_result]:
        if not source.is_absolute() or source.is_symlink(): raise TrustedMediaToolError("media tool enrollment requires an absolute regular non-symlink file")
        try: source_stat = source.stat()
        except OSError as exc: raise TrustedMediaToolError("media tool does not exist") from exc
        if not stat.S_ISREG(source_stat.st_mode) or not source_stat.st_mode & 0o111: raise TrustedMediaToolError("media tool must be a regular executable file")
        canonical = source.resolve(strict=True)
        if kind == "browser" and canonical != source:
            raise TrustedMediaToolError("browser path must be canonical without symlink ancestors")
        if kind == "browser" and str(canonical) not in BROWSER_APPLICATION_POLICIES: raise TrustedMediaToolError("browser enrollment requires an approved system browser executable path")
        system_say = kind == "narration" and str(canonical) == "/usr/bin/say" and source_stat.st_uid == 0
        system_browser = kind == "browser" and source_stat.st_uid == 0 and str(canonical) in BROWSER_APPLICATION_POLICIES
        signed_admin_paths = {canonical, *list(canonical.parents)[:3], Path("/Applications")} if kind == "browser" and str(canonical) in _MACOS_BROWSER_PATHS else set()

        def trusted_admin_mode(path: Path, observed: os.stat_result) -> bool:
            # Exact macOS app locations may be maintained by the admin group.
            # Enrollment and launch additionally require the pinned signature.
            owners = {0} if path == Path("/Applications") else {0, os.getuid()}
            return path in signed_admin_paths and observed.st_uid in owners and observed.st_gid == 80 and stat.S_IMODE(observed.st_mode) == 0o775

        if (source_stat.st_uid != os.getuid() and not system_say and not system_browser) or (source_stat.st_mode & 0o022 and not trusted_admin_mode(canonical, source_stat)):
            raise TrustedMediaToolError("media tool owner or write permissions are not trusted")
        for parent in canonical.parents:
            observed = parent.stat()
            if observed.st_mode & 0o022 and not trusted_admin_mode(parent, observed):
                raise TrustedMediaToolError(f"group/world-writable media tool parent: {parent}")
            if kind == "browser" and observed.st_uid not in {0, os.getuid()}:
                raise TrustedMediaToolError(f"foreign-owned browser parent: {parent}")
        return canonical, source_stat

    @staticmethod
    def _identity(kind: str, path: Path, observed: os.stat_result, digest: str) -> TrustedExecutable:
        return TrustedExecutable(kind, str(path), observed.st_uid, stat.S_IMODE(observed.st_mode), observed.st_dev, observed.st_ino, observed.st_size, digest)

    @staticmethod
    def _assert_final_path_identity(path: Path, opened: os.stat_result) -> None:
        try: final = path.lstat()
        except OSError as exc: raise TrustedMediaToolError("media tool disappeared during verification") from exc
        if not stat.S_ISREG(final.st_mode) or stat.S_ISLNK(final.st_mode) or not _same_stat_identity(opened, final): raise TrustedMediaToolError("media tool pathname identity changed during verification")

    def _stage_from_verified_fd(self, descriptor: int, executable: TrustedExecutable) -> TrustedMediaTool:
        before = os.fstat(descriptor)
        if not self._same_identity(executable, self._identity(executable.kind, Path(executable.source_path), before, executable.sha256)): raise TrustedMediaToolError(f"trusted {executable.kind} identity changed before staging")
        if self._staging_root:
            self._staging_root.mkdir(parents=True, exist_ok=True); os.chmod(self._staging_root, 0o700); root = Path(tempfile.mkdtemp(prefix=f"{executable.kind}-", dir=self._staging_root))
        else: root = Path(tempfile.mkdtemp(prefix=f"dreamina-{executable.kind}-"))
        os.chmod(root, 0o700); staged = root / executable.kind
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            copied_digest = hashlib.sha256(); copied_size = 0
            with os.fdopen(os.dup(descriptor), "rb") as reader, staged.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    copied_digest.update(chunk); copied_size += len(chunk); writer.write(chunk)
                writer.flush(); os.fsync(writer.fileno())
            after = os.fstat(descriptor)
            if not _same_stat_identity(before, after): raise TrustedMediaToolError(f"trusted {executable.kind} changed while being staged")
            if copied_size != executable.size_bytes or not hmac.compare_digest(copied_digest.hexdigest(), executable.sha256): raise TrustedMediaToolError(f"trusted {executable.kind} copied bytes do not match the verified digest")
            self._assert_final_path_identity(Path(executable.source_path), after)
            os.chmod(staged, 0o500)
            return TrustedMediaTool(executable.kind, executable.source_path, executable.sha256, str(staged))
        except Exception: shutil.rmtree(root, ignore_errors=True); raise

    @staticmethod
    def _same_identity(enrolled: TrustedExecutable, current: TrustedExecutable) -> bool:
        if enrolled.kind != current.kind or enrolled.source_path != current.source_path or enrolled.owner_uid != current.owner_uid or not hmac.compare_digest(enrolled.sha256, current.sha256): return False
        return enrolled.kind not in {"node", "browser"} or (enrolled.mode, enrolled.device, enrolled.inode, enrolled.size_bytes) == (current.mode, current.device, current.inode, current.size_bytes)

    def _verify_browser_signature(self, path: str) -> BrowserSignature:
        try: policy = BROWSER_APPLICATION_POLICIES[path]
        except KeyError as exc: raise TrustedMediaToolError("browser path is not an approved application executable") from exc
        detail = ""
        for index, argv in enumerate(([_CODESIGN, "--verify", "--strict", "--deep", "--verbose=2", path], [_CODESIGN, "-d", "--verbose=4", "-r-", path])):
            try: result = run_bounded(argv, env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}, timeout_seconds=10, stdout_cap=65536, stderr_cap=65536)
            except (OSError, BoundedProcessError) as exc: raise TrustedMediaToolError("macOS codesign is unavailable for browser verification") from exc
            if result.returncode != 0: raise TrustedMediaToolError("browser code signature verification failed")
            if index: detail = (result.stdout or "") + "\n" + (result.stderr or "")
        signature = _parse_codesign_details(detail)
        if signature != BrowserSignature(policy.identifier, policy.team_id, policy.designated_requirement): raise TrustedMediaToolError("browser code signature does not match the approved application policy")
        return signature

    def _record_identity(self, kind: str, record: Mapping[str, str | int]) -> TrustedExecutable:
        if set(record) != (_FULL_FIELDS if kind in {"node", "browser"} else _LEGACY_FIELDS): raise TrustedMediaToolError("trusted media tool record has unexpected fields")
        path, uid, digest = record.get("source_path"), record.get("owner_uid"), record.get("sha256")
        if not isinstance(path, str) or not Path(path).is_absolute(): raise TrustedMediaToolError("trusted media tool source path is invalid")
        if not isinstance(uid, int) or isinstance(uid, bool): raise TrustedMediaToolError("trusted media tool owner UID is invalid")
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None: raise TrustedMediaToolError("trusted media tool digest is invalid")
        say = kind == "narration" and path == "/usr/bin/say" and uid == 0; browser = kind == "browser" and path in BROWSER_APPLICATION_POLICIES and uid == 0
        if uid != os.getuid() and not say and not browser: raise TrustedMediaToolError("trusted media tool recorded owner does not match user")
        values = {field: 0 for field in ("mode", "device", "inode", "size_bytes")}
        if kind in {"node", "browser"}:
            values = {field: record[field] for field in values}
            if not isinstance(values["mode"], int) or isinstance(values["mode"], bool) or not 0 <= values["mode"] <= 0o777 or any(not isinstance(values[field], int) or isinstance(values[field], bool) or values[field] < 0 for field in ("device", "inode", "size_bytes")): raise TrustedMediaToolError("trusted executable identity metadata is invalid")
        return TrustedExecutable(kind, path, uid, values["mode"], values["device"], values["inode"], values["size_bytes"], digest)

    def _load_config(self, *, allow_missing: bool = False) -> dict[str, dict[str, str | int]]:
        self._ensure_private_config_directory(create=allow_missing)
        if not self.path.exists():
            if allow_missing: return {}
            raise TrustedMediaToolError("trusted media tools are not enrolled")
        if self.path.is_symlink() or not self.path.is_file(): raise TrustedMediaToolError("trusted media tool config must be a regular file")
        current = self.path.stat()
        if current.st_uid != os.getuid() or stat.S_IMODE(current.st_mode) != 0o600: raise TrustedMediaToolError("trusted media tool config must be user-owned mode 0600")
        parent = self.path.parent
        if parent.is_symlink() or parent.stat().st_uid != os.getuid() or stat.S_IMODE(parent.stat().st_mode) != 0o700: raise TrustedMediaToolError("trusted media tool config directory must be user-owned mode 0700")
        try: payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: raise TrustedMediaToolError("trusted media tool config is invalid") from exc
        if not isinstance(payload, dict) or set(payload) != {"version", "tools"} or payload["version"] != 1 or not isinstance(payload["tools"], dict): raise TrustedMediaToolError("trusted media tool config has unexpected fields")
        tools: dict[str, dict[str, str | int]] = {}
        for kind, record in payload["tools"].items():
            self._validate_kinds({kind})
            if not isinstance(record, dict): raise TrustedMediaToolError("trusted media tool record is invalid")
            self._record_identity(kind, record); tools[kind] = record.copy()
        return tools

    def _atomic_write(self, payload: dict[str, object]) -> None:
        directory_fd = self._open_private_config_directory(create=True); name = f".trusted-media-tools.{secrets.token_hex(12)}"; flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0); fd: int | None = None; created = False
        try:
            fd = os.open(name, flags, 0o600, dir_fd=directory_fd); created = True; os.fchmod(fd, 0o600); handle = os.fdopen(fd, "w", encoding="utf-8"); fd = None
            with handle: json.dump(payload, handle, indent=2, sort_keys=True); handle.flush(); os.fsync(handle.fileno())
            os.replace(name, self.path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd); os.fsync(directory_fd)
        except Exception:
            if created:
                try: os.unlink(name, dir_fd=directory_fd)
                except FileNotFoundError: pass
            raise
        finally:
            try:
                if fd is not None: os.close(fd)
            finally: os.close(directory_fd)

    def _ensure_private_config_directory(self, *, create: bool) -> os.stat_result:
        parent = self.path.parent
        try: observed = parent.lstat()
        except FileNotFoundError:
            if not create: raise TrustedMediaToolError("trusted media tool config directory does not exist")
            parent.parent.mkdir(parents=True, exist_ok=True)
            try: os.mkdir(parent, 0o700)
            except FileExistsError: pass
            observed = parent.lstat()
        if not stat.S_ISDIR(observed.st_mode) or observed.st_uid != os.getuid() or stat.S_IMODE(observed.st_mode) != 0o700: raise TrustedMediaToolError("trusted media tool config directory must be a user-owned non-symlink mode 0700 directory")
        return observed

    def _open_private_config_directory(self, *, create: bool) -> int:
        expected = self._ensure_private_config_directory(create=create)
        try: fd = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc: raise TrustedMediaToolError("trusted media tool config directory is unsafe") from exc
        observed = os.fstat(fd)
        if (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino) or observed.st_uid != os.getuid() or stat.S_IMODE(observed.st_mode) != 0o700: os.close(fd); raise TrustedMediaToolError("trusted media tool config directory changed")
        return fd


def _same_stat_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino, first.st_uid, stat.S_IMODE(first.st_mode), first.st_size, first.st_mtime_ns, first.st_ctime_ns) == (second.st_dev, second.st_ino, second.st_uid, stat.S_IMODE(second.st_mode), second.st_size, second.st_mtime_ns, second.st_ctime_ns)

def _digest_descriptor(fd: int) -> str:
    digest = hashlib.sha256(); os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, 1024 * 1024): digest.update(chunk)
    os.lseek(fd, 0, os.SEEK_SET); return digest.hexdigest()

def _digest_opened_file(path: Path) -> tuple[str, os.stat_result]:
    try: fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc: raise TrustedMediaToolError("media tool could not be opened safely") from exc
    try: return _digest_descriptor(fd), os.fstat(fd)
    finally: os.close(fd)

def _parse_codesign_details(details: str) -> BrowserSignature:
    identifier = re.search(r"^Identifier=(.+)$", details, re.MULTILINE); team = re.search(r"^TeamIdentifier=(.+)$", details, re.MULTILINE); requirement = re.search(r"^designated => (.+)$", details, re.MULTILINE)
    if identifier is None or team is None or requirement is None: raise TrustedMediaToolError("browser code signature details are incomplete")
    return BrowserSignature(identifier.group(1).strip(), team.group(1).strip(), requirement.group(1).strip())

__all__ = ["BROWSER_APPLICATION_POLICIES", "BrowserApplicationPolicy", "BrowserLaunchContext", "BrowserLaunchHandle", "BrowserSignature", "MEDIA_TOOL_KINDS", "TrustedExecutable", "TrustedMediaTool", "TrustedMediaToolError", "TrustedMediaToolStore"]
