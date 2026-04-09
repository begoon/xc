#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "boto3",
#   "google-cloud-storage",
#   "oci",
#   "google-api-python-client",
#   "google-auth",
# ]
# ///
"""xc - two-panel console file manager."""

from __future__ import annotations

import bz2
import curses
import fnmatch
import gzip
import io
import json
import logging
import lzma
import os
import select
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import termios
import time
import tty
import urllib.request
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

VERSION = "0.2.13"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB


def xc_dir() -> Path:
    d = Path.home() / ".xc"
    d.mkdir(parents=True, exist_ok=True)
    return d


def truncate_log(path: Path) -> None:
    try:
        sz = path.stat().st_size
    except OSError:
        return
    if sz <= MAX_LOG_SIZE:
        return
    with open(path, "rb") as f:
        f.seek(sz - MAX_LOG_SIZE)
        tail = f.read()
    idx = tail.find(b"\n")
    if idx >= 0:
        tail = tail[idx + 1 :]
    path.write_bytes(tail)


class LogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        msg = record.getMessage()
        return f"{ts} {record.levelname} {msg}"


def init_logging() -> None:
    log_path = xc_dir() / "xc.log"
    truncate_log(log_path)
    handler = logging.FileHandler(str(log_path), mode="a")
    handler.setFormatter(LogFormatter())
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)


log = logging.getLogger("xc")

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


@dataclass
class AppState:
    panels: list[str] = field(default_factory=lambda: ["", ""])
    active: int = 0
    copy_history: list[list[str]] = field(default_factory=lambda: [[], []])
    cmd_history: list[str] = field(default_factory=list)


def load_state() -> AppState | None:
    p = xc_dir() / "xc.json"
    try:
        data = json.loads(p.read_text())
        st = AppState()
        st.panels = data.get("panels", ["", ""])
        st.active = data.get("active", 0)
        st.copy_history = data.get("copy_history", [[], []])
        st.cmd_history = data.get("cmd_history", [])
        return st
    except Exception:
        return None


def save_state(st: AppState) -> None:
    p = xc_dir() / "xc.json"
    try:
        p.write_text(
            json.dumps(
                {
                    "panels": st.panels,
                    "active": st.active,
                    "copy_history": st.copy_history,
                    "cmd_history": st.cmd_history,
                },
                indent=2,
            )
        )
    except Exception as e:
        log.error("saveState: %s", e)


# ---------------------------------------------------------------------------
# VFS abstraction
# ---------------------------------------------------------------------------

FILE_TYPE_FILE = 0
FILE_TYPE_DIR = 1
FILE_TYPE_SYMLINK = 2


@dataclass
class VFile:
    name: str
    size: int = 0
    file_type: int = FILE_TYPE_FILE
    mod_time: float = 0.0  # unix timestamp
    executable: bool = False
    link_target: str = ""

    def is_dir(self) -> bool:
        return self.file_type == FILE_TYPE_DIR

    def is_symlink(self) -> bool:
        return self.file_type == FILE_TYPE_SYMLINK

    def is_executable(self) -> bool:
        return self.executable

    def ext(self) -> str:
        if self.file_type == FILE_TYPE_DIR:
            return ""
        name = self.name
        if name.startswith("."):
            name = name[1:]
        _, e = os.path.splitext(name)
        return e

    def base_name(self) -> str:
        e = self.ext()
        if not e:
            return self.name
        return self.name[: len(self.name) - len(e)]


def format_size(size: int) -> str:
    if size < 1024:
        return str(size)
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}k"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}M"
    return f"{size / (1024 * 1024 * 1024):.1f}G"


def shorten_name(s: str, width: int) -> str:
    """Shorten to width: beginning~.ext or beginning~last3 if no ext."""
    if len(s) <= width:
        return s
    if width <= 4:
        return s[:width]
    _, ext = os.path.splitext(s.rstrip("/"))
    trail = "/" if s.endswith("/") else ""
    suffix = (ext + trail) if ext else s[-3:]
    if 1 + 1 + len(suffix) > width:
        return s[: width - 1] + "~"
    avail = width - 1 - len(suffix)
    return s[:avail] + "~" + suffix


def pad_or_truncate(s: str, width: int) -> str:
    s = shorten_name(s, width)
    return s + " " * (width - len(s))


def render_file(f: VFile, width: int, dir_size: int = -1) -> str:
    if f.mod_time:
        dt = datetime.fromtimestamp(f.mod_time)
        date_str = dt.strftime("%y-%m-%d %H:%M")
    else:
        date_str = "             "
    size_width = 6
    name_ext_width = width - 23
    if name_ext_width < 1:
        name_ext_width = 1

    prefix = " "
    if f.is_dir():
        if dir_size >= 0:
            size_str = format_size(dir_size).rjust(size_width)
        else:
            size_str = "<DIR>".rjust(size_width)
        name_ext = pad_or_truncate(f.name + "/", name_ext_width)
    elif f.is_symlink():
        prefix = "@"
        size_str = "<LNK>".rjust(size_width)
        display = f.name
        if f.link_target:
            display = f.name + " -> " + f.link_target
        name_ext = pad_or_truncate(display, name_ext_width)
    else:
        size_str = format_size(f.size).rjust(size_width)
        name_ext = pad_or_truncate(f.name, name_ext_width)
        if f.executable:
            prefix = "*"

    return prefix + name_ext + " " + size_str + " " + date_str


def render_grep_result(f: VFile, width: int) -> str:
    name = f.name
    if f.is_dir():
        name += "/"
    if len(name) > width:
        if width > 3:
            name = "..." + name[-(width - 3) :]
        else:
            name = name[-width:]
    return name + " " * max(0, width - len(name))


def sort_files(files: list[VFile]) -> list[VFile]:
    return sorted(files, key=lambda f: (not f.is_dir(), f.name))


def _expand_home(path: str) -> str:
    """Expand ~, $HOME, and $(HOME) prefixes in *path*."""
    home = os.path.expanduser("~")
    for prefix in ("$(HOME)", "$HOME"):
        if path == prefix:
            return home
        if path.startswith(prefix + "/") or path.startswith(prefix + os.sep):
            return home + path[len(prefix) :]
    return os.path.expanduser(path)


# ---------------------------------------------------------------------------
# File associations (platform-specific)
# ---------------------------------------------------------------------------


@dataclass
class Assoc:
    cmd: str
    fire_and_forget: bool = False


ASSOCIATIONS: dict[str, Assoc] = {}

if sys.platform == "darwin":
    for _ext in (".jpeg", ".jpg", ".png", ".mov", ".mp4", ".pdf"):
        ASSOCIATIONS[_ext] = Assoc(cmd="open %f", fire_and_forget=True)

if sys.platform == "darwin" or sys.platform.startswith("linux"):
    ASSOCIATIONS[".json"] = Assoc(cmd="cat %f | jq")


class VFS(ABC):
    label: str = ""

    @abstractmethod
    def probe(self, header: bytes, filename: str) -> bool: ...
    @abstractmethod
    def enter(self, header: bytes, filename: str, cwd: str = "") -> VFS: ...
    @abstractmethod
    def read_dir(self, path: str) -> list[VFile]: ...
    @abstractmethod
    def read_file(self, path: str) -> io.IOBase: ...
    @abstractmethod
    def write_file(self, path: str, data: io.IOBase) -> None: ...
    @abstractmethod
    def mkdir_all(self, path: str) -> None: ...
    @abstractmethod
    def leave(self) -> None: ...


