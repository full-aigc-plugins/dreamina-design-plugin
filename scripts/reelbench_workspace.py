"""Descriptor-relative file operations for one owned ReelBench action."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 256
MAX_TREE_BYTES = 256 * 1024 * 1024
MAX_WORKSPACE_FILES = 512


class WorkspaceQuotaError(ValueError):
    """The private workspace exhausted its aggregate byte or entry budget."""


def check_quota(root, reserve=0, reserve_files=0):
    """Measure current entries without loading file contents into memory."""
    count = 0
    total = reserve
    directories = 0
    def visit(fd, depth):
        nonlocal count, total, directories
        if depth > 5:
            raise WorkspaceQuotaError('workspace depth quota exceeded')
        with os.scandir(fd) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    directories += 1
                    if directories > 64:
                        raise WorkspaceQuotaError('workspace directory quota exceeded')
                    with directory(fd, entry.name) as child:
                        visit(child, depth + 1)
                elif stat.S_ISREG(info.st_mode):
                    count += 1
                    total += info.st_size
                    if count > MAX_WORKSPACE_FILES or total > MAX_TREE_BYTES:
                        raise WorkspaceQuotaError('workspace aggregate quota exceeded')
                else:
                    raise WorkspaceQuotaError('workspace contains unsafe entry')
    with directory(root) as fd:
        visit(fd, 0)
    if total > MAX_TREE_BYTES or count + reserve_files > MAX_WORKSPACE_FILES:
        raise WorkspaceQuotaError('workspace aggregate quota exceeded')
    return total, count


def components(value):
    text = str(value)
    path = PurePosixPath(text)
    if path.is_absolute() or str(path) != text or any(p in {'.', '..'} for p in path.parts) or not path.parts:
        raise ValueError('noncanonical workspace path')
    return path.parts


def _close_owned(owned):
    """Close all descriptors even when ownership-transfer cleanup raises."""
    primary = sys.exc_info()[1]
    first = None
    for descriptor in reversed(owned):
        try:
            os.close(descriptor)
        except BaseException as exc:
            if first is None:
                first = exc
            # An injected/pre-close failure can leave the descriptor open.
            # No new descriptor is opened during this cleanup.
            try:
                os.close(descriptor)
            except BaseException:
                pass
    owned.clear()
    if primary is None and first is not None:
        raise first


@contextmanager
def directory(root, relative=None):
    owned = [os.dup(root)]
    try:
        if relative is not None:
            for part in components(relative):
                child = os.open(part, DIRECTORY, dir_fd=owned[-1])
                owned.append(child)
                previous = owned[-2]
                os.close(previous)
                owned.remove(previous)
        yield owned[-1]
    finally:
        _close_owned(owned)


def open_absolute(path):
    """Pin every ancestor; newly opened handles immediately have an owner."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('absolute root required')
    owned = [os.open('/', DIRECTORY)]
    try:
        for part in path.parts[1:]:
            child = os.open(part, DIRECTORY, dir_fd=owned[-1])
            owned.append(child)
            previous = owned[-2]
            os.close(previous)
            owned.remove(previous)
        return owned.pop()
    finally:
        _close_owned(owned)


@contextmanager
def file_at(root, name):
    parts = components(name)
    with directory(root, '/'.join(parts[:-1]) if len(parts) > 1 else None) as parent:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.getuid()}:
                raise ValueError('unsafe file type or owner')
            yield fd
        finally:
            _close_owned([fd])


def mkdir(root, name):
    owned = [os.dup(root)]
    try:
        for part in components(name):
            try:
                os.mkdir(part, 0o700, dir_fd=owned[-1])
                os.fsync(owned[-1])
            except FileExistsError:
                pass
            child = os.open(part, DIRECTORY, dir_fd=owned[-1])
            owned.append(child)
            info = os.fstat(child)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('workspace directory is not private')
            previous = owned[-2]
            os.close(previous)
            owned.remove(previous)
    finally:
        _close_owned(owned)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_uid, info.st_mtime_ns, info.st_ctime_ns)


def read(root, name, maximum=MAX_FILE_BYTES):
    with file_at(root, name) as fd:
        before = os.fstat(fd)
        if before.st_size > maximum:
            raise ValueError('file exceeds preventive byte bound')
        chunks = []
        total = 0
        while chunk := os.read(fd, min(1024 * 1024, maximum - total + 1)):
            total += len(chunk)
            if total > maximum:
                raise ValueError('file exceeds byte bound')
            chunks.append(chunk)
        if identity(before) != identity(os.fstat(fd)):
            raise ValueError('file changed while reading')
        return b''.join(chunks)


