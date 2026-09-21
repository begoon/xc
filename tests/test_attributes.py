import errno
import gzip
import io
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import xc


def panel(fs, path):
    p = SimpleNamespace(fs=fs, path=str(path), files=[])
    p.reload = lambda: setattr(p, "files", fs.read_dir(str(path)))
    p.disk_path = lambda name: (
        str(Path(path) / name) if isinstance(fs, xc.LocalFS) else ""
    )
    p.reload()
    return p


def app(src, dst):
    a = xc.App.__new__(xc.App)
    a.panels = [src, dst]
    a.active = 0
    a.op_cancelled = False
    a.overwrite_all = False
    a._ow_dir_cache = {}
    a.err_msg = ""
    a.progress = lambda _: False
    a.poll_cancel = lambda: False
    a.finish_op = lambda: None
    a._ask_overwrite = lambda *args, **kwargs: "overwrite"
    return a


class AttributeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.src = self.root / "src"
        self.dst = self.root / "dst"
        self.src.mkdir()
        self.dst.mkdir()
        self.fs = xc.LocalFS()
        self.a = app(panel(self.fs, self.src), panel(self.fs, self.dst))

    def tearDown(self):
        for path, dirs, files in os.walk(self.root):
            if hasattr(os, "chflags"):
                os.chflags(path, 0)
                for name in files:
                    os.chflags(
                        os.path.join(path, name), 0, follow_symlinks=False
                    )
            os.chmod(path, 0o700)
        self.temp.cleanup()

    def file(self, name="file", mode=0o755):
        path = self.src / name
        path.write_bytes(b"payload")
        path.chmod(mode)
        os.utime(path, ns=(946684800123456000, 946684801987654000))
        self.a.panels[0].reload()
        return path

    def assert_metadata(self, src, dst):
        a, b = os.lstat(src), os.lstat(dst)
        self.assertEqual(stat.S_IMODE(a.st_mode), stat.S_IMODE(b.st_mode))
        self.assertEqual(a.st_mtime_ns, b.st_mtime_ns)

    def test_copy_permissions_and_timestamps(self):
        for mode in (0o755, 0o600, 0o444, 0o640, 0o751):
            with self.subTest(mode=oct(mode)):
                src = self.file(str(mode), mode)
                before = src.stat()
                self.assertTrue(
                    self.a.do_copy(src.name, str(self.dst)), self.a.err_msg
                )
                self.assert_metadata(src, self.dst / src.name)
                self.assertEqual(
                    before.st_atime_ns, (self.dst / src.name).stat().st_atime_ns
                )

    def test_overwrite_uses_source_permissions(self):
        src = self.file(mode=0o600)
        dst = self.dst / src.name
        dst.write_text("old")
        dst.chmod(0o666)
        self.assertTrue(self.a.do_copy(src.name, str(self.dst)), self.a.err_msg)
        self.assert_metadata(src, dst)
        self.assertEqual(dst.read_bytes(), b"payload")

    def test_directory_metadata_applied_after_children(self):
        d = self.src / "directory"
        d.mkdir()
        (d / "child").write_bytes(b"child")
        d.chmod(0o500)
        os.utime(d, (946684800, 946684800))
        self.a.panels[0].reload()
        self.assertTrue(self.a.do_copy(d.name, str(self.dst)), self.a.err_msg)
        self.assert_metadata(d, self.dst / d.name)
        self.assertEqual((self.dst / d.name / "child").read_bytes(), b"child")

    def test_same_filesystem_move_preserves_inode(self):
        src = self.file(mode=0o600)
        before = src.stat()
        self.a._exec_copy_move(True, src.name, None, str(self.dst))
        dst = self.dst / src.name
        self.assertFalse(src.exists())
        self.assertEqual(dst.stat().st_ino, before.st_ino)
        self.assertEqual(dst.stat().st_mode, before.st_mode)
        self.assertEqual(dst.stat().st_mtime_ns, before.st_mtime_ns)

    def test_cross_filesystem_move_fallback(self):
        src = self.file(mode=0o600)
        before = src.stat()
        with patch.object(
            xc.os, "rename", side_effect=OSError(errno.EXDEV, "cross device")
        ):
            self.a._exec_copy_move(True, src.name, None, str(self.dst))
        self.assertFalse(src.exists(), self.a.err_msg)
        self.assertEqual((self.dst / src.name).stat().st_mode, before.st_mode)
        self.assertEqual(
            (self.dst / src.name).stat().st_mtime_ns, before.st_mtime_ns
        )

    def test_metadata_failure_keeps_move_source_and_old_destination(self):
        src = self.file()
        dst = self.dst / src.name
        dst.write_bytes(b"old")
        with (
            patch.object(
                xc.os,
                "rename",
                side_effect=OSError(errno.EXDEV, "cross device"),
            ),
            patch.object(
                xc,
                "copy_local_metadata",
                side_effect=OSError("metadata failed"),
            ),
        ):
            self.a._exec_copy_move(True, src.name, None, str(self.dst))
        self.assertEqual(src.read_bytes(), b"payload")
        self.assertEqual(dst.read_bytes(), b"old")
        self.assertIn("metadata failed", self.a.err_msg)
        self.assertEqual(list(self.dst.glob(".xc-copy-*")), [])

    def test_same_file_is_not_truncated_or_deleted(self):
        src = self.file()
        self.a._exec_copy_move(True, src.name, None, str(src))
        self.assertEqual(src.read_bytes(), b"payload")
        self.assertIn("same file", self.a.err_msg)

    def test_recursive_copy_into_itself_rejected(self):
        d = self.src / "directory"
        d.mkdir()
        self.a.panels[0].reload()
        self.assertFalse(self.a.do_copy(d.name, str(d / "nested")))
        self.assertFalse((d / "nested").exists())

    def test_symlinks_including_dangling(self):
        target = self.file()
        for name, link_target in [("link", "file"), ("dangling", "absent")]:
            (self.src / name).symlink_to(link_target)
            self.a.panels[0].reload()
            self.assertTrue(self.a.do_copy(name, str(self.dst)), self.a.err_msg)
            self.assertTrue((self.dst / name).is_symlink())
            self.assertEqual(os.readlink(self.dst / name), link_target)
        self.assertEqual(target.read_bytes(), b"payload")

    def test_overwrite_symlink_does_not_modify_target(self):
        src = self.file()
        victim = self.root / "victim"
        victim.write_bytes(b"unchanged")
        (self.dst / src.name).symlink_to(victim)
        self.assertTrue(self.a.do_copy(src.name, str(self.dst)), self.a.err_msg)
        self.assertFalse((self.dst / src.name).is_symlink())
        self.assertEqual(victim.read_bytes(), b"unchanged")

    def test_overwrite_dangling_symlink(self):
        src = self.file()
        dst = self.dst / src.name
        dst.symlink_to("missing")
        self.assertTrue(self.a.do_copy(src.name, str(self.dst)), self.a.err_msg)
        self.assertFalse(dst.is_symlink())
        self.assertEqual(dst.read_bytes(), b"payload")

    def test_directory_symlink_is_not_followed(self):
        d = self.src / "directory"
        d.mkdir()
        (d / "child").write_text("child")
        outside = self.root / "outside"
        outside.mkdir()
        (self.dst / d.name).symlink_to(outside)
        self.a.panels[0].reload()
        self.assertFalse(self.a.do_copy(d.name, str(self.dst)))
        self.assertFalse((outside / "child").exists())

    def test_delete_directory_symlink_only_removes_link(self):
        target = self.src / "target"
        target.mkdir()
        (self.src / "link").symlink_to(target)
        self.a.do_delete("link")
        self.assertTrue(target.is_dir())
        self.assertFalse((self.src / "link").is_symlink())

    def make_tar(self):
        archive = self.root / "test.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            for name, mode in [("executable", 0o755), ("private", 0o600)]:
                member = tarfile.TarInfo(name)
                member.mode = mode
                member.mtime = 946684800
                member.size = 7
                tf.addfile(member, io.BytesIO(b"payload"))
            d = tarfile.TarInfo("empty")
            d.type = tarfile.DIRTYPE
            d.mode = 0o500
            d.mtime = 946684800
            tf.addfile(d)
            link = tarfile.TarInfo("link")
            link.type = tarfile.SYMTYPE
            link.linkname = "private"
            tf.addfile(link)
        fs = xc.TarFS().enter(b"", str(archive))
        self.addCleanup(fs.leave)
        return fs

    def test_tar_single_and_batch(self):
        for batch in (False, True):
            with self.subTest(batch=batch):
                fs = self.make_tar()
                a = app(panel(fs, ""), panel(self.fs, self.dst))
                names = ["executable", "private", "empty", "link"]
                if batch:
                    self.assertEqual(
                        a._copy_tagged(names, str(self.dst)), names, a.err_msg
                    )
                else:
                    for name in names:
                        self.assertTrue(
                            a.do_copy(name, str(self.dst)), a.err_msg
                        )
                for name, mode in [
                    ("executable", 0o755),
                    ("private", 0o600),
                    ("empty", 0o500),
                ]:
                    self.assertEqual(
                        stat.S_IMODE((self.dst / name).stat().st_mode), mode
                    )
                    self.assertEqual(
                        (self.dst / name).stat().st_mtime, 946684800
                    )
                self.assertTrue((self.dst / "link").is_symlink())

    def test_zip_unix_permissions_and_symlink(self):
        archive = self.root / "test.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            for name, mode, data in [
                ("file", stat.S_IFREG | 0o751, b"payload"),
                ("link", stat.S_IFLNK | 0o777, b"file"),
                ("empty/", stat.S_IFDIR | 0o500, b""),
            ]:
                info = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = mode << 16
                zf.writestr(info, data)
        fs = xc.ZipFS().enter(b"", str(archive))
        a = app(panel(fs, ""), panel(self.fs, self.dst))
        for name in ("file", "link", "empty"):
            self.assertTrue(a.do_copy(name, str(self.dst)), a.err_msg)
        self.assertEqual(
            stat.S_IMODE((self.dst / "file").stat().st_mode), 0o751
        )
        self.assertEqual(
            stat.S_IMODE((self.dst / "empty").stat().st_mode), 0o500
        )
        self.assertTrue((self.dst / "link").is_symlink())

    def test_gzip_metadata(self):
        archive = self.root / "file.gz"
        with archive.open("wb") as out:
            with gzip.GzipFile(fileobj=out, mode="wb", mtime=946684800) as f:
                f.write(b"payload")
        archive.chmod(0o600)
        fs = xc.CompressedFS().enter(b"", str(archive))
        a = app(panel(fs, ""), panel(self.fs, self.dst))
        self.assertTrue(a.do_copy("file", str(self.dst)), a.err_msg)
        self.assertEqual(
            stat.S_IMODE((self.dst / "file").stat().st_mode), 0o600
        )
        self.assertEqual((self.dst / "file").stat().st_mtime, 946684800)

    def test_grep_copy_preserves_local_metadata(self):
        src = self.file(mode=0o600)
        fs = xc.GrepFS()
        fs.base_dir = str(self.src)
        fs.add_path(src.name)
        a = app(panel(fs, ""), panel(self.fs, self.dst))
        self.assertTrue(a.do_copy(src.name, str(self.dst)), a.err_msg)
        self.assert_metadata(src, self.dst / src.name)

    def test_ssh_metadata_commands(self):
        src = self.file("file with ' quotes", 0o751)
        fs = xc.SSHFS()

        def run(command):
            return subprocess.run(
                ["/bin/sh", "-c", command],
                check=True,
                capture_output=True,
                text=True,
            ).stdout

        fs._run = run
        metadata = fs.get_metadata(str(src))
        self.assertEqual(metadata.mode, 0o751)
        self.assertEqual(metadata.mtime_ns, src.stat().st_mtime_ns)
        dst = self.dst / src.name
        dst.write_bytes(b"payload")
        fs.set_metadata(str(dst), metadata)
        self.assert_metadata(src, dst)
        fs.write_symlink(str(self.dst / "link"), src.name)
        self.assertEqual(os.readlink(self.dst / "link"), src.name)

    def test_cloud_reports_metadata_loss_and_retains_move_source(self):
        src = self.file(mode=0o600)
        cloud = xc.S3FS()
        objects = {}
        cloud.client = SimpleNamespace(
            put_object=lambda **kw: objects.update({kw["Key"]: kw["Body"]})
        )
        cloud.read_dir = lambda path: []
        a = app(panel(self.fs, self.src), panel(cloud, ""))
        a._exec_copy_move(True, src.name, None, "remote")
        self.assertEqual(objects["remote"], b"payload")
        self.assertTrue(src.exists())
        self.assertIn("cannot preserve", a.err_msg)

    @unittest.skipUnless(shutil.which("xattr"), "macOS xattr utility required")
    def test_macos_extended_attributes(self):
        src = self.file()
        subprocess.run(
            ["xattr", "-w", "com.xc.test", "metadata", str(src)], check=True
        )
        self.assertTrue(self.a.do_copy(src.name, str(self.dst)), self.a.err_msg)
        value = subprocess.check_output(
            ["xattr", "-p", "com.xc.test", str(self.dst / src.name)]
        )
        self.assertEqual(value.strip(), b"metadata")

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACLs required")
    def test_macos_acl_preserved(self):
        src = self.file()
        subprocess.run(
            ["chmod", "+a", "everyone deny delete", str(src)], check=True
        )
        try:
            self.assertTrue(
                self.a.do_copy(src.name, str(self.dst)), self.a.err_msg
            )
            acl = subprocess.check_output(
                ["ls", "-le", str(self.dst / src.name)], text=True
            )
            source_acl = subprocess.check_output(
                ["ls", "-le", str(src)], text=True
            )
            self.assertEqual(acl.splitlines()[1:], source_acl.splitlines()[1:])
            self.assertIn("deny delete", acl)
        finally:
            subprocess.run(["chmod", "-N", str(src)], check=True)
            if (self.dst / src.name).exists():
                subprocess.run(
                    ["chmod", "-N", str(self.dst / src.name)], check=True
                )

    @unittest.skipUnless(hasattr(os, "chflags"), "file flags required")
    def test_file_flags_preserved_after_staging(self):
        src = self.file()
        os.chflags(src, stat.UF_IMMUTABLE)
        self.assertTrue(self.a.do_copy(src.name, str(self.dst)), self.a.err_msg)
        self.assertEqual(
            (self.dst / src.name).stat().st_flags, src.stat().st_flags
        )

    def test_overwrite_skip_keeps_move_source(self):
        src = self.file()
        dst = self.dst / src.name
        dst.write_text("old")
        self.a._ask_overwrite = lambda *args, **kwargs: "skip"
        self.a._exec_copy_move(True, src.name, None, str(self.dst))
        self.assertTrue(src.exists())
        self.assertEqual(dst.read_text(), "old")

    def test_tagged_native_moves(self):
        a, b = self.file("a", 0o600), self.file("b", 0o755)
        inodes = {p.name: p.stat().st_ino for p in (a, b)}
        self.a._exec_copy_move(True, None, ["a", "b"], str(self.dst))
        for name, inode in inodes.items():
            self.assertFalse((self.src / name).exists())
            self.assertEqual((self.dst / name).stat().st_ino, inode)

    def test_tar_late_directory_header(self):
        archive = self.root / "late.tar"
        with tarfile.open(archive, "w") as tf:
            f = tarfile.TarInfo("dir/child")
            f.size = 7
            tf.addfile(f, io.BytesIO(b"payload"))
            d = tarfile.TarInfo("dir")
            d.type = tarfile.DIRTYPE
            d.mode = 0o700
            d.mtime = 946684800
            tf.addfile(d)
        fs = xc.TarFS().enter(b"", str(archive))
        self.addCleanup(fs.leave)
        a = app(panel(fs, ""), panel(self.fs, self.dst))
        self.assertTrue(a.do_copy("dir", str(self.dst)), a.err_msg)
        self.assertEqual(stat.S_IMODE((self.dst / "dir").stat().st_mode), 0o700)
        self.assertEqual((self.dst / "dir").stat().st_mtime, 946684800)

    def test_archive_traversal_rejected(self):
        archive = self.root / "unsafe.tar"
        with tarfile.open(archive, "w") as tf:
            tf.addfile(tarfile.TarInfo("../outside"))
        with self.assertRaisesRegex(OSError, "unsafe archive path"):
            xc.TarFS().enter(b"", str(archive))

    def test_ssh_write_replaces_symlink_without_following_it(self):
        victim = self.file("victim", 0o600)
        dst = self.dst / "link"
        dst.symlink_to(victim)
        fs = xc.SSHFS()

        def run(command, *, stdin):
            return subprocess.run(
                ["/bin/sh", "-c", command],
                input=stdin,
                capture_output=True,
                check=True,
            ).stdout

        fs._run_bytes = run
        fs.write_file(str(dst), io.BytesIO(b"new"))
        self.assertFalse(dst.is_symlink())
        self.assertEqual(dst.read_bytes(), b"new")
        self.assertEqual(victim.read_bytes(), b"payload")

    def test_remote_command_can_execute_and_upload_mode_only_changes(self):
        fs = xc.SSHFS()
        fs.read_dir = lambda path: []
        fs.get_metadata = lambda path: xc.FileMetadata(
            mode=0o755, mtime_ns=946684800000000000
        )
        fs.read_file = lambda path: io.BytesIO(b"payload")
        updated = []
        fs.set_metadata = lambda path, meta: updated.append(meta)
        fs.write_file = lambda path, data: self.fail(
            "mode-only change should not upload contents"
        )
        p = panel(fs, "")
        p.vfs_path = lambda name: name
        a = app(p, panel(self.fs, self.dst))
        a.scr = SimpleNamespace(refresh=lambda: None)
        path = []

        def expand(command, tmp):
            path.append(tmp)
            return (tmp, True)

        a._expand_macro_with_path = expand

        def command(*args, **kwargs):
            self.assertEqual(stat.S_IMODE(os.stat(path[0]).st_mode), 0o755)
            os.chmod(path[0], 0o700)
            return SimpleNamespace(returncode=0)

        with (
            patch.object(xc.subprocess, "run", side_effect=command),
            patch.object(xc.curses, "endwin"),
            patch.object(xc.curses, "raw"),
        ):
            a._action_run_remote("%F", p, xc.VFile("file"))
        self.assertEqual(len(updated), 1, a.err_msg)
        self.assertEqual(updated[0].mode, 0o700)
        self.assertFalse(os.path.exists(path[0]))

    def test_ssh_listing_special_permission_bits(self):
        entry = xc._parse_ls_line("-rwsr-Sr-t 1 user group 1 Jan 1 2000 file")
        self.assertEqual(entry.mode, 0o7745)
        self.assertTrue(entry.executable)


if __name__ == "__main__":
    unittest.main()