class LocalFS(VFS):
    def probe(self, header: bytes, filename: str) -> bool:
        return os.path.isdir(filename)

    def enter(self, header: bytes, filename: str, cwd: str = "") -> VFS:
        return self

    def read_dir(self, path: str) -> list[VFile]:
        files: list[VFile] = []
        try:
            entries = os.scandir(path)
        except OSError as e:
            raise e
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            ft = FILE_TYPE_FILE
            is_symlink = entry.is_symlink()
            if is_symlink:
                ft = FILE_TYPE_SYMLINK
            elif entry.is_dir(follow_symlinks=False):
                ft = FILE_TYPE_DIR
            executable = ft == FILE_TYPE_FILE and (info.st_mode & 0o111) != 0
            vf = VFile(
                name=entry.name,
                size=info.st_size,
                file_type=ft,
                mod_time=info.st_mtime,
                executable=executable,
            )
            if is_symlink:
                try:
                    vf.link_target = os.readlink(os.path.join(path, entry.name))
                except OSError:
                    pass
            files.append(vf)
        return sort_files(files)

    def read_file(self, path: str) -> io.IOBase:
        return open(path, "rb")

    def write_file(self, path: str, data: io.IOBase) -> None:
        with open(path, "wb") as f:
            shutil.copyfileobj(data, f)

    def mkdir_all(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def leave(self) -> None:
        pass


class TarFS(VFS):
    label = "TAR"

    def __init__(self) -> None:
        self.dirs: dict[str, list[VFile]] | None = None
        self.archive_path: str = ""
        self.tar_mode: str = "r:"
        self.tf: tarfile.TarFile | None = None
        self._members: dict[str, tarfile.TarInfo] = {}

    def probe(self, header: bytes, filename: str) -> bool:
        lower = filename.lower()
        return any(
            lower.endswith(ext)
            for ext in (
                ".tar",
                ".tar.gz",
                ".tgz",
                ".tar.bz2",
                ".tbz2",
                ".tar.xz",
                ".txz",
            )
        )

    def enter(self, header: bytes, filename: str, cwd: str = "") -> VFS:
        lower = filename.lower()
        mode = "r:*"
        if lower.endswith(".gz") or lower.endswith(".tgz"):
            mode = "r:gz"
        elif lower.endswith(".bz2") or lower.endswith(".tbz2"):
            mode = "r:bz2"
        elif lower.endswith(".xz") or lower.endswith(".txz"):
            mode = "r:xz"
        tf = tarfile.open(filename, mode)
        dirs: dict[str, list[VFile]] = {}
        seen: set[str] = set()
        members: dict[str, tarfile.TarInfo] = {}

        def ensure_dir_chain(dir_path: str) -> None:
            if not dir_path:
                return
            parent = os.path.dirname(dir_path)
            if parent == ".":
                parent = ""
            base = os.path.basename(dir_path)
            key = parent + "\x00" + base
            if key in seen:
                return
            seen.add(key)
            ensure_dir_chain(parent)
            dirs.setdefault(parent, []).append(
                VFile(name=base, file_type=FILE_TYPE_DIR)
            )

        for member in tf:
            name = os.path.normpath(member.name)
            if name in (".", ""):
                continue
            d = os.path.dirname(name)
            if d == ".":
                d = ""
            base = os.path.basename(name)

            ft = FILE_TYPE_FILE
            if member.isdir():
                ft = FILE_TYPE_DIR
            elif member.issym():
                ft = FILE_TYPE_SYMLINK

            key = d + "\x00" + base
            if key in seen:
                continue
            seen.add(key)
            if not member.isdir():
                members[name] = member
            ensure_dir_chain(d)
            dirs.setdefault(d, []).append(
                VFile(
                    name=base,
                    size=member.size,
                    file_type=ft,
                    mod_time=member.mtime,
                )
            )

        for d in dirs:
            dirs[d] = sort_files(dirs[d])
        new_fs = TarFS()
        new_fs.dirs = dirs
        new_fs.archive_path = filename
        new_fs.tar_mode = mode
        new_fs.tf = tf
        new_fs._members = members
        return new_fs

    def read_dir(self, path: str) -> list[VFile]:
        if self.dirs is None:
            raise OSError("tar not opened")
        if path not in self.dirs:
            raise OSError(f"directory not found in archive: {path}")
        return self.dirs[path]

    def read_file(self, path: str) -> io.IOBase:
        if self.tf is not None and path in self._members:
            f = self.tf.extractfile(self._members[path])
            if f is None:
                raise OSError(f"cannot read {path} from tar archive")
            data = f.read()
            f.close()
            return io.BytesIO(data)
        tf = tarfile.open(self.archive_path, self.tar_mode)
        member = tf.getmember(path)
        f = tf.extractfile(member)
        if f is None:
            tf.close()
            raise OSError(f"cannot read {path} from tar archive")
        data = f.read()
        f.close()
        tf.close()
        return io.BytesIO(data)

    def read_files(self, paths: set[str]) -> dict[str, io.BytesIO]:
        """Extract multiple files in one sequential pass (no repeated decompression)."""
        result: dict[str, io.BytesIO] = {}
        remaining = set(paths)
        stream_mode = self.tar_mode.replace("r:", "r|")
        with tarfile.open(self.archive_path, stream_mode) as tf:
            for member in tf:
                if not remaining:
                    break
                name = os.path.normpath(member.name)
                if name in remaining:
                    f = tf.extractfile(member)
                    if f is not None:
                        result[name] = io.BytesIO(f.read())
                        f.close()
                    remaining.discard(name)
        return result

    def write_file(self, path: str, data: io.IOBase) -> None:
        raise OSError("writing to tar archives not supported")

    def mkdir_all(self, path: str) -> None:
        raise OSError("creating directories in tar archives not supported")

    def leave(self) -> None:
        if self.tf is not None:
            self.tf.close()
            self.tf = None
        self._members = {}
        self.dirs = None
        self.archive_path = ""


class ZipFS(VFS):
    label = "ZIP"

    def __init__(self) -> None:
        self.dirs: dict[str, list[VFile]] | None = None
        self.archive_path: str = ""

    def probe(self, header: bytes, filename: str) -> bool:
        return filename.lower().endswith(".zip")

    def enter(self, header: bytes, filename: str, cwd: str = "") -> VFS:
        zf = zipfile.ZipFile(filename, "r")
        dirs: dict[str, list[VFile]] = {}
        seen: set[str] = set()

        def ensure_dir_chain(dir_path: str) -> None:
            if not dir_path:
                return
            parent = os.path.dirname(dir_path)
            if parent == ".":
                parent = ""
            base = os.path.basename(dir_path)
            key = parent + "\x00" + base
            if key in seen:
                return
            seen.add(key)
            ensure_dir_chain(parent)
            dirs.setdefault(parent, []).append(
                VFile(name=base, file_type=FILE_TYPE_DIR)
            )

        for info in zf.infolist():
            name = os.path.normpath(info.filename)
            if name in (".", ""):
                continue
            d = os.path.dirname(name)
            if d == ".":
                d = ""
            base = os.path.basename(name)
            if not base:
                continue

            is_dir = info.filename.endswith("/")
            ft = FILE_TYPE_DIR if is_dir else FILE_TYPE_FILE

            key = d + "\x00" + base
            if key in seen:
                continue
            seen.add(key)
            ensure_dir_chain(d)

            mod_time = 0.0
            try:
                from datetime import datetime as _dt

                mod_time = _dt(*info.date_time).timestamp()
            except (ValueError, OSError):
                pass

            dirs.setdefault(d, []).append(
                VFile(
                    name=base,
                    size=info.file_size,
                    file_type=ft,
                    mod_time=mod_time,
                )
            )

        zf.close()
        for d in dirs:
            dirs[d] = sort_files(dirs[d])
        new_fs = ZipFS()
        new_fs.dirs = dirs
        new_fs.archive_path = filename
        return new_fs

    def read_dir(self, path: str) -> list[VFile]:
        if self.dirs is None:
            raise OSError("zip not opened")
        if path not in self.dirs:
            raise OSError(f"directory not found in archive: {path}")
        return self.dirs[path]

    def read_file(self, path: str) -> io.IOBase:
        zf = zipfile.ZipFile(self.archive_path, "r")
        data = zf.read(path)
        zf.close()
        return io.BytesIO(data)

    def write_file(self, path: str, data: io.IOBase) -> None:
        raise OSError("writing to zip archives not supported")

    def mkdir_all(self, path: str) -> None:
        raise OSError("creating directories in zip archives not supported")

    def leave(self) -> None:
        self.dirs = None
        self.archive_path = ""


class CompressedFS(VFS):
    label = "GZ"

    EXTS: dict[str, tuple[str, type]] = {
        ".gz": ("GZ", gzip.GzipFile),
        ".bz2": ("BZ2", bz2.BZ2File),
        ".xz": ("XZ", lzma.LZMAFile),
        ".lzma": ("LZMA", lzma.LZMAFile),
    }

    # Extensions that other VFS types handle (TarFS)
    SKIP = (".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")

    def __init__(self) -> None:
        self.inner_name: str = ""
        self.inner_size: int = 0
        self.inner_mtime: float = 0.0
        self.archive_path: str = ""
        self.ext: str = ""

    def probe(self, header: bytes, filename: str) -> bool:
        lower = filename.lower()
        if any(lower.endswith(s) for s in self.SKIP):
            return False
        return any(lower.endswith(ext) for ext in self.EXTS)

    def _ext(self, filename: str) -> str:
        lower = filename.lower()
        for ext in self.EXTS:
            if lower.endswith(ext):
                return ext
        return ""

    def enter(self, header: bytes, filename: str, cwd: str = "") -> VFS:
        ext = self._ext(filename)
        label, opener = self.EXTS[ext]
        base = os.path.basename(filename)
        inner_name = base[: -len(ext)]
        if not inner_name:
            inner_name = base + ".out"

        # Read to get the decompressed size and mtime
        with opener(filename, "rb") as f:
            data = f.read()

        try:
            file_mtime = os.path.getmtime(filename)
        except OSError:
            file_mtime = 0.0

        new_fs = CompressedFS()
        new_fs.label = label
        new_fs.inner_name = inner_name
        new_fs.inner_size = len(data)
        new_fs.inner_mtime = file_mtime
        new_fs.archive_path = filename
        new_fs.ext = ext
        return new_fs

    def read_dir(self, path: str) -> list[VFile]:
        if path != "":
            raise OSError(f"directory not found: {path}")
        return [
            VFile(
                name=self.inner_name,
                size=self.inner_size,
                file_type=FILE_TYPE_FILE,
                mod_time=self.inner_mtime,
            )
        ]

    def read_file(self, path: str) -> io.IOBase:
        _, opener = self.EXTS[self.ext]
        with opener(self.archive_path, "rb") as f:
            data = f.read()
        return io.BytesIO(data)

    def write_file(self, path: str, data: io.IOBase) -> None:
        raise OSError("writing to compressed archives not supported")

    def mkdir_all(self, path: str) -> None:
        raise OSError(
            "creating directories in compressed archives not supported"
        )

    def leave(self) -> None:
        pass


class S3FS(VFS):
    label = "S3"

    def __init__(self) -> None:
        self.client = None
        self.bucket = ""

    def probe(self, header: bytes, filename: str) -> bool:
        if not filename.lower().endswith(".s3"):
            return False
        return header.startswith(b"type=s3")

    def enter(self, header: bytes, filename: str, cwd: str = "") -> VFS:
        bucket = access_key = secret_key = region = profile = ""
        with open(filename) as f:
            for line in f:
                line = line.strip()
                if line.startswith("bucket="):
                    bucket = line[len("bucket=") :]
                    if bucket.startswith("s3://"):
                        bucket = bucket[5:]
                elif line.startswith("AWS_ACCESS_KEY_ID="):
                    access_key = line[len("AWS_ACCESS_KEY_ID=") :]
                elif line.startswith("AWS_SECRET_ACCESS_KEY="):
                    secret_key = line[len("AWS_SECRET_ACCESS_KEY=") :]
                elif line.startswith("AWS_REGION="):
                    region = line[len("AWS_REGION=") :]
                elif line.startswith("AWS_PROFILE="):
                    profile = line[len("AWS_PROFILE=") :]
        if not bucket:
            raise OSError(f"no bucket specified in {filename}")
        if not region:
            region = "us-east-1"
        import boto3

        session_kwargs: dict = {}
        if profile:
            session_kwargs["profile_name"] = profile
        session = boto3.Session(**session_kwargs)
        kwargs: dict = {"region_name": region}
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key
        client = session.client("s3", **kwargs)
        fs = S3FS()
        fs.client = client
        fs.bucket = bucket
        return fs

    def read_dir(self, path: str) -> list[VFile]:
        if not self.client:
            raise OSError("S3 not connected")
        prefix = path
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        paginator = self.client.get_paginator("list_objects_v2")
        files: list[VFile] = []
        for page in paginator.paginate(
            Bucket=self.bucket,
            Prefix=prefix,
            Delimiter="/",
        ):
            for cp in page.get("CommonPrefixes", []):
                name = cp["Prefix"][len(prefix) :]
                name = name.rstrip("/")
                if name:
                    files.append(VFile(name=name, file_type=FILE_TYPE_DIR))
            for obj in page.get("Contents", []):
                name = obj["Key"][len(prefix) :]
                if not name:
                    continue
                mt = obj.get("LastModified")
                ts = mt.timestamp() if mt else 0.0
                files.append(
                    VFile(name=name, size=obj.get("Size", 0), mod_time=ts)
                )
        return sort_files(files)

    def read_file(self, path: str) -> io.IOBase:
        if not self.client:
            raise OSError("S3 not connected")
        resp = self.client.get_object(Bucket=self.bucket, Key=path)
        return resp["Body"]

    def write_file(self, path: str, data: io.IOBase) -> None:
        if not self.client:
            raise OSError("S3 not connected")
        body = data.read()
        self.client.put_object(Bucket=self.bucket, Key=path, Body=body)

    def mkdir_all(self, path: str) -> None:
        pass  # implicit in S3

    def leave(self) -> None:
        self.client = None


class GCSFS(VFS):
    label = "GCS"

    def __init__(self) -> None:
        self.client = None
        self.bucket_name = ""

    def probe(self, header: bytes, filename: str) -> bool:
        if not filename.lower().endswith(".gcs"):
            return False
        return header.startswith(b"type=gcs")

    def enter(self, header: bytes, filename: str, cwd: str = "") -> VFS:
        bucket_name = key = ""
        with open(filename) as f:
            for line in f:
                line = line.strip()
                if line.startswith("bucket="):
                    bucket_name = line[len("bucket=") :]
                    if bucket_name.startswith("gs://"):
                        bucket_name = bucket_name[5:]
                elif line.startswith("key="):
                    key = line[len("key=") :]
        if not bucket_name:
            raise OSError(f"no bucket specified in {filename}")
        if key:
            key = _expand_home(key)
            if not os.path.isabs(key):
                base = cwd if cwd else os.path.dirname(filename)
                key = os.path.join(base, key)
        from google.cloud import storage as gcs_storage

        kwargs: dict = {}
        if key:
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_file(key)
            kwargs["credentials"] = creds
        client = gcs_storage.Client(**kwargs)
        fs = GCSFS()
        fs.client = client
        fs.bucket_name = bucket_name
        return fs

    def read_dir(self, path: str) -> list[VFile]:
        if not self.client:
            raise OSError("GCS not connected")
        prefix = path
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        bucket = self.client.bucket(self.bucket_name)
        blobs = bucket.list_blobs(prefix=prefix, delimiter="/")
        files: list[VFile] = []
        for blob in blobs:
            name = blob.name[len(prefix) :]
            if not name:
                continue
            ts = blob.updated.timestamp() if blob.updated else 0.0
            files.append(VFile(name=name, size=blob.size or 0, mod_time=ts))
        for pfx in blobs.prefixes:
            name = pfx[len(prefix) :].rstrip("/")
            if name:
                files.append(VFile(name=name, file_type=FILE_TYPE_DIR))
        return sort_files(files)

    def read_file(self, path: str) -> io.IOBase:
        if not self.client:
            raise OSError("GCS not connected")
        bucket = self.client.bucket(self.bucket_name)
        blob = bucket.blob(path)
        buf = io.BytesIO()
        blob.download_to_file(buf)
        buf.seek(0)
        return buf

    def write_file(self, path: str, data: io.IOBase) -> None:
        if not self.client:
            raise OSError("GCS not connected")
        bucket = self.client.bucket(self.bucket_name)
        blob = bucket.blob(path)
        blob.upload_from_file(data)

    def mkdir_all(self, path: str) -> None:
        pass  # implicit in GCS

    def leave(self) -> None:
        if self.client:
            self.client.close()
            self.client = None


class OCIFS(VFS):
    label = "OCI"

    def __init__(self) -> None:
        self.client = None
        self.bucket = ""
        self.namespace = ""

    def probe(self, header: bytes, filename: str) -> bool:
        if not filename.lower().endswith(".oci"):
            return False
        return header.startswith(b"type=oci")

    def enter(self, header: bytes, filename: str, cwd: str = "") -> VFS:
        bucket = namespace = user = fingerprint = tenancy = region = ""
        key_base64 = key_file = ""
        with open(filename) as f:
            for line in f:
                line = line.strip()
                if line.startswith("bucket="):
                    bucket = line[len("bucket=") :]
                elif line.startswith("OCI_BUCKET_NAMESPACE="):
                    namespace = line[len("OCI_BUCKET_NAMESPACE=") :]
                elif line.startswith("OCI_USER="):
                    user = line[len("OCI_USER=") :]
                elif line.startswith("OCI_FINGERPRINT="):
                    fingerprint = line[len("OCI_FINGERPRINT=") :]
                elif line.startswith("OCI_TENANCY="):
                    tenancy = line[len("OCI_TENANCY=") :]
                elif line.startswith("OCI_REGION="):
                    region = line[len("OCI_REGION=") :]
                elif line.startswith("OCI_KEY_BASE64="):
                    key_base64 = line[len("OCI_KEY_BASE64=") :]
                elif line.startswith("OCI_KEY_FILE="):
                    key_file = line[len("OCI_KEY_FILE=") :]
        if not bucket:
            raise OSError(f"no bucket specified in {filename}")
        if not namespace:
            raise OSError(f"no namespace specified in {filename}")
        import oci

        if key_base64:
            import base64

            key_content = base64.b64decode(key_base64).decode()
            config = {
                "user": user,
                "fingerprint": fingerprint,
                "tenancy": tenancy,
                "region": region,
                "key_content": key_content,
            }
        elif key_file:
            key_file = _expand_home(key_file)
            if not os.path.isabs(key_file):
                base = cwd if cwd else os.path.dirname(filename)
                key_file = os.path.join(base, key_file)
            config = {
                "user": user,
                "fingerprint": fingerprint,
                "tenancy": tenancy,
                "region": region,
                "key_file": key_file,
            }
        else:
            config = oci.config.from_file()
        client = oci.object_storage.ObjectStorageClient(config)
        fs = OCIFS()
        fs.client = client
        fs.bucket = bucket
        fs.namespace = namespace
        return fs

    def read_dir(self, path: str) -> list[VFile]:
        if not self.client:
            raise OSError("OCI not connected")
        prefix = path
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        files: list[VFile] = []
        next_start = None
        while True:
            kwargs: dict = {
                "namespace_name": self.namespace,
                "bucket_name": self.bucket,
                "prefix": prefix,
                "delimiter": "/",
                "fields": "name,size,timeModified",
            }
            if next_start:
                kwargs["start"] = next_start
            resp = self.client.list_objects(**kwargs)
            data = resp.data
            for pfx in data.prefixes or []:
                name = pfx[len(prefix) :].rstrip("/")
                if name:
                    files.append(VFile(name=name, file_type=FILE_TYPE_DIR))
            for obj in data.objects or []:
                name = obj.name[len(prefix) :]
                if not name:
                    continue
                ts = obj.time_modified.timestamp() if obj.time_modified else 0.0
                files.append(VFile(name=name, size=obj.size or 0, mod_time=ts))
            if data.next_start_with:
                next_start = data.next_start_with
            else:
                break
        return sort_files(files)

    def read_file(self, path: str) -> io.IOBase:
        if not self.client:
            raise OSError("OCI not connected")
        resp = self.client.get_object(self.namespace, self.bucket, path)
        buf = io.BytesIO(resp.data.content)
        return buf

    def write_file(self, path: str, data: io.IOBase) -> None:
        if not self.client:
            raise OSError("OCI not connected")
        body = data.read()
        self.client.put_object(self.namespace, self.bucket, path, body)

    def mkdir_all(self, path: str) -> None:
        pass  # implicit in OCI Object Storage

    def leave(self) -> None:
        self.client = None


class GDriveFS(VFS):
    label = "GDrive"

    def __init__(self) -> None:
        self.service = None
        self.root_folder_id = ""
        self._id_cache: dict[str, str] = {}

    def probe(self, header: bytes, filename: str) -> bool:
        if not filename.lower().endswith(".gdrive"):
            return False
        return header.startswith(b"type=gdrive")

    def enter(self, header: bytes, filename: str, cwd: str = "") -> VFS:
        folder_id = key = ""
        with open(filename) as f:
            for line in f:
                line = line.strip()
                if line.startswith("folder="):
                    folder_id = line[len("folder=") :]
                elif line.startswith("key="):
                    key = line[len("key=") :]
        if not folder_id:
            raise OSError(f"no folder specified in {filename}")
        if key:
            key = _expand_home(key)
            if not os.path.isabs(key):
                base = cwd if cwd else os.path.dirname(filename)
                key = os.path.join(base, key)
        from googleapiclient.discovery import build

        scopes = ["https://www.googleapis.com/auth/drive"]
        if key:
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_file(
                key, scopes=scopes
            )
        else:
            import google.auth

            creds, _ = google.auth.default(scopes=scopes)
        service = build("drive", "v3", credentials=creds)
        fs = GDriveFS()
        fs.service = service
        fs.root_folder_id = folder_id
        fs._id_cache = {"": folder_id}
        return fs

    def _resolve_id(self, path: str) -> str:
        if path in self._id_cache:
            return self._id_cache[path]
        parts = path.strip("/").split("/")
        current_path = ""
        parent_id = self.root_folder_id
        for part in parts:
            current_path = f"{current_path}/{part}" if current_path else part
            if current_path in self._id_cache:
                parent_id = self._id_cache[current_path]
                continue
            escaped = part.replace("\\", "\\\\").replace("'", "\\'")
            q = (
                f"'{parent_id}' in parents"
                f" and name = '{escaped}'"
                f" and trashed = false"
            )
            resp = (
                self.service.files()
                .list(
                    q=q,
                    fields="files(id)",
                    pageSize=1,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            files = resp.get("files", [])
            if not files:
                raise OSError(f"not found: {current_path}")
            parent_id = files[0]["id"]
            self._id_cache[current_path] = parent_id
        return parent_id

    def read_dir(self, path: str) -> list[VFile]:
        if not self.service:
            raise OSError("GDrive not connected")
        folder_id = self._resolve_id(path)
        q = f"'{folder_id}' in parents and trashed = false"
        files: list[VFile] = []
        page_token = None
        while True:
            resp = (
                self.service.files()
                .list(
                    q=q,
                    fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
                    pageSize=1000,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for item in resp.get("files", []):
                name = item["name"]
                child_path = f"{path}/{name}" if path else name
                self._id_cache[child_path] = item["id"]
                if item["mimeType"] == "application/vnd.google-apps.folder":
                    files.append(VFile(name=name, file_type=FILE_TYPE_DIR))
                else:
                    size = int(item.get("size", 0))
                    ts = 0.0
                    mt = item.get("modifiedTime")
                    if mt:
                        from datetime import datetime

                        ts = datetime.fromisoformat(
                            mt.replace("Z", "+00:00")
                        ).timestamp()
                    files.append(VFile(name=name, size=size, mod_time=ts))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return sort_files(files)

    def read_file(self, path: str) -> io.IOBase:
        if not self.service:
            raise OSError("GDrive not connected")
        file_id = self._resolve_id(path)
        from googleapiclient.http import MediaIoBaseDownload

        request = self.service.files().get_media(
            fileId=file_id, supportsAllDrives=True
        )
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        return buf

    def write_file(self, path: str, data: io.IOBase) -> None:
        if not self.service:
            raise OSError("GDrive not connected")
        parts = path.rsplit("/", 1)
        if len(parts) == 2:
            parent_path, name = parts
        else:
            parent_path, name = "", parts[0]
        parent_id = self._resolve_id(parent_path)
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        q = (
            f"'{parent_id}' in parents"
            f" and name = '{escaped}'"
            f" and trashed = false"
        )
        resp = (
            self.service.files()
            .list(
                q=q,
                fields="files(id)",
                pageSize=1,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        existing = resp.get("files", [])
        from googleapiclient.http import MediaIoBaseUpload

        media = MediaIoBaseUpload(
            data, mimetype="application/octet-stream", resumable=True
        )
        if existing:
            self.service.files().update(
                fileId=existing[0]["id"],
                media_body=media,
                supportsAllDrives=True,
            ).execute()
        else:
            metadata = {"name": name, "parents": [parent_id]}
            self.service.files().create(
                body=metadata,
                media_body=media,
                supportsAllDrives=True,
            ).execute()

    def mkdir_all(self, path: str) -> None:
        if not self.service:
            raise OSError("GDrive not connected")
        parts = path.strip("/").split("/")
        current_path = ""
        parent_id = self.root_folder_id
        for part in parts:
            current_path = f"{current_path}/{part}" if current_path else part
            if current_path in self._id_cache:
                parent_id = self._id_cache[current_path]
                continue
            escaped = part.replace("\\", "\\\\").replace("'", "\\'")
            q = (
                f"'{parent_id}' in parents"
                f" and name = '{escaped}'"
                f" and mimeType = 'application/vnd.google-apps.folder'"
                f" and trashed = false"
            )
            resp = (
                self.service.files()
                .list(
                    q=q,
                    fields="files(id)",
                    pageSize=1,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            existing = resp.get("files", [])
            if existing:
                parent_id = existing[0]["id"]
            else:
                metadata = {
                    "name": part,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id],
                }
                created = (
                    self.service.files()
                    .create(
                        body=metadata,
                        fields="id",
                        supportsAllDrives=True,
                    )
                    .execute()
                )
                parent_id = created["id"]
            self._id_cache[current_path] = parent_id

    def leave(self) -> None:
        self.service = None
        self._id_cache.clear()


def _parse_ls_line(line: str) -> VFile | None:
    """Parse one line of ``ls -la`` output into a VFile."""
    # Expected format:
    #   perms links user group size month day time_or_year name
    # e.g. -rw-r--r--  1 user group  1234 Mar 12 10:00 file.txt
    #      lrwxrwxrwx  1 user group    12 Mar 12 10:00 link -> target
    parts = line.split(None, 8)
    if len(parts) < 9:
        return None

    perms = parts[0]
    try:
        size = int(parts[4])
    except ValueError:
        size = 0
    name_field = parts[8]

    file_type = FILE_TYPE_FILE
    link_target = ""
    executable = False

    if perms.startswith("d"):
        file_type = FILE_TYPE_DIR
    elif perms.startswith("l"):
        file_type = FILE_TYPE_SYMLINK
        if " -> " in name_field:
            name_field, link_target = name_field.split(" -> ", 1)

    if file_type == FILE_TYPE_FILE and "x" in perms[1:]:
        executable = True

    months = {
        "Jan": 1,
        "Feb": 2,
        "Mar": 3,
        "Apr": 4,
        "May": 5,
        "Jun": 6,
        "Jul": 7,
        "Aug": 8,
        "Sep": 9,
        "Oct": 10,
        "Nov": 11,
        "Dec": 12,
    }
    try:
        month = months.get(parts[5], 1)
        day = int(parts[6])
        if ":" in parts[7]:
            hour, minute = parts[7].split(":")
            year = datetime.now().year
            dt = datetime(year, month, day, int(hour), int(minute))
        else:
            year = int(parts[7])
            dt = datetime(year, month, day)
        mod_time = dt.timestamp()
    except (ValueError, KeyError):
        mod_time = 0.0

    return VFile(
        name=name_field,
        size=size,
        file_type=file_type,
        mod_time=mod_time,
        executable=executable,
        link_target=link_target,
    )


class SSHFS(VFS):
    """SSH virtual filesystem using the ``ssh`` executable."""

    label = "SSH"

    def __init__(self) -> None:
        self.host = ""
        self.user = ""
        self.port = ""
        self.identity = ""
        self._control_path = ""

    def _ssh_args(self) -> list[str]:
        args = ["ssh"]
        if self.port:
            args += ["-p", self.port]
        if self.identity:
            args += ["-i", self.identity]
        args += [
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ControlMaster=auto",
            "-o",
            f"ControlPath={self._control_path}",
            "-o",
            "ControlPersist=60",
        ]
        target = f"{self.user}@{self.host}" if self.user else self.host
        args.append(target)
        return args

    def _run(self, cmd: str, *, timeout: int = 30) -> str:
        args = self._ssh_args() + [cmd]
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout
        )
        if r.returncode != 0:
            raise OSError(r.stderr.strip() or f"ssh command failed: {cmd}")
        return r.stdout

    def _run_bytes(
        self,
        cmd: str,
        *,
        timeout: int = 60,
        stdin: bytes | None = None,
    ) -> bytes:
        args = self._ssh_args() + [cmd]
        r = subprocess.run(
            args,
            capture_output=True,
            input=stdin,
            timeout=timeout,
        )
        if r.returncode != 0:
            raise OSError(r.stderr.decode(errors="replace").strip())
        return r.stdout

    def probe(self, header: bytes, filename: str) -> bool:
        if not filename.lower().endswith(".ssh"):
            return False
        text = header.decode(errors="ignore")
        return "kind=ssh" in text or "host=" in text

    def enter(self, header: bytes, filename: str, cwd: str = "") -> VFS:
        host = user = port = identity = ""
        with open(filename) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                else:
                    key, _, val = line.partition(" ")
                key = key.strip().lower()
                val = val.strip()
                if key == "host":
                    host = val
                elif key == "user":
                    user = val
                elif key == "port":
                    port = val
                elif key == "identity":
                    identity = val
        if not host:
            raise OSError(f"no host specified in {filename}")

        fs = SSHFS()
        fs.host = host
        fs.user = user
        fs.port = port
        if identity:
            fs.identity = _expand_home(identity)
        fs._control_path = os.path.join(
            tempfile.gettempdir(),
            f"xc-ssh-{user or 'default'}-{host}-{port or '22'}",
        )

        # verify connectivity
        try:
            fs._run("echo ok")
        except Exception as e:
            raise OSError(f"SSH connection to {host} failed: {e}") from e
        return fs

    def read_dir(self, path: str) -> list[VFile]:
        remote = path if path else "."
        q = remote.replace("'", "'\\''")
        output = self._run(f"LANG=C ls -la '{q}'")
        files: list[VFile] = []
        for line in output.splitlines():
            if line.startswith("total ") or not line.strip():
                continue
            vf = _parse_ls_line(line)
            if vf and vf.name not in (".", ".."):
                files.append(vf)
        return sort_files(files)

    def read_file(self, path: str) -> io.IOBase:
        q = path.replace("'", "'\\''")
        data = self._run_bytes(f"cat '{q}'")
        return io.BytesIO(data)

    def write_file(self, path: str, data: io.IOBase) -> None:
        content = data.read()
        if isinstance(content, str):
            content = content.encode()
        q = path.replace("'", "'\\''")
        self._run_bytes(f"cat > '{q}'", stdin=content)

    def mkdir_all(self, path: str) -> None:
        q = path.replace("'", "'\\''")
        self._run(f"mkdir -p '{q}'")

    def leave(self) -> None:
        if self._control_path:
            try:
                target = f"{self.user}@{self.host}" if self.user else self.host
                subprocess.run(
                    [
                        "ssh",
                        "-O",
                        "exit",
                        "-o",
                        f"ControlPath={self._control_path}",
                        target,
                    ],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass
        self.host = ""


class GrepFS(VFS):
    label = "GREP"

    def __init__(self) -> None:
        self.results: list[VFile] = []
        self.base_dir: str = ""

    def probe(self, header: bytes, filename: str) -> bool:
        return False

    def enter(self, header: bytes, filename: str, cwd: str = "") -> VFS:
        raise OSError("GrepFS cannot be entered via probe")

    def add_path(self, rel_path: str) -> None:
        full = os.path.join(self.base_dir, rel_path)
        try:
            st = os.stat(full)
            ft = FILE_TYPE_DIR if stat.S_ISDIR(st.st_mode) else FILE_TYPE_FILE
            vf = VFile(
                name=rel_path,
                size=st.st_size,
                file_type=ft,
                mod_time=st.st_mtime,
                executable=ft == FILE_TYPE_FILE and bool(st.st_mode & 0o111),
            )
        except OSError:
            vf = VFile(name=rel_path)
        self.results.append(vf)

    def read_dir(self, path: str) -> list[VFile]:
        return list(self.results)

    def read_file(self, path: str) -> io.IOBase:
        full = os.path.join(self.base_dir, path)
        return open(full, "rb")

    def write_file(self, path: str, data: io.IOBase) -> None:
        raise OSError("GrepFS is read-only")

    def mkdir_all(self, path: str) -> None:
        raise OSError("GrepFS is read-only")

    def leave(self) -> None:
        self.results = []
        self.base_dir = ""


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------


@dataclass
class VFSEntry:
    fs: VFS
    path: str
    cursor: int
    offset: int
    entry_path: str


class Panel:
    def __init__(
        self,
        path: str,
        fs: VFS,
        probes: list[VFS],
        on_error: Callable[[str], None],
        on_exec: Callable[[str], None],
        on_run: Callable[[str, bool], None] | None = None,
    ):
        self.path = path
        self.fs = fs
        self.probes = probes
        self.on_error = on_error
        self.on_exec = on_exec
        self.on_run = on_run
        self.files: list[VFile] = []
        self.cursor = 0
        self.offset = 0
        self.stack: list[VFSEntry] = []
        self.tagged: dict[str, bool] = {}
        self.dir_sizes: dict[str, int] = {}
        self.load_dir()

    def report_error(self, err: Exception) -> None:
        log.error("panel error path=%s err=%s", self.path, err)
        if self.on_error:
            self.on_error(str(err))

    def load_dir(self) -> None:
        self.tagged = {}
        self.dir_sizes = {}
        try:
            files = self.fs.read_dir(self.path)
        except Exception as e:
            self.report_error(e)
            self.files = []
            return
        show_dotdot = True
        if isinstance(self.fs, LocalFS):
            parent = os.path.dirname(self.path)
            if parent == self.path:
                show_dotdot = False
        if show_dotdot:
            self.files = [VFile(name="..", file_type=FILE_TYPE_DIR)] + files
        else:
            self.files = files

    def enter(self) -> None:
        if self.cursor >= len(self.files):
            return
        f = self.files[self.cursor]
        if f.name == "..":
            self.go_up()
            return
        if isinstance(self.fs, GrepFS):
            rel_path = f.name
            base_dir = self.fs.base_dir
            self.fs.leave()
            prev = self.stack.pop()
            self.fs = prev.fs
            target_dir = os.path.join(base_dir, os.path.dirname(rel_path))
            target_name = os.path.basename(rel_path)
            self.path = target_dir
            self.cursor = 0
            self.offset = 0
            self.load_dir()
            for i, ff in enumerate(self.files):
                if ff.name == target_name:
                    self.cursor = i
                    break
            return
        if f.is_dir():
            self.path = os.path.join(self.path, f.name)
            self.cursor = 0
            self.offset = 0
            self.load_dir()
            return
        if f.is_symlink():
            dp = self.disk_path(f.name)
            if dp:
                try:
                    resolved = os.path.realpath(dp)
                    if os.path.isdir(resolved):
                        self.path = resolved
                        self.cursor = 0
                        self.offset = 0
                        self.load_dir()
                        return
                except OSError:
                    pass
        if f.is_executable() and self.on_exec:
            dp = self.disk_path(f.name)
            if dp:
                self.on_exec(shell_quote(dp))
                return
        full_path = self.disk_path(f.name)
        if not full_path:
            return
        header = read_header(full_path, 32)
        for probe in self.probes:
            if not probe.probe(header, f.name):
                continue
            try:
                new_fs = probe.enter(header, full_path, cwd=self.path)
            except Exception as e:
                self.report_error(e)
                return
            self.stack.append(
                VFSEntry(
                    fs=self.fs,
                    path=self.path,
                    cursor=self.cursor,
                    offset=self.offset,
                    entry_path=full_path,
                )
            )
            self.fs = new_fs
            self.path = ""
            self.cursor = 0
            self.offset = 0
            self.load_dir()
            return
        ext = f.ext().lower()
        if ext and ext in ASSOCIATIONS and self.on_run:
            assoc = ASSOCIATIONS[ext]
            self.on_run(assoc.cmd, assoc.fire_and_forget)

    def enter_remote(self, path: str) -> None:
        header = read_header(path, 32)
        for probe in self.probes:
            if not probe.probe(header, os.path.basename(path)):
                continue
            try:
                new_fs = probe.enter(header, path, cwd=self.path)
            except Exception as e:
                self.report_error(e)
                return
            self.stack.append(
                VFSEntry(
                    fs=self.fs,
                    path=self.path,
                    cursor=self.cursor,
                    offset=self.offset,
                    entry_path=path,
                )
            )
            self.fs = new_fs
            self.path = ""
            self.cursor = 0
            self.offset = 0
            self.load_dir()
            return

    def go_up(self) -> None:
        at_root = not self.path or os.path.dirname(self.path) == self.path
        if at_root and self.stack:
            self.fs.leave()
            prev = self.stack.pop()
            self.fs = prev.fs
            self.path = prev.path
            self.cursor = prev.cursor
            self.offset = prev.offset
            self.load_dir()
            return
        if at_root:
            return
        old_dir = os.path.basename(self.path)
        parent = os.path.dirname(self.path)
        if parent == ".":
            parent = ""
        self.path = parent
        self.load_dir()
        self.cursor = 0
        self.offset = 0
        for i, f in enumerate(self.files):
            if f.name == old_dir:
                self.cursor = i
                break

    def move_to(self, idx: int) -> None:
        if not self.files:
            self.cursor = 0
            return
        self.cursor = max(0, min(idx, len(self.files) - 1))

    def adjust_offset(self, visible: int) -> None:
        if visible <= 0:
            return
        if self.cursor < self.offset:
            self.offset = self.cursor
        if self.cursor >= self.offset + visible:
            self.offset = self.cursor - visible + 1

    def disk_path(self, name: str) -> str:
        if isinstance(self.fs, LocalFS):
            return os.path.join(self.path, name)
        return ""

    def vfs_path(self, name: str) -> str:
        if self.path:
            return self.path + "/" + name
        return name

    def display_path(self) -> str:
        if not self.stack:
            return self.path
        base = self.stack[-1].entry_path
        if self.path:
            base = base + "/" + self.path
        if self.fs.label:
            base = base + " (" + self.fs.label + ")"
        return base

    def selected_file(self) -> VFile | None:
        if self.cursor < len(self.files):
            return self.files[self.cursor]
        return None

    def reload(self) -> None:
        cur, off = self.cursor, self.offset
        self.load_dir()
        self.move_to(cur)
        self.offset = off

    def scroll(self, delta: int) -> None:
        self.offset += delta
        mx = len(self.files) - 1
        self.offset = max(0, min(self.offset, mx))
        self.move_to(self.cursor + delta)


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------


@dataclass
class MenuItem:
    key: str
    label: str
    action: Callable[[], None]


@dataclass
class Menu:
    name: str
    items: list[MenuItem] = field(default_factory=list)


@dataclass
class MenuState:
    name: str
    cursor: int
    offset: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read_header(path: str, n: int) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def quote_if_needed(s: str) -> str:
    if " " in s:
        return '"' + s + '"'
    return s


def shorten_home(path: str) -> str:
    home = str(Path.home())
    if home and path.startswith(home):
        return "~" + path[len(home) :]
    return path


def expand_home(path: str) -> str:
    if path.startswith("~/") or path == "~":
        return str(Path.home()) + path[1:]
    return path


INTERACTIVE_CMDS = {
    "vi",
    "vim",
    "nano",
    "cot",
    "less",
    "more",
    "open",
    "mcedit",
}


def is_interactive_cmd(cmd: str) -> bool:
    parts = cmd.split()
    if not parts:
        return False
    return os.path.basename(parts[0]) in INTERACTIVE_CMDS


def calc_dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirs, filenames in os.walk(path):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


def vfs_join(fs: VFS, base: str, name: str) -> str:
    if isinstance(fs, LocalFS):
        return os.path.join(base, name)
    if not base:
        return name
    return base + "/" + name


# ---------------------------------------------------------------------------
# Color pairs
# ---------------------------------------------------------------------------

CP_DEF = 1
CP_CURSOR = 2
CP_TAGGED = 3
CP_BORDER = 4
CP_STATUS = 5
CP_CMDLINE = 6
CP_ERR = 7
CP_MENU = 8
CP_MENUSEL = 9
CP_DIR = 10
CP_DIM = 11

DIMMED_NAMES = {"node_modules", "__pycache__"}


def is_dimmed(name: str) -> bool:
    return name.startswith(".") or name in DIMMED_NAMES


def init_colors() -> None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(CP_DEF, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(CP_CURSOR, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(CP_TAGGED, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(CP_BORDER, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(CP_STATUS, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(CP_CMDLINE, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(CP_ERR, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(CP_MENU, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(CP_MENUSEL, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(CP_DIR, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(CP_DIM, curses.COLOR_WHITE, curses.COLOR_BLACK)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class App:
    def __init__(
        self,
        stdscr: curses.window,
        left_dir: str,
        right_dir: str,
        saved: AppState | None,
    ) -> None:
        self.scr = stdscr
        self.panels: list[Panel] = []
        self.active = 0
        self.cmd_mode = 0  # 0=off, 1=direct(;), 2=piped(:)
        self.cmd_line: list[str] = []
        self.cmd_cursor = 0
        self.cmd_history: list[str] = []
        self.cmd_hist_mode = False
        self.cmd_hist_idx = -1
        self.cmd_hist_offset = 0
        self.cmd_line_saved: list[str] = []
        self.search_mode = False
        self.search_query: list[str] = []
        self.grep_mode = 0  # 0=off, 1=file pattern, 2=search string
        self.grep_sensitive = True
        self.grep_with_grep = False
        self.grep_edit: list[str] = []
        self.grep_cursor = 0
        self.grep_file_pattern = ""
        self.copy_mode = 0  # 0=off, 1=src, 2=dst
        self.copy_is_move = False
        self.copy_from = ""
        self.copy_edit: list[str] = []
        self.copy_cursor = 0
        self.copy_history: list[list[str]] = [[], []]
        self.copy_hist_idx = -1
        self.copy_edit_saved: list[str] = []
        self.menus: dict[str, Menu] = {}
        self.menu_active = ""
        self.menu_cursor = 0
        self.menu_offset = 0
        self.menu_stack: list[MenuState] = []
        self.keymaps: dict[str, Callable[[], None]] = {}
        self.prompt_mode = False
        self.prompt_label = ""
        self.prompt_edit: list[str] = []
        self.prompt_cursor = 0
        self.prompt_action: Callable[[str], None] | None = None
        self.err_msg = ""
        self.ctrl_c_pending = False
        self.help_mode = False
        self.cursor_pos: tuple[int, int] | None = None  # (y, x) for cursor

        local_fs = LocalFS()
        probes: list[VFS] = [
            TarFS(),
            ZipFS(),
            CompressedFS(),
            S3FS(),
            GCSFS(),
            OCIFS(),
            GDriveFS(),
            SSHFS(),
        ]

        self.panels = [
            Panel(
                left_dir,
                local_fs,
                probes,
                self.set_error,
                lambda cmd: self.run_shell_cmd(cmd, False),
                self.run_assoc,
            ),
            Panel(
                right_dir,
                local_fs,
                probes,
                self.set_error,
                lambda cmd: self.run_shell_cmd(cmd, False),
                self.run_assoc,
            ),
        ]

        if saved:
            self.copy_history = saved.copy_history
            self.cmd_history = saved.cmd_history
            if saved.active in (0, 1):
                self.active = saved.active

    # -- Menus --

    def add_menu(self, name: str, items: list[MenuItem]) -> None:
        self.menus[name] = Menu(name=name, items=items)

    def add_keymap(self, key: str, action: Callable[[], None]) -> None:
        self.keymaps[key] = action

    def menu_selector(self, name: str) -> None:
        if self.menu_active:
            self.menu_stack.append(
                MenuState(
                    name=self.menu_active,
                    cursor=self.menu_cursor,
                    offset=self.menu_offset,
                )
            )
        self.menu_active = name
        self.menu_cursor = 0
        self.menu_offset = 0

    def menu(self, name: str) -> None:
        self.menu_selector(name)

    def pop_menu(self) -> None:
        if self.menu_stack:
            prev = self.menu_stack.pop()
            self.menu_active = prev.name
            self.menu_cursor = prev.cursor
            self.menu_offset = prev.offset
        else:
            self.menu_active = ""

    def exec_menu_item(self, item: MenuItem) -> None:
        self.menu_active = ""
        self.menu_stack = []
        item.action()

    # -- Prompt --

    def show_prompt(
        self,
        label: str,
        initial: str,
        action: Callable[[str], None],
    ) -> None:
        self.prompt_mode = True
        self.prompt_label = label
        self.prompt_edit = list(initial)
        self.prompt_cursor = len(self.prompt_edit)
        self.prompt_action = action

    # -- Error --

    def set_error(self, msg: str) -> None:
        self.err_msg = msg

    # -- State --

    def do_save_state(self) -> None:
        st = AppState(
            panels=[self.panels[0].path, self.panels[1].path],
            active=self.active,
            copy_history=self.copy_history,
            cmd_history=self.cmd_history,
        )
        save_state(st)

    # -- Macro expansion --

    def expand_macro(self, cmd: str) -> tuple[str, bool]:
        p = self.panels[self.active]
        f = p.selected_file()
        background = False
        result: list[str] = []
        runes = list(cmd)
        i = 0
        while i < len(runes):
            if runes[i] != "%" or i + 1 >= len(runes):
                result.append(runes[i])
                i += 1
                continue
            i += 1
            no_quote = False
            if runes[i] == "~" and i + 1 < len(runes):
                no_quote = True
                i += 1
            ch = runes[i]
            i += 1
            val = ""
            if ch == "&":
                background = True
                continue
            elif ch == "f":
                val = f.name if f else ""
            elif ch == "F":
                val = p.disk_path(f.name) if f else ""
            elif ch == "x":
                val = f.base_name() if f else ""
            elif ch == "X":
                if f:
                    dp = p.disk_path(f.name)
                    if dp:
                        base, _ = os.path.splitext(dp)
                        val = base
            elif ch == "m":
                names = [ff.name for ff in p.files if p.tagged.get(ff.name)]
                if no_quote:
                    val = " ".join(names)
                else:
                    val = " ".join(shell_quote(n) for n in names)
                result.append(val)
                continue
            elif ch == "M":
                paths = [
                    p.disk_path(ff.name)
                    for ff in p.files
                    if p.tagged.get(ff.name) and p.disk_path(ff.name)
                ]
                if no_quote:
                    val = " ".join(paths)
                else:
                    val = " ".join(shell_quote(pp) for pp in paths)
                result.append(val)
                continue
            elif ch == "d":
                val = os.path.basename(p.path)
            elif ch == "D":
                val = p.path
            else:
                result.append("%")
                result.append(ch)
                continue
            if no_quote:
                result.append(val)
            else:
                result.append(shell_quote(val))
        return "".join(result), background

    # -- Grep --

    def start_grep(self, with_grep: bool) -> None:
        p = self.panels[self.active]
        if not isinstance(p.fs, LocalFS):
            self.set_error("search works only on local filesystem")
            return
        self.grep_sensitive = True
        self.grep_with_grep = with_grep
        self.grep_mode = 1
        self.grep_edit = list("*.*")
        self.grep_cursor = len(self.grep_edit)
        self.grep_file_pattern = ""

    def spinner_check_esc(self, frame: int) -> bool:
        """Update spinner on cmd line row. Returns True if ESC pressed."""
        spinner = r"|/-\\"
        h, w = self.scr.getmaxyx()
        y = h - 2
        attr = curses.color_pair(CP_CMDLINE)
        ch = spinner[frame % len(spinner)]
        self.draw_string(0, y, f" searching {ch} (ESC to stop)", w, attr)
        self.scr.refresh()
        try:
            key = self.scr.get_wch()
            if isinstance(key, str):
                key = ord(key)
            if key == 27:
                return True
        except curses.error:
            pass
        return False

    def exec_grep(self) -> None:
        search_str = "".join(self.grep_edit).strip()
        file_pat = self.grep_file_pattern
        p = self.panels[self.active]
        base_dir = p.path
        self.grep_mode = 0

        gfs = GrepFS()
        gfs.base_dir = base_dir
        p.stack.append(
            VFSEntry(
                fs=p.fs,
                path=p.path,
                cursor=p.cursor,
                offset=p.offset,
                entry_path=base_dir,
            )
        )
        p.fs = gfs
        p.path = ""
        p.cursor = 0
        p.offset = 0
        p.tagged = {}
        p.files = [VFile(name="..", file_type=FILE_TYPE_DIR)]

        frame = 0
        last_spin = 0.0
        cancelled = False
        self.scr.nodelay(True)
        try:
            for dirpath, dirnames, filenames in os.walk(base_dir):
                # Skip hidden directories
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                now = time.monotonic()
                if now - last_spin >= 0.1:
                    p.files = [
                        VFile(name="..", file_type=FILE_TYPE_DIR)
                    ] + gfs.results
                    self.draw()
                    if self.spinner_check_esc(frame):
                        cancelled = True
                        break
                    frame += 1
                    last_spin = now
                for name in filenames:
                    if file_pat and not fnmatch.fnmatch(name, file_pat):
                        continue
                    full = os.path.join(dirpath, name)
                    rel = os.path.relpath(full, base_dir)
                    if not search_str:
                        gfs.add_path(rel)
                        continue
                    try:
                        with open(full, "rb") as f:
                            chunk = f.read(8192)
                            if b"\x00" in chunk:
                                continue  # skip binary
                            text = chunk
                            rest = f.read()
                            if rest:
                                text += rest
                        content = text.decode("utf-8", errors="ignore")
                        if self.grep_sensitive:
                            if search_str in content:
                                gfs.add_path(rel)
                        else:
                            if search_str.lower() in content.lower():
                                gfs.add_path(rel)
                    except (OSError, PermissionError):
                        continue
        except Exception as e:
            self.set_error(str(e))
        finally:
            self.scr.nodelay(False)

        p.load_dir()

        if not gfs.results:
            gfs.leave()
            prev = p.stack.pop()
            p.fs = prev.fs
            p.path = prev.path
            p.cursor = prev.cursor
            p.offset = prev.offset
            p.load_dir()
            self.set_error("no matches found")
        elif cancelled:
            self.set_error(f"search stopped, {len(gfs.results)} matches")

    # -- Actions --

    def start_copy_or_move(self, is_move: bool) -> None:
        p = self.panels[self.active]
        self.copy_is_move = is_move
        if p.tagged:
            self.copy_from = f"{len(p.tagged)} files"
            self.copy_mode = 2
            other = self.panels[1 - self.active]
            self.copy_edit = list(other.path)
            self.copy_cursor = len(self.copy_edit)
            self.copy_hist_idx = -1
        else:
            f = p.selected_file()
            if f and f.name != "..":
                self.copy_mode = 1
                self.copy_edit = list(f.name)
                self.copy_cursor = len(self.copy_edit)
                self.copy_hist_idx = -1

    def do_copy(self, src: str, dest: str) -> None:
        src_panel = self.panels[self.active]
        dst_panel = self.panels[1 - self.active]
        src_path = vfs_join(src_panel.fs, src_panel.path, src)
        if dest.endswith("/"):
            dest += os.path.basename(src)
        elif isinstance(dst_panel.fs, LocalFS) and os.path.isdir(dest):
            dest = os.path.join(dest, os.path.basename(src))
        is_dir = False
        for f in src_panel.files:
            if f.name == src:
                is_dir = f.is_dir()
                break
        if is_dir:
            self._copy_dir(src_panel.fs, src_path, dst_panel.fs, dest)
        else:
            self._copy_file(src_panel.fs, src_path, dst_panel.fs, dest)
        self.panels[0].reload()
        self.panels[1].reload()

    def _copy_file(
        self,
        src_fs: VFS,
        src_path: str,
        dst_fs: VFS,
        dst_path: str,
    ) -> None:
        log.info("copy file from=%s to=%s", src_path, dst_path)
        try:
            inp = src_fs.read_file(src_path)
        except Exception as e:
            self.set_error(str(e))
            return
        try:
            dst_fs.write_file(dst_path, inp)
        except Exception as e:
            self.set_error(str(e))
        finally:
            if hasattr(inp, "close"):
                inp.close()

    def _copy_dir(
        self,
        src_fs: VFS,
        src_path: str,
        dst_fs: VFS,
        dst_path: str,
    ) -> None:
        log.info("copy dir from=%s to=%s", src_path, dst_path)
        try:
            dst_fs.mkdir_all(dst_path)
        except Exception as e:
            self.set_error(str(e))
            return
        try:
            files = src_fs.read_dir(src_path)
        except Exception as e:
            self.set_error(str(e))
            return
        for f in files:
            child_src = vfs_join(src_fs, src_path, f.name)
            child_dst = vfs_join(dst_fs, dst_path, f.name)
            if f.is_dir():
                self._copy_dir(src_fs, child_src, dst_fs, child_dst)
            else:
                self._copy_file(src_fs, child_src, dst_fs, child_dst)

    def _copy_tagged(self, names: list[str], dest: str) -> None:
        src_panel = self.panels[self.active]
        dst_panel = self.panels[1 - self.active]
        src_fs = src_panel.fs
        # Batch optimisation for TarFS: single-pass extraction avoids
        # decompressing a large .tar.gz once per file.
        if isinstance(src_fs, TarFS) and len(names) > 1:
            file_lookup = {f.name: f for f in src_panel.files}
            file_names: list[str] = []
            dir_names: list[str] = []
            for name in names:
                fi = file_lookup.get(name)
                if fi and fi.is_dir():
                    dir_names.append(name)
                else:
                    file_names.append(name)
            # Collect all tar paths for flat files and dir descendants.
            tar_to_dest: list[tuple[str, str]] = []
            dirs_to_create: list[str] = []
            for name in file_names:
                tp = vfs_join(src_fs, src_panel.path, name)
                d = dest
                if d.endswith("/"):
                    d += name
                elif isinstance(dst_panel.fs, LocalFS) and os.path.isdir(d):
                    d = os.path.join(d, name)
                tar_to_dest.append((tp, d))
            for name in dir_names:
                tp = vfs_join(src_fs, src_panel.path, name)
                d = dest
                if d.endswith("/"):
                    d += name
                elif isinstance(dst_panel.fs, LocalFS) and os.path.isdir(d):
                    d = os.path.join(d, name)
                self._collect_tar_tree(
                    src_fs, tp, d, dst_panel.fs, tar_to_dest, dirs_to_create
                )
            for dp in dirs_to_create:
                try:
                    dst_panel.fs.mkdir_all(dp)
                except Exception as e:
                    self.set_error(str(e))
            tar_paths = {tp for tp, _ in tar_to_dest}
            extracted = src_fs.read_files(tar_paths)
            for tp, dp in tar_to_dest:
                if tp in extracted:
                    log.info("copy file from=%s to=%s", tp, dp)
                    try:
                        dst_panel.fs.write_file(dp, extracted[tp])
                    except Exception as e:
                        self.set_error(str(e))
                    dst_panel.reload()
                    self.draw()
                    self.scr.refresh()
            self.panels[0].reload()
            self.panels[1].reload()
        else:
            for name in names:
                self.do_copy(name, dest)

    def _collect_tar_tree(
        self,
        src_fs: TarFS,
        src_path: str,
        dst_path: str,
        dst_fs: VFS,
        tar_to_dest: list[tuple[str, str]],
        dirs_to_create: list[str],
    ) -> None:
        dirs_to_create.append(dst_path)
        try:
            files = src_fs.read_dir(src_path)
        except OSError:
            return
        for f in files:
            child_src = src_path + "/" + f.name
            child_dst = vfs_join(dst_fs, dst_path, f.name)
            if f.is_dir():
                self._collect_tar_tree(
                    src_fs,
                    child_src,
                    child_dst,
                    dst_fs,
                    tar_to_dest,
                    dirs_to_create,
                )
            else:
                tar_to_dest.append((child_src, child_dst))

    def do_delete(self, name: str) -> None:
        p = self.panels[self.active]
        dp = p.disk_path(name)
        if not dp:
            self.set_error("delete not supported in virtual FS")
            return
        log.info("delete path=%s", dp)
        try:
            if os.path.isdir(dp):
                shutil.rmtree(dp)
            else:
                os.remove(dp)
        except Exception as e:
            self.set_error(str(e))
            return
        p.reload()

    def action_copy(self) -> None:
        self.start_copy_or_move(False)

    def action_move(self) -> None:
        self.start_copy_or_move(True)

    def action_remove(self) -> None:
        p = self.panels[self.active]
        if p.tagged:
            self.show_prompt(
                f"DELETE {len(p.tagged)} files? (y/n): ",
                "",
                lambda ans: self._do_remove_tagged(ans),
            )
        else:
            f = p.selected_file()
            if f and f.name != "..":
                name = f.name
                self.show_prompt(
                    f"DELETE {name}? (y/n): ",
                    "",
                    lambda ans, n=name: self._do_remove_single(ans, n),
                )

    def _do_remove_tagged(self, ans: str) -> None:
        if ans != "y":
            return
        p = self.panels[self.active]
        names = [f.name for f in p.files if p.tagged.get(f.name)]
        for n in names:
            self.do_delete(n)

    def _do_remove_single(self, ans: str, name: str) -> None:
        if ans != "y":
            return
        self.do_delete(name)

    def action_mkdir(self) -> None:
        def do_it(name: str) -> None:
            p = self.panels[self.active]
            dp = p.disk_path(name)
            if not dp:
                try:
                    p.fs.mkdir_all(vfs_join(p.fs, p.path, name))
                except Exception as e:
                    self.set_error(str(e))
            else:
                try:
                    os.makedirs(dp, exist_ok=True)
                except Exception as e:
                    self.set_error(str(e))
            p.reload()

        self.show_prompt("mkdir: ", "", do_it)

    def action_touch(self) -> None:
        def do_it(name: str) -> None:
            p = self.panels[self.active]
            dp = p.disk_path(name)
            if not dp:
                self.set_error("new file not supported in virtual FS")
                return
            try:
                open(dp, "a").close()
            except Exception as e:
                self.set_error(str(e))
            p.reload()

        self.show_prompt("new file: ", "", do_it)

    def action_chmod(self) -> None:
        p = self.panels[self.active]
        f = p.selected_file()
        if not f or f.name == "..":
            return
        dp = p.disk_path(f.name)
        if not dp:
            self.set_error("chmod not supported in virtual FS")
            return
        try:
            info = os.stat(dp)
        except Exception as e:
            self.set_error(str(e))
            return
        current = f"{stat.S_IMODE(info.st_mode):04o}"

        def do_it(mode_str: str) -> None:
            try:
                mode = int(mode_str, 8)
            except ValueError:
                self.set_error("invalid mode: " + mode_str)
                return
            try:
                os.chmod(dp, mode)
            except Exception as e:
                self.set_error(str(e))
            p.reload()

        self.show_prompt("chmod: ", current, do_it)

    def action_rename(self) -> None:
        p = self.panels[self.active]
        f = p.selected_file()
        if not f or f.name == "..":
            return
        dp = p.disk_path(f.name)
        if not dp:
            self.set_error("rename not supported in virtual FS")
            return

        def do_it(new_name: str) -> None:
            new_path = os.path.join(p.path, new_name)
            try:
                os.rename(dp, new_path)
            except Exception as e:
                self.set_error(str(e))
            p.reload()

        self.show_prompt("rename to: ", f.name, do_it)

    def action_chdir(self, *paths: str) -> None:
        if paths:
            path = expand_home(paths[0])
            p = self.panels[self.active]
            p.path = path
            p.cursor = 0
            p.offset = 0
            p.load_dir()
        else:

            def do_it(path: str) -> None:
                path = expand_home(path)
                p = self.panels[self.active]
                p.path = path
                p.cursor = 0
                p.offset = 0
                p.load_dir()

            self.show_prompt("chdir: ", self.panels[self.active].path, do_it)

    def action_remotes(self) -> None:
        remotes_dir = os.path.join(os.path.expanduser("~"), ".xc", "remotes")
        if not os.path.isdir(remotes_dir):
            self.set_error("no remotes directory")
            return
        files = sorted(
            f
            for f in os.listdir(remotes_dir)
            if os.path.isfile(os.path.join(remotes_dir, f))
            and f.rsplit(".", 1)[-1] in ("s3", "gcs", "ssh")
        )
        if not files:
            self.set_error("no remotes found")
            return
        keys = "abcdefghijklmnopqrstuvwxyz"
        items: list[MenuItem] = []
        for i, name in enumerate(files):
            key = keys[i] if i < len(keys) else str(i)
            path = os.path.join(remotes_dir, name)
            items.append(
                MenuItem(
                    key,
                    name,
                    lambda p=path: self.panels[self.active].enter_remote(p),
                )
            )
        self.add_menu("remote", items)
        self.menu_selector("remote")

    def action_run(self, cmd: str) -> None:
        p = self.panels[self.active]
        f = p.selected_file()
        is_remote = not isinstance(p.fs, LocalFS) and f
        uses_F = "%F" in cmd or "%~F" in cmd or "%X" in cmd or "%~X" in cmd

        if is_remote and uses_F:
            self._action_run_remote(cmd, p, f)
        else:
            expanded, background = self.expand_macro(cmd)
            if background or is_interactive_cmd(expanded):
                self.run_shell_cmd(expanded, True)
            else:
                self.run_shell_cmd(expanded, False)

    def _action_run_remote(
        self,
        cmd: str,
        p: Panel,
        f: VFile,
    ) -> None:
        remote_path = p.vfs_path(f.name)
        _, ext = os.path.splitext(f.name)
        try:
            data = p.fs.read_file(remote_path)
            content = data.read()
        except Exception as e:
            self.set_error(f"download: {e}")
            return

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="xc-")
        try:
            os.write(tmp_fd, content)
            os.close(tmp_fd)
            mtime_before = os.path.getmtime(tmp_path)

            # expand macros with tmp_path standing in for %F
            expanded, background = self._expand_macro_with_path(cmd, tmp_path)
            fire = background or is_interactive_cmd(expanded)

            curses.endwin()
            user_shell = os.environ.get("SHELL", "sh")
            if fire:
                shell = expanded
            else:
                shell = f"{{ {expanded}; }} 2>&1 | less"
            rc = subprocess.run([user_shell, "-c", shell]).returncode
            self.scr.refresh()
            curses.raw()

            if rc == 0:
                mtime_after = os.path.getmtime(tmp_path)
                if mtime_after != mtime_before:
                    with open(tmp_path, "rb") as fh:
                        p.fs.write_file(remote_path, fh)
            else:
                self.set_error(f"error: exit code = {rc}")
        except Exception as e:
            self.set_error(str(e))
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        p.reload()

    def _expand_macro_with_path(
        self,
        cmd: str,
        local_path: str,
    ) -> tuple[str, bool]:
        """Like expand_macro but %F/%X resolve to *local_path*."""
        p = self.panels[self.active]
        f = p.selected_file()
        background = False
        result: list[str] = []
        runes = list(cmd)
        i = 0
        while i < len(runes):
            if runes[i] != "%" or i + 1 >= len(runes):
                result.append(runes[i])
                i += 1
                continue
            i += 1
            no_quote = False
            if runes[i] == "~" and i + 1 < len(runes):
                no_quote = True
                i += 1
            ch = runes[i]
            i += 1
            val = ""
            if ch == "&":
                background = True
                continue
            elif ch == "f":
                val = f.name if f else ""
            elif ch == "F":
                val = local_path
            elif ch == "x":
                val = f.base_name() if f else ""
            elif ch == "X":
                base, _ = os.path.splitext(local_path)
                val = base
            elif ch == "m":
                names = [ff.name for ff in p.files if p.tagged.get(ff.name)]
                if no_quote:
                    val = " ".join(names)
                else:
                    val = " ".join(shell_quote(n) for n in names)
                result.append(val)
                continue
            elif ch == "M":
                paths = [
                    p.disk_path(ff.name)
                    for ff in p.files
                    if p.tagged.get(ff.name) and p.disk_path(ff.name)
                ]
                if no_quote:
                    val = " ".join(paths)
                else:
                    val = " ".join(shell_quote(pp) for pp in paths)
                result.append(val)
                continue
            elif ch == "d":
                val = os.path.basename(p.path)
            elif ch == "D":
                val = p.path
            else:
                result.append("%")
                result.append(ch)
                continue
            if no_quote:
                result.append(val)
            else:
                result.append(shell_quote(val))
        return "".join(result), background

    # -- Shell --

    def run_shell_cmd(self, cmd: str, fire_and_forget: bool) -> None:
        p = self.panels[self.active]
        user_shell = os.environ.get("SHELL", "sh")
        log.info("runShellCmd cmd=%s fireAndForget=%s", cmd, fire_and_forget)
        self.do_save_state()
        self.err_msg = ""

        # Temporarily leave curses mode
        curses.endwin()

        if fire_and_forget:
            shell = f"cd {shell_quote(p.path)} && {cmd}"
        else:
            shell = f"cd {shell_quote(p.path)} && {{ {cmd}; }} 2>&1 | less"

        try:
            rc = subprocess.run([user_shell, "-c", shell]).returncode
            if rc != 0:
                self.err_msg = f"error: exit code = {rc}"
        except Exception as e:
            self.err_msg = str(e)

        # Resume curses
        self.scr.refresh()
        curses.raw()
        p.reload()

    def run_assoc(self, cmd: str, fire_and_forget: bool) -> None:
        expanded, bg = self.expand_macro(cmd)
        if bg:
            fire_and_forget = True
        self.run_shell_cmd(expanded, fire_and_forget)

    def exec_command(self) -> None:
        cmd = "".join(self.cmd_line).strip()
        if not cmd:
            return
        self.add_cmd_history(cmd)
        self.cmd_hist_mode = False
        self.cmd_hist_idx = -1
        mode = self.cmd_mode
        self.cmd_mode = 0
        self.cmd_line = []
        self.cmd_cursor = 0

        fire_and_forget = cmd.endswith("&")
        if fire_and_forget:
            cmd = cmd[:-1].strip()
        elif mode == 1:
            fire_and_forget = True
        else:
            fire_and_forget = is_interactive_cmd(cmd)
        self.run_shell_cmd(cmd, fire_and_forget)

    def calc_selected_dir_sizes(self) -> None:
        p = self.panels[self.active]

        def calc_for(f: VFile | None) -> None:
            if not f or not f.is_dir() or f.name == "..":
                return
            dp = p.disk_path(f.name)
            if not dp:
                return
            p.dir_sizes[f.name] = calc_dir_size(dp)

        if p.tagged:
            for f in p.files:
                if p.tagged.get(f.name):
                    calc_for(f)
        else:
            calc_for(p.selected_file())

    # -- Copy history --

    def filtered_copy_history(self) -> list[str]:
        idx = self.copy_mode - 1
        if idx < 0 or idx > 1:
            return []
        query = "".join(self.copy_edit)
        if self.copy_hist_idx >= 0:
            query = "".join(self.copy_edit_saved)
        query = query.lower()
        matches = []
        for h in self.copy_history[idx]:
            if not query or query in h.lower():
                matches.append(h)
                if len(matches) >= 5:
                    break
        return matches

    def add_copy_history(self, idx: int, val: str) -> None:
        hist = [h for h in self.copy_history[idx] if h != val]
        self.copy_history[idx] = [val] + hist
        if len(self.copy_history[idx]) > 20:
            self.copy_history[idx] = self.copy_history[idx][:20]

    def add_cmd_history(self, val: str) -> None:
        hist = [h for h in self.cmd_history if h != val]
        self.cmd_history = [val] + hist
        if len(self.cmd_history) > 100:
            self.cmd_history = self.cmd_history[:100]

    # -- Drawing --

    def safe_addstr(self, y: int, x: int, s: str, attr: int = 0) -> None:
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        # Truncate to fit
        max_len = w - x
        if max_len <= 0:
            return
        s = s[:max_len]
        try:
            self.scr.addstr(y, x, s, attr)
        except curses.error:
            pass

    def draw_string(
        self, x: int, y: int, s: str, max_w: int, attr: int
    ) -> None:
        runes = list(s)
        out = []
        for i in range(max_w):
            if i < len(runes):
                out.append(runes[i])
            else:
                out.append(" ")
        self.safe_addstr(y, x, "".join(out), attr)

    def set_cell(self, x: int, y: int, ch: int | str, attr: int) -> None:
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        try:
            if isinstance(ch, int):
                self.scr.addch(y, x, ch, attr)
            else:
                self.scr.addstr(y, x, ch, attr)
        except curses.error:
            pass

    def draw(self) -> None:
        self.scr.erase()
        self.cursor_pos = None
        h, w = self.scr.getmaxyx()
        panel_w = w // 2
        panel_h = h - 3

        self.draw_panel(
            0,
            0,
            panel_w,
            panel_h,
            self.panels[0],
            self.active == 0,
        )
        self.draw_panel(
            panel_w,
            0,
            w - panel_w,
            panel_h,
            self.panels[1],
            self.active == 1,
        )
        # Version in top-right corner (keep corner char + char before it)
        ver = " " + VERSION + " "
        vx = w - 2 - len(ver)
        if vx > panel_w + 1:
            attr_border = curses.color_pair(CP_BORDER)
            for i, ch in enumerate(ver):
                self.set_cell(vx + i, 0, ch, attr_border)

        self.draw_status_line(0, h - 3, w)
        self.draw_cmd_line(0, h - 2, w)
        self.draw_err_line(0, h - 1, w)

        if self.menu_active:
            self.draw_menu()
        if self.copy_mode > 0:
            self.draw_copy_history(w, h)
        if self.cmd_hist_mode:
            self.draw_cmd_history(w, h)
        if self.help_mode:
            self.draw_help()

        if self.cursor_pos:
            curses.curs_set(1)
            try:
                self.scr.move(self.cursor_pos[0], self.cursor_pos[1])
            except curses.error:
                pass
        else:
            curses.curs_set(0)

    def draw_panel(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        p: Panel,
        active: bool,
    ) -> None:
        if w < 4 or h < 3:
            return
        inner_w = w - 2
        visible_rows = h - 2
        p.adjust_offset(visible_rows)

        attr_border = curses.color_pair(CP_BORDER)
        attr_def = curses.color_pair(CP_DEF)
        attr_dir = curses.color_pair(CP_DIR) | curses.A_BOLD
        attr_cursor = curses.color_pair(CP_CURSOR)
        attr_tagged = curses.color_pair(CP_TAGGED)
        attr_dim = curses.color_pair(CP_DIM) | curses.A_DIM

        # Top border with path
        self.set_cell(x, y, curses.ACS_ULCORNER, attr_border)
        self.set_cell(x + w - 1, y, curses.ACS_URCORNER, attr_border)
        display_path = shorten_home(p.display_path())
        title = " " + display_path + " "
        for i in range(1, w - 1):
            idx = i - 1
            if idx < len(title):
                self.set_cell(x + i, y, title[idx], attr_border)
            else:
                self.set_cell(x + i, y, curses.ACS_HLINE, attr_border)

        # File rows
        for row in range(visible_rows):
            file_idx = p.offset + row
            row_y = y + 1 + row
            self.set_cell(x, row_y, curses.ACS_VLINE, attr_border)
            self.set_cell(x + w - 1, row_y, curses.ACS_VLINE, attr_border)

            if file_idx < len(p.files):
                f = p.files[file_idx]
                if isinstance(p.fs, GrepFS) and f.name != "..":
                    line = render_grep_result(f, inner_w)
                else:
                    dir_size = p.dir_sizes.get(f.name, -1)
                    line = render_file(f, inner_w, dir_size)
                tagged = p.tagged.get(f.name, False)
                if tagged:
                    chars = list(line)
                    if chars:
                        chars[0] = "+"
                        line = "".join(chars)

                dimmed = is_dimmed(f.name)
                if file_idx == p.cursor and active:
                    style = attr_cursor
                elif tagged:
                    style = attr_tagged
                elif file_idx == p.cursor:
                    style = attr_def
                elif dimmed:
                    style = attr_dim
                elif f.is_dir():
                    style = attr_dir
                else:
                    style = attr_def
                self.draw_string(x + 1, row_y, line, inner_w, style)
            else:
                self.draw_string(x + 1, row_y, "", inner_w, attr_def)

        # Bottom border
        bottom_y = y + h - 1
        counter = f"[{p.cursor}/{len(p.files)}]"
        suffix = ""
        if p.tagged:
            total = 0
            for f in p.files:
                if not p.tagged.get(f.name):
                    continue
                if f.is_dir():
                    ds = p.dir_sizes.get(f.name)
                    if ds is not None:
                        total += ds
                else:
                    total += f.size
            suffix = f" selected {format_size(total)} "

        suffix_start = w - 1 - len(suffix)
        for i in range(w):
            if i < len(counter):
                self.set_cell(x + i, bottom_y, counter[i], attr_border)
            elif suffix and suffix_start <= i < suffix_start + len(suffix):
                self.set_cell(
                    x + i,
                    bottom_y,
                    suffix[i - suffix_start],
                    attr_border,
                )
            elif i == w - 1:
                self.set_cell(x + i, bottom_y, curses.ACS_LRCORNER, attr_border)
            else:
                self.set_cell(x + i, bottom_y, curses.ACS_HLINE, attr_border)

    def draw_status_line(self, x: int, y: int, w: int) -> None:
        attr = curses.color_pair(CP_STATUS)
        p = self.panels[self.active]
        f = p.selected_file()
        if not f:
            self.draw_string(x, y, "", w, attr)
            return
        parts = []
        dp = p.disk_path(f.name)
        if dp:
            try:
                st = os.statvfs(p.path)
                total = st.f_blocks * st.f_frsize
                free = st.f_bavail * st.f_frsize
                used_pct = (total - free) / total * 100 if total > 0 else 0
                parts.append(f"{format_size(free)} free {used_pct:.1f}% used")
            except OSError:
                pass
            try:
                info = os.lstat(dp)
                mode = stat.filemode(info.st_mode)
                nlinks = info.st_nlink
                dt = datetime.fromtimestamp(info.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                prefix = f"{mode} {nlinks} {info.st_size} {dt} "
                prior = " ".join(parts) + " " if parts else ""
                avail = w - len(prior) - len(prefix)
                name = shorten_name(f.name, max(avail, 1))
                parts.append(prefix + name)
            except OSError:
                pass
        else:
            type_str = "dir" if f.is_dir() else "file"
            dt = (
                datetime.fromtimestamp(f.mod_time).strftime("%Y-%m-%d %H:%M:%S")
                if f.mod_time
                else ""
            )
            prefix = f"{type_str} {format_size(f.size)} {dt} "
            prior = " ".join(parts) + " " if parts else ""
            avail = w - len(prior) - len(prefix)
            name = shorten_name(f.name, max(avail, 1))
            parts.append(prefix + name)
        self.draw_string(x, y, " ".join(parts), w, attr)

    def draw_cmd_line(self, x: int, y: int, w: int) -> None:
        attr = curses.color_pair(CP_CMDLINE)
        if self.prompt_mode:
            pw = len(self.prompt_label)
            self.draw_string(x, y, self.prompt_label, pw, attr)
            edit_w = w - pw
            text = "".join(self.prompt_edit)
            self.draw_string(x + pw, y, text, edit_w, attr)
            self.cursor_pos = (y, x + pw + self.prompt_cursor)
            return

        if self.copy_mode > 0:
            verb = "Move" if self.copy_is_move else "Copy"
            if self.copy_mode == 1:
                prompt = f"{verb} from "
            else:
                prompt = f"{verb} from {self.copy_from} to "
            pw = len(prompt)
            self.draw_string(x, y, prompt, pw, attr)
            edit_w = w - pw
            text = "".join(self.copy_edit)
            self.draw_string(x + pw, y, text, edit_w, attr)
            self.cursor_pos = (y, x + pw + self.copy_cursor)
            return

        if self.grep_mode > 0:
            if self.grep_mode == 1:
                prompt = "search "
            else:
                pat = self.grep_file_pattern or "*"
                prompt = f"search {pat} grep for "
            pw = len(prompt)
            self.draw_string(x, y, prompt, pw, attr)
            edit_w = w - pw
            text = "".join(self.grep_edit)
            self.draw_string(x + pw, y, text, edit_w, attr)
            self.cursor_pos = (y, x + pw + self.grep_cursor)
            return

        if self.search_mode:
            prompt = "/ "
            pw = 2
            self.draw_string(x, y, prompt, pw, attr)
            edit_w = w - pw
            text = "".join(self.search_query)
            self.draw_string(x + pw, y, text, edit_w, attr)
            self.cursor_pos = (y, x + pw + len(self.search_query))
            return

        if self.cmd_mode == 0:
            self.draw_string(x, y, "", w, attr)
            return

        prompt = "> " if self.cmd_mode == 1 else "] "
        pw = 2
        self.draw_string(x, y, prompt, pw, attr)
        edit_w = w - pw
        view_offset = 0
        if self.cmd_cursor > edit_w - 1:
            view_offset = self.cmd_cursor - edit_w + 1
        end = view_offset + edit_w
        if end > len(self.cmd_line):
            end = len(self.cmd_line)
        visible = "".join(self.cmd_line[view_offset:end])
        self.draw_string(x + pw, y, visible, edit_w, attr)
        self.cursor_pos = (y, x + pw + self.cmd_cursor - view_offset)

    def draw_err_line(self, x: int, y: int, w: int) -> None:
        attr = curses.color_pair(CP_ERR) | curses.A_BOLD
        self.draw_string(x, y, self.err_msg, w, attr)

    def draw_menu(self) -> None:
        menu = self.menus.get(self.menu_active)
        if not menu:
            return
        h, w = self.scr.getmaxyx()
        panel_w = w // 2
        panel_h = h - 3
        panel_x = 0
        pw = panel_w
        if self.active == 1:
            panel_x = panel_w
            pw = w - panel_w
        inner_w = pw - 2
        max_menu_h = min(10, panel_h - 2)
        visible_h = min(len(menu.items), max_menu_h)
        if self.menu_cursor < self.menu_offset:
            self.menu_offset = self.menu_cursor
        if self.menu_cursor >= self.menu_offset + visible_h:
            self.menu_offset = self.menu_cursor - visible_h + 1
        menu_start_y = panel_h - 1 - visible_h
        attr_menu = curses.color_pair(CP_MENU)
        attr_sel = curses.color_pair(CP_MENUSEL)
        for i in range(visible_h):
            idx = self.menu_offset + i
            row_y = menu_start_y + i
            if idx >= len(menu.items):
                break
            item = menu.items[idx]
            line = f" {item.key}  {item.label}"
            style = attr_sel if idx == self.menu_cursor else attr_menu
            self.draw_string(panel_x + 1, row_y, line, inner_w, style)

    def draw_copy_history(self, screen_w: int, screen_h: int) -> None:
        items = self.filtered_copy_history()
        if not items:
            return
        cmd_y = screen_h - 2
        attr = curses.color_pair(CP_CMDLINE)
        attr_sel = curses.color_pair(CP_CURSOR)
        for i, item in enumerate(items):
            row_y = cmd_y - len(items) + i
            if row_y < 0:
                continue
            style = attr_sel if i == self.copy_hist_idx else attr
            self.draw_string(0, row_y, " " + item, screen_w, style)

    def draw_cmd_history(self, screen_w: int, screen_h: int) -> None:
        if not self.cmd_history:
            return
        total = len(self.cmd_history)
        visible_h = min(5, total)
        cmd_y = screen_h - 2
        if self.cmd_hist_idx < self.cmd_hist_offset:
            self.cmd_hist_offset = self.cmd_hist_idx
        if self.cmd_hist_idx >= self.cmd_hist_offset + visible_h:
            self.cmd_hist_offset = self.cmd_hist_idx - visible_h + 1
        attr = curses.color_pair(CP_CMDLINE)
        attr_sel = curses.color_pair(CP_CURSOR)
        for i in range(visible_h):
            idx = self.cmd_hist_offset + i
            if idx >= total:
                break
            row_y = cmd_y - visible_h + i
            if row_y < 0:
                continue
            style = attr_sel if idx == self.cmd_hist_idx else attr
            self.draw_string(
                0, row_y, " " + self.cmd_history[idx], screen_w, style
            )

    def draw_help(self) -> None:
        lines = [
            "Navigation",
            "  ↑/↓  k/j     move cursor",
            "  Enter         enter directory / open file",
            "  Backspace     go to parent directory",
            "  Tab           switch panel",
            "  PgUp/PgDn     page up / page down",
            "  Home/End      first / last file",
            "  Space         tag file",
            "  +/_           tag all / untag all",
            "",
            "Menus",
            "  x             command menu",
            "  e             editor menu",
            "  v             view menu",
            "  b             bookmark menu",
            "  r             remotes",
            "",
            "Commands",
            "  ;             command line (shell)",
            "  :             command line (internal)",
            "  s/S           search / search with grep",
            "  /             search in file list",
            "  i             calculate directory sizes",
            "",
            "Other",
            "  ESC ESC       show main screen",
            "  Ctrl-L        reload panel",
            "  Ctrl-C ×2     quit",
            "  q             quit",
            "  h             this help",
        ]
        h, w = self.scr.getmaxyx()
        box_w = max(len(line) for line in lines) + 6
        box_h = len(lines) + 4
        x0 = (w - box_w) // 2
        y0 = (h - box_h) // 2
        attr = curses.color_pair(CP_MENU)
        attr_title = curses.color_pair(CP_MENU) | curses.A_BOLD
        attr_border = curses.color_pair(CP_BORDER)
        # fill background
        for dy in range(box_h):
            self.draw_string(x0, y0 + dy, "", box_w, attr)
        # border
        self.set_cell(x0, y0, curses.ACS_ULCORNER, attr_border)
        self.set_cell(x0 + box_w - 1, y0, curses.ACS_URCORNER, attr_border)
        self.set_cell(x0, y0 + box_h - 1, curses.ACS_LLCORNER, attr_border)
        self.set_cell(
            x0 + box_w - 1, y0 + box_h - 1, curses.ACS_LRCORNER, attr_border
        )
        for i in range(1, box_w - 1):
            self.set_cell(x0 + i, y0, curses.ACS_HLINE, attr_border)
            self.set_cell(x0 + i, y0 + box_h - 1, curses.ACS_HLINE, attr_border)
        for i in range(1, box_h - 1):
            self.set_cell(x0, y0 + i, curses.ACS_VLINE, attr_border)
            self.set_cell(x0 + box_w - 1, y0 + i, curses.ACS_VLINE, attr_border)
        # content
        for i, line in enumerate(lines):
            a = attr_title if line and not line.startswith(" ") else attr
            self.draw_string(x0 + 3, y0 + 2 + i, line, box_w - 6, a)

    # -- Key handling --

    def cmd_insert_string(self, s: str) -> None:
        chars = list(s)
        self.cmd_line = (
            self.cmd_line[: self.cmd_cursor]
            + chars
            + self.cmd_line[self.cmd_cursor :]
        )
        self.cmd_cursor += len(chars)

    def handle_prompt_key(self, key: int) -> None:
        if key == 27:  # ESC
            self.prompt_mode = False
        elif key in (curses.KEY_ENTER, 10, 13):
            self.prompt_mode = False
            if self.prompt_action:
                self.prompt_action("".join(self.prompt_edit))
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            if self.prompt_cursor > 0:
                self.prompt_edit = (
                    self.prompt_edit[: self.prompt_cursor - 1]
                    + self.prompt_edit[self.prompt_cursor :]
                )
                self.prompt_cursor -= 1
        elif key == curses.KEY_LEFT:
            if self.prompt_cursor > 0:
                self.prompt_cursor -= 1
        elif key == curses.KEY_RIGHT:
            if self.prompt_cursor < len(self.prompt_edit):
                self.prompt_cursor += 1
        elif key == 1:  # Ctrl-A
            self.prompt_cursor = 0
        elif key == 5:  # Ctrl-E
            self.prompt_cursor = len(self.prompt_edit)
        elif key == 21:  # Ctrl-U
            self.prompt_edit = self.prompt_edit[self.prompt_cursor :]
            self.prompt_cursor = 0
        elif key == 11:  # Ctrl-K
            self.prompt_edit = self.prompt_edit[: self.prompt_cursor]
        elif 32 <= key <= 0x10FFFF:
            ch = chr(key)
            self.prompt_edit = (
                self.prompt_edit[: self.prompt_cursor]
                + [ch]
                + self.prompt_edit[self.prompt_cursor :]
            )
            self.prompt_cursor += 1

    def handle_menu_key(self, key: int) -> None:
        menu = self.menus.get(self.menu_active)
        if not menu:
            self.menu_active = ""
            return
        if key == 27:
            self.pop_menu()
        elif key == curses.KEY_UP:
            if self.menu_cursor > 0:
                self.menu_cursor -= 1
        elif key == curses.KEY_DOWN:
            if self.menu_cursor < len(menu.items) - 1:
                self.menu_cursor += 1
        elif key in (curses.KEY_ENTER, 10, 13):
            self.exec_menu_item(menu.items[self.menu_cursor])
        elif 32 <= key <= 0x10FFFF:
            ch = chr(key)
            for item in menu.items:
                if item.key == ch:
                    self.exec_menu_item(item)
                    return

    def handle_copy_key(self, key: int) -> None:
        if key == 27:
            self.copy_mode = 0
            return
        if key in (curses.KEY_ENTER, 10, 13):
            if self.copy_mode == 1:
                self.copy_from = "".join(self.copy_edit)
                self.add_copy_history(0, self.copy_from)
                other = self.panels[1 - self.active]
                dest = other.path
                self.copy_edit = list(dest)
                self.copy_cursor = len(self.copy_edit)
                self.copy_hist_idx = -1
                self.copy_mode = 2
            else:
                dest = "".join(self.copy_edit)
                self.add_copy_history(1, dest)
                is_move = self.copy_is_move
                self.copy_mode = 0
                p = self.panels[self.active]
                if p.tagged:
                    names = [f.name for f in p.files if p.tagged.get(f.name)]
                    self._copy_tagged(names, dest)
                    if is_move:
                        for name in names:
                            self.do_delete(name)
                else:
                    self.do_copy(self.copy_from, dest)
                    if is_move:
                        self.do_delete(self.copy_from)
            return

        if key == curses.KEY_UP:
            items = self.filtered_copy_history()
            if not items:
                return
            if self.copy_hist_idx < 0:
                self.copy_edit_saved = list(self.copy_edit)
                self.copy_hist_idx = len(items) - 1
            elif self.copy_hist_idx > 0:
                self.copy_hist_idx -= 1
            self.copy_edit = list(items[self.copy_hist_idx])
            self.copy_cursor = len(self.copy_edit)
        elif key == curses.KEY_DOWN:
            items = self.filtered_copy_history()
            if self.copy_hist_idx < 0:
                return
            if self.copy_hist_idx < len(items) - 1:
                self.copy_hist_idx += 1
                self.copy_edit = list(items[self.copy_hist_idx])
                self.copy_cursor = len(self.copy_edit)
            else:
                self.copy_hist_idx = -1
                self.copy_edit = list(self.copy_edit_saved)
                self.copy_cursor = len(self.copy_edit)
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.copy_hist_idx = -1
            if self.copy_cursor > 0:
                self.copy_edit = (
                    self.copy_edit[: self.copy_cursor - 1]
                    + self.copy_edit[self.copy_cursor :]
                )
                self.copy_cursor -= 1
        elif key == curses.KEY_LEFT:
            if self.copy_cursor > 0:
                self.copy_cursor -= 1
        elif key == curses.KEY_RIGHT:
            if self.copy_cursor < len(self.copy_edit):
                self.copy_cursor += 1
        elif key == 1:  # Ctrl-A
            self.copy_cursor = 0
        elif key == 5:  # Ctrl-E
            self.copy_cursor = len(self.copy_edit)
        elif key == 21:  # Ctrl-U
            self.copy_hist_idx = -1
            self.copy_edit = self.copy_edit[self.copy_cursor :]
            self.copy_cursor = 0
        elif key == 11:  # Ctrl-K
            self.copy_hist_idx = -1
            self.copy_edit = self.copy_edit[: self.copy_cursor]
        elif 32 <= key <= 0x10FFFF:
            self.copy_hist_idx = -1
            ch = chr(key)
            self.copy_edit = (
                self.copy_edit[: self.copy_cursor]
                + [ch]
                + self.copy_edit[self.copy_cursor :]
            )
            self.copy_cursor += 1

    def handle_grep_key(self, key: int) -> None:
        if key == 27:
            self.grep_mode = 0
            return
        if key in (curses.KEY_ENTER, 10, 13):
            if self.grep_mode == 1:
                self.grep_file_pattern = "".join(self.grep_edit).strip()
                self.grep_edit = []
                self.grep_cursor = 0
                if self.grep_with_grep:
                    self.grep_mode = 2
                else:
                    self.exec_grep()
            else:
                self.exec_grep()
            return
        if key in (curses.KEY_BACKSPACE, 127, 8):
            if self.grep_cursor > 0:
                self.grep_edit = (
                    self.grep_edit[: self.grep_cursor - 1]
                    + self.grep_edit[self.grep_cursor :]
                )
                self.grep_cursor -= 1
        elif key == curses.KEY_LEFT:
            if self.grep_cursor > 0:
                self.grep_cursor -= 1
        elif key == curses.KEY_RIGHT:
            if self.grep_cursor < len(self.grep_edit):
                self.grep_cursor += 1
        elif key == 1:  # Ctrl-A
            self.grep_cursor = 0
        elif key == 5:  # Ctrl-E
            self.grep_cursor = len(self.grep_edit)
        elif key == 21:  # Ctrl-U
            self.grep_edit = self.grep_edit[self.grep_cursor :]
            self.grep_cursor = 0
        elif key == 11:  # Ctrl-K
            self.grep_edit = self.grep_edit[: self.grep_cursor]
        elif 32 <= key <= 0x10FFFF:
            ch = chr(key)
            self.grep_edit = (
                self.grep_edit[: self.grep_cursor]
                + [ch]
                + self.grep_edit[self.grep_cursor :]
            )
            self.grep_cursor += 1

    def handle_search_key(self, key: int) -> None:
        if key == 27:
            self.search_mode = False
            self.search_query = []
        elif key in (curses.KEY_ENTER, 10, 13):
            self.search_mode = False
            self.search_query = []
            p = self.panels[self.active]
            f = p.selected_file()
            if f and f.name != "..":
                self.cmd_mode = 1
                self.cmd_line = list(quote_if_needed(f.name))
                self.cmd_cursor = len(self.cmd_line)
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            if self.search_query:
                self.search_query.pop()
                self.search_navigate()
        elif 32 <= key <= 0x10FFFF:
            self.search_query.append(chr(key))
            self.search_navigate()

    def search_navigate(self) -> None:
        p = self.panels[self.active]
        prefix = "".join(self.search_query).lower()
        if not prefix:
            return
        for i, f in enumerate(p.files):
            if f.name.lower().startswith(prefix):
                p.move_to(i)
                return

    def handle_cmd_key(self, key: int) -> None:
        p = self.panels[self.active]
        if key == 27:
            if self.cmd_hist_mode:
                self.cmd_hist_mode = False
                self.cmd_line = list(self.cmd_line_saved)
                self.cmd_cursor = len(self.cmd_line)
                self.cmd_hist_idx = -1
                return
            # Read next key with timeout to detect ESC+key vs bare ESC
            self.scr.timeout(500)
            try:
                next_key = self.scr.get_wch()
            except curses.error:
                next_key = None
            self.scr.timeout(-1)
            if next_key is None:
                # Bare ESC → exit cmd mode
                self.cmd_mode = 0
                self.cmd_hist_mode = False
                return
            if isinstance(next_key, str):
                next_key = ord(next_key)
            if next_key in (curses.KEY_ENTER, 10, 13):
                if p.tagged:
                    names = [
                        quote_if_needed(f.name)
                        for f in p.files
                        if p.tagged.get(f.name)
                    ]
                    self.cmd_insert_string(" ".join(names))
                else:
                    f = p.selected_file()
                    if f and f.name != "..":
                        self.cmd_insert_string(quote_if_needed(f.name))
            elif next_key == ord("h"):
                if self.cmd_history:
                    self.cmd_hist_mode = True
                    self.cmd_hist_idx = 0
                    self.cmd_hist_offset = 0
                    self.cmd_line_saved = list(self.cmd_line)
                    self.cmd_line = list(self.cmd_history[0])
                    self.cmd_cursor = len(self.cmd_line)
            return

        if self.cmd_hist_mode:
            if key == curses.KEY_UP:
                if self.cmd_hist_idx > 0:
                    self.cmd_hist_idx -= 1
                    self.cmd_line = list(self.cmd_history[self.cmd_hist_idx])
                    self.cmd_cursor = len(self.cmd_line)
            elif key == curses.KEY_DOWN:
                if self.cmd_hist_idx < len(self.cmd_history) - 1:
                    self.cmd_hist_idx += 1
                    self.cmd_line = list(self.cmd_history[self.cmd_hist_idx])
                    self.cmd_cursor = len(self.cmd_line)
            elif key in (curses.KEY_ENTER, 10, 13):
                self.cmd_hist_mode = False
            else:
                self.cmd_hist_mode = False
                self.cmd_hist_idx = -1
                if 32 <= key <= 0x10FFFF:
                    self.cmd_insert_string(chr(key))
            return

        if key in (curses.KEY_ENTER, 10, 13):
            self.exec_command()
        elif key == 9:  # Tab
            f = p.selected_file()
            if f and f.name != "..":
                self.cmd_insert_string(quote_if_needed(f.name))
        elif key == curses.KEY_UP:
            p.move_to(p.cursor - 1)
        elif key == curses.KEY_DOWN:
            p.move_to(p.cursor + 1)
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            if self.cmd_cursor > 0:
                self.cmd_line = (
                    self.cmd_line[: self.cmd_cursor - 1]
                    + self.cmd_line[self.cmd_cursor :]
                )
                self.cmd_cursor -= 1
        elif key == curses.KEY_LEFT:
            if self.cmd_cursor > 0:
                self.cmd_cursor -= 1
        elif key == curses.KEY_RIGHT:
            if self.cmd_cursor < len(self.cmd_line):
                self.cmd_cursor += 1
        elif key == 1:  # Ctrl-A
            self.cmd_cursor = 0
        elif key == 5:  # Ctrl-E
            self.cmd_cursor = len(self.cmd_line)
        elif key == 21:  # Ctrl-U
            self.cmd_line = self.cmd_line[self.cmd_cursor :]
            self.cmd_cursor = 0
        elif key == 11:  # Ctrl-K
            self.cmd_line = self.cmd_line[: self.cmd_cursor]
        elif 32 <= key <= 0x10FFFF:
            self.cmd_insert_string(chr(key))

    def show_main_screen(self) -> None:
        """Switch to main screen to see command output."""
        curses.endwin()
        sys.stdout.write("\n--- press ESC or Ctrl-O to return ---\n")
        sys.stdout.flush()
        fd = sys.stdin.fileno()
        old_attr = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if not ch:
                    break
                c = ord(ch)
                if c == 15:  # Ctrl-O
                    break
                if c == 27:  # ESC
                    # Bare ESC vs escape sequence
                    r, _, _ = select.select([fd], [], [], 0.05)
                    if not r:
                        break
                    # Consume escape sequence
                    while r:
                        sys.stdin.read(1)
                        r, _, _ = select.select([fd], [], [], 0.01)
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attr)
        self.scr.refresh()
        curses.raw()

    def handle_meta(self, key: int) -> None:
        p = self.panels[self.active]
        h, _ = self.scr.getmaxyx()
        page_size = max(1, h - 3)
        if 32 <= key <= 0x10FFFF:
            ch = chr(key)
            if ch == "v":
                p.move_to(p.cursor - page_size)
            elif ch == "n":
                p.scroll(1)
            elif ch == "p":
                p.scroll(-1)
            elif ch == "<":
                p.move_to(0)
            elif ch == ">":
                p.move_to(len(p.files) - 1)

    def handle_key(self, key: int) -> None:
        if self.help_mode:
            self.help_mode = False
            return
        if self.menu_active:
            self.handle_menu_key(key)
            return
        if self.prompt_mode:
            self.handle_prompt_key(key)
            return
        if self.copy_mode > 0:
            self.handle_copy_key(key)
            return
        if self.grep_mode > 0:
            self.handle_grep_key(key)
            return
        if self.search_mode:
            self.handle_search_key(key)
            return
        if self.cmd_mode > 0:
            self.handle_cmd_key(key)
            return

        # ESC + key → meta combo, ESC ESC → main screen
        if key == 27:
            self.scr.timeout(500)
            try:
                next_key = self.scr.get_wch()
            except curses.error:
                next_key = None
            self.scr.timeout(-1)
            if next_key is not None:
                if isinstance(next_key, str):
                    next_key = ord(next_key)
                if next_key == 27:
                    self.show_main_screen()
                else:
                    self.handle_meta(next_key)
            return

        p = self.panels[self.active]
        h, _ = self.scr.getmaxyx()
        page_size = max(1, h - 3)
        half_page = max(1, page_size // 2)

        if key == curses.KEY_UP:
            p.move_to(p.cursor - 1)
        elif key == curses.KEY_DOWN:
            p.move_to(p.cursor + 1)
        elif key == curses.KEY_LEFT:
            p.move_to(p.cursor - page_size)
        elif key == curses.KEY_RIGHT:
            p.move_to(p.cursor + page_size)
        elif key in (curses.KEY_ENTER, 10, 13):
            p.enter()
        elif key == 9:  # Tab
            self.active = 1 - self.active
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            p.go_up()
        elif key == curses.KEY_PPAGE:
            p.move_to(p.cursor - page_size)
        elif key == curses.KEY_NPAGE:
            p.move_to(p.cursor + page_size)
        elif key == curses.KEY_HOME:
            p.move_to(0)
        elif key == curses.KEY_END:
            p.move_to(len(p.files) - 1)
        elif key == 14:  # Ctrl-N
            p.move_to(p.cursor + 1)
        elif key == 16:  # Ctrl-P
            p.move_to(p.cursor - 1)
        elif key == 1:  # Ctrl-A
            p.move_to(0)
        elif key == 5:  # Ctrl-E
            p.move_to(len(p.files) - 1)
        elif key == 4:  # Ctrl-D
            p.move_to(p.cursor + half_page)
        elif key == 21:  # Ctrl-U
            p.move_to(p.cursor - half_page)
        elif key == 22:  # Ctrl-V
            p.move_to(p.cursor + page_size)
        elif key == 12:  # Ctrl-L
            p.reload()
        elif key == 15:  # Ctrl-O - toggle main screen to see command output
            self.show_main_screen()
        elif 32 <= key <= 0x10FFFF:
            ch = chr(key)
            # Check keymaps first
            if ch in self.keymaps:
                self.keymaps[ch]()
                return
            if ch == "q":
                self.do_save_state()
                raise SystemExit(0)
            elif ch == "k":
                p.move_to(p.cursor - 1)
            elif ch == "j":
                p.move_to(p.cursor + 1)
            elif ch == "h":
                self.help_mode = True
            elif ch == "^":
                p.move_to(0)
            elif ch == "G":
                p.move_to(len(p.files) - 1)
            elif ch == "/":
                self.search_mode = True
                self.search_query = []
            elif ch == " ":
                f = p.selected_file()
                if f and f.name != "..":
                    if f.name in p.tagged:
                        del p.tagged[f.name]
                    else:
                        p.tagged[f.name] = True
                    p.move_to(p.cursor + 1)
            elif ch == "i":
                self.calc_selected_dir_sizes()
            elif ch == "+":
                p.tagged = {f.name: True for f in p.files if f.name != ".."}
            elif ch == "_":
                p.tagged = {}

    def run(self) -> None:
        self.scr.keypad(True)
        self.scr.timeout(-1)
        curses.set_escdelay(25)
        curses.raw()
        while True:
            self.draw()
            self.scr.refresh()
            try:
                key = self.scr.get_wch()
            except curses.error:
                continue
            if isinstance(key, str):
                key = ord(key)
            if key == 3:  # Ctrl-C
                if self.ctrl_c_pending:
                    break
                self.ctrl_c_pending = True
                self.err_msg = "press Ctrl-C again to terminate"
                continue
            if self.ctrl_c_pending:
                self.ctrl_c_pending = False
                self.err_msg = ""
            self.handle_key(key)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(stdscr: curses.window) -> None:
    init_colors()
    curses.curs_set(0)

    home = str(Path.home())
    cwd = os.getcwd() or home

    saved = load_state()
    if saved and saved.active in (0, 1):
        inactive = 1 - saved.active
        inactive_dir = saved.panels[inactive] or home
        if saved.active == 0:
            left_dir, right_dir = cwd, inactive_dir
        else:
            left_dir, right_dir = inactive_dir, cwd
    else:
        left_dir, right_dir = cwd, home

    app = App(stdscr, left_dir, right_dir, saved)

    # Register menus
    app.add_menu(
        "command",
        [
            MenuItem("c", "copy", app.action_copy),
            MenuItem("m", "move", app.action_move),
            MenuItem("d", "delete", app.action_remove),
            MenuItem("k", "mkdir", app.action_mkdir),
            MenuItem("t", "touch", app.action_touch),
            MenuItem("p", "chmod", app.action_chmod),
            MenuItem("r", "rename", app.action_rename),
            MenuItem("g", "chdir", app.action_chdir),
        ],
    )

    app.add_menu(
        "bookmark",
        [
            MenuItem("h", "home", lambda: app.action_chdir(home)),
            MenuItem(
                "d",
                "desktop",
                lambda: app.action_chdir(os.path.join(home, "Desktop")),
            ),
            MenuItem(
                "w",
                "downloads",
                lambda: app.action_chdir(os.path.join(home, "Downloads")),
            ),
            MenuItem(
                "g",
                "github",
                lambda: app.action_chdir(os.path.join(home, "github")),
            ),
            MenuItem(
                "i",
                "iproov",
                lambda: app.action_chdir(os.path.join(home, "iproov")),
            ),
        ]
        + (
            [
                MenuItem(
                    "f",
                    "fork",
                    lambda: app.action_run("fork"),
                ),
            ]
            if sys.platform == "darwin"
            else []
        ),
    )

    app.add_menu(
        "editor",
        [
            MenuItem("v", "vi", lambda: app.action_run("vi %F")),
            MenuItem("m", "mcedit", lambda: app.action_run("mcedit %F")),
            MenuItem("x", "view...", lambda: app.menu("view")),
        ]
        + (
            [MenuItem("c", "cot", lambda: app.action_run("cot %F"))]
            if sys.platform == "darwin"
            else []
        ),
    )

    app.add_menu(
        "view",
        [
            MenuItem("l", "less", lambda: app.action_run("less %F")),
            MenuItem("j", "jq", lambda: app.action_run("cat %F | jq .")),
            MenuItem("v", "xxd", lambda: app.action_run("xxd -g 1 %F")),
        ],
    )

    # Register keymaps
    app.add_keymap("x", lambda: app.menu_selector("command"))
    app.add_keymap("b", lambda: app.menu_selector("bookmark"))
    app.add_keymap("e", lambda: app.menu_selector("editor"))
    app.add_keymap("v", lambda: app.menu_selector("view"))
    app.add_keymap(";", lambda: setattr(app, "cmd_mode", 1))
    app.add_keymap(":", lambda: setattr(app, "cmd_mode", 2))
    app.add_keymap("r", lambda: app.action_remotes())
    app.add_keymap("s", lambda: app.start_grep(False))
    app.add_keymap("S", lambda: app.start_grep(True))

    app.run()


def _parse_version(text: str) -> tuple[int, ...]:
    import re

    m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not m:
        return (0,)
    return tuple(int(x) for x in m.group(1).split("."))


REMOTE_URL = "https://raw.githubusercontent.com/begoon/xc/main/xc.py"


def _fetch_remote() -> str:
    print(f"fetching {REMOTE_URL} ...")
    try:
        with urllib.request.urlopen(REMOTE_URL) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        sys.exit(f"download failed: {e}")


def self_update() -> None:
    exe = Path(sys.argv[0]).resolve()
    prev = exe.with_name("xc.prev")
    remote_text = _fetch_remote()
    remote_version = _parse_version(remote_text)
    local_version = _parse_version(exe.read_text())
    rv = ".".join(str(x) for x in remote_version)
    lv = ".".join(str(x) for x in local_version)
    if remote_version <= local_version:
        print(f"already up to date (local {lv}, remote {rv})")
        return
    shutil.copy2(exe, prev)
    exe.write_text(remote_text, encoding="utf-8")
    os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR | stat.S_IXGRP)
    print(f"updated {lv} -> {rv}, previous version saved to {prev}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "-u":
        self_update()
        sys.exit(0)
    init_logging()
    log.info("starting xc.py")
    try:
        curses.wrapper(main)
    except SystemExit:
        pass
    except KeyboardInterrupt:
        pass