def write(root, name, payload, mode=0o600, *, quota=False):
    if quota:
        check_quota(root, reserve=len(payload), reserve_files=1)
    parts = components(name)
    if len(parts) > 1:
        mkdir(root, '/'.join(parts[:-1]))
    with directory(root, '/'.join(parts[:-1]) if len(parts) > 1 else None) as parent:
        fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
        try:
            view = memoryview(payload)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError('short write')
                view = view[count:]
            os.fsync(fd)
        finally:
            _close_owned([fd])
        os.fsync(parent)


def copy(root, name, destination, target, *, maximum, expected=None, mode=0o600, quota=False):
    """Stream the exact opened bytes; never re-open a verified source pathname."""
    parts = components(target)
    if len(parts) > 1:
        mkdir(destination, '/'.join(parts[:-1]))
    with file_at(root, name) as source, directory(destination, '/'.join(parts[:-1]) if len(parts) > 1 else None) as parent:
        before = os.fstat(source)
        if before.st_size < 1 or before.st_size > maximum:
            raise ValueError('input file size exceeds preventive bound')
        if quota:
            check_quota(destination, reserve=before.st_size, reserve_files=1)
        out = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
        digest = hashlib.sha256()
        total = 0
        try:
            while chunk := os.read(source, min(1024 * 1024, maximum - total + 1)):
                total += len(chunk)
                if total > maximum:
                    raise ValueError('copied input exceeds bound')
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    count = os.write(out, view)
                    if count <= 0:
                        raise OSError('short write')
                    view = view[count:]
            if identity(before) != identity(os.fstat(source)):
                raise ValueError('input identity changed while copying')
            record = {'size_bytes': total, 'sha256': digest.hexdigest()}
            if expected is not None and any(record[k] != expected[k] for k in record):
                raise ValueError('copied artifact digest or size does not match receipt')
            os.fsync(out)
            result_info = os.fstat(out)
        finally:
            _close_owned([out])
        os.fsync(parent)
        return record, result_info


def inventory(root, relative=None, *, maximum=MAX_FILES, max_bytes=MAX_TREE_BYTES, file_maximum=MAX_FILE_BYTES, private=False):
    """Reject special entries and depth/count/size overflow before reading bytes."""
    result = {}
    total = 0
    def visit(fd, prefix, depth):
        nonlocal total
        with os.scandir(fd) as entries:
            count = 0
            for entry in entries:
                count += 1
                if count > maximum or len(result) >= maximum:
                    raise ValueError('artifact inventory exceeds count bound')
                info = entry.stat(follow_symlinks=False)
                if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
                    raise ValueError('ancestor artifact is not private')
                name = prefix + entry.name
                if stat.S_ISDIR(info.st_mode):
                    if depth >= 3:
                        raise ValueError('artifact tree exceeds depth bound')
                    with directory(fd, entry.name) as child:
                        visit(child, name + '/', depth + 1)
                elif stat.S_ISREG(info.st_mode):
                    if info.st_size < 1 or info.st_size > file_maximum:
                        raise ValueError('artifact exceeds file byte bound')
                    total += info.st_size
                    if total > max_bytes:
                        raise ValueError('artifact tree exceeds aggregate byte bound')
                    with file_at(fd, entry.name) as source:
                        before = os.fstat(source)
                        digest = hashlib.sha256()
                        size = 0
                        while chunk := os.read(source, min(1024 * 1024, file_maximum - size + 1)):
                            size += len(chunk)
                            if size > file_maximum:
                                raise ValueError('artifact exceeds file byte bound')
                            digest.update(chunk)
                        if identity(before) != identity(os.fstat(source)):
                            raise ValueError('artifact changed while hashing')
                    result[name] = {'size_bytes': size, 'sha256': digest.hexdigest()}
                else:
                    raise ValueError('artifact inventory contains unsafe entry')
    with directory(root, relative) as fd:
        visit(fd, '', 0)
    return result


def remove_tree(root, name, *, expected=None):
    """Remove only descendants of the already pinned parent, without symlink traversal."""
    with directory(root, name) as fd:
        info = os.fstat(fd)
        identity = {"device": info.st_dev, "inode": info.st_ino}
        if expected is not None and expected != identity:
            raise ValueError("cleanup directory identity changed")
        for entry in os.listdir(fd):
            info = os.stat(entry, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                remove_tree(fd, entry)
            else:
                os.unlink(entry, dir_fd=fd)
    current = os.stat(name, dir_fd=root, follow_symlinks=False)
    if {"device": current.st_dev, "inode": current.st_ino} != identity:
        raise ValueError("cleanup directory identity changed")
    os.rmdir(name, dir_fd=root)
    os.fsync(root)
