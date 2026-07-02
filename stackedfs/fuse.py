#!/usr/bin/env python3
"""StackedFS FUSE implementation using pyfuse3."""

import os
import errno
import stat as stat_module
import time
from pathlib import Path
from pyfuse3 import (
    Operations, EntryAttributes, FileInfo, ROOT_INODE, FUSEError, StatvfsData
)
from pyfuse3 import init as pyfuse3_init
from pyfuse3 import main as pyfuse3_main
from pyfuse3 import close as pyfuse3_close
from .layers import LayerChain


class StackedFS(Operations):
    """A FUSE filesystem that mirrors a source directory through a layer chain."""

    def __init__(self, source_path: str, layer_chain: LayerChain):
        self.source_path = Path(source_path).resolve()
        self.layer_chain = layer_chain

        self._inode_counter = ROOT_INODE
        self._path_to_inode: dict[str, int] = {}
        self._inode_to_path: dict[int, str] = {}
        self._fh_counter = 0
        self._open_files: dict[int, tuple] = {}

        self._path_to_inode["/"] = ROOT_INODE
        self._inode_to_path[ROOT_INODE] = "/"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _real_path(self, virtual_path: str) -> Path:
        rel = virtual_path.lstrip("/")
        return self.source_path / rel

    def _inode(self, path: str) -> int:
        path = path.rstrip("/") or "/"
        if path in self._path_to_inode:
            return self._path_to_inode[path]
        self._inode_counter += 1
        self._path_to_inode[path] = self._inode_counter
        self._inode_to_path[self._inode_counter] = path
        return self._inode_counter

    def _path(self, inode: int) -> str | None:
        return self._inode_to_path.get(inode)

    def _build_attr(self, virtual_path: str) -> EntryAttributes | None:
        real = self._real_path(virtual_path)
        try:
            st = real.stat()
        except OSError:
            return None
        attr = EntryAttributes()
        attr.st_mode = st.st_mode
        attr.st_nlink = st.st_nlink
        attr.st_uid = st.st_uid
        attr.st_gid = st.st_gid
        attr.st_size = st.st_size
        attr.st_atime_ns = int(st.st_atime * 1e9)
        attr.st_mtime_ns = int(st.st_mtime * 1e9)
        attr.st_ctime_ns = int(st.st_ctime * 1e9)
        attr.st_blksize = getattr(st, 'st_blksize', 4096)
        attr.st_blocks = getattr(st, 'st_blocks', (st.st_size + 511) // 512)
        attr.st_ino = self._inode(virtual_path)
        return attr

    def _dir_entries(self, virtual_path: str) -> list[str]:
        real = self._real_path(virtual_path)
        try:
            return sorted(os.listdir(real))
        except OSError:
            return []

    # ------------------------------------------------------------------
    # pyfuse3 callbacks
    # ------------------------------------------------------------------

    async def init(self):
        pass

    async def destroy(self):
        for fh, (fobj, _) in self._open_files.items():
            try:
                fobj.close()
            except Exception:
                pass
        self._open_files.clear()

    # -- getattr --

    async def getattr(self, inode, ctx=None):
        path = self._path(inode)
        if path is None:
            raise FUSEError(errno.ENOENT)
        path = self.layer_chain.pre_getattr(path)
        attr = self._build_attr(path)
        if attr is None:
            raise FUSEError(errno.ENOENT)
        attr.st_ino = inode
        attr = self.layer_chain.post_getattr(path, attr)
        return attr

    # -- lookup --

    async def lookup(self, parent_inode, name, ctx=None):
        parent_path = self._path(parent_inode)
        if parent_path is None:
            raise FUSEError(errno.ENOENT)

        name_str = name.decode("utf-8")
        if name_str == ".":
            path = parent_path
            inode = parent_inode
        elif name_str == "..":
            if parent_path == "/":
                path = parent_path
                inode = parent_inode
            else:
                parts = parent_path.rstrip("/").split("/")
                path = "/".join(parts[:-1]) or "/"
                inode = self._inode(path)
        else:
            path = parent_path.rstrip("/") + "/" + name_str
            if parent_path == "/":
                path = "/" + name_str
            inode = self._inode(path)

        attr = await self.getattr(inode)
        return {"entry_attributes": attr, "inode": inode}

    # -- readdir --

    async def opendir(self, inode, ctx=None):
        return inode

    async def readdir(self, fh, start_id, token):
        path = self._path(fh)
        if path is None:
            raise FUSEError(errno.ENOENT)

        path = self.layer_chain.pre_readdir(path)
        entries = self._dir_entries(path)
        entries = self.layer_chain.post_readdir(path, entries)

        for idx, entry in enumerate(entries, start=1):
            if idx < start_id:
                continue
            entry_path = path.rstrip("/") + "/" + entry
            if path == "/":
                entry_path = "/" + entry
            entry_path = self.layer_chain.pre_getattr(entry_path)
            attr = self._build_attr(entry_path)
            if attr is None:
                continue
            yield (idx, entry.encode("utf-8"), attr)

    async def releasedir(self, fh):
        pass

    # -- open / read / write / release --

    async def open(self, inode, flags, ctx=None):
        path = self._path(inode)
        if path is None:
            raise FUSEError(errno.ENOENT)
        path = self.layer_chain.pre_open(path)

        real = self._real_path(path)
        if not real.exists():
            raise FUSEError(errno.ENOENT)

        self._fh_counter += 1
        fh = self._fh_counter
        mode = "rb"
        if flags & os.O_WRONLY:
            mode = "r+b"
        elif flags & os.O_RDWR:
            mode = "r+b"
        fobj = real.open(mode)
        self._open_files[fh] = (fobj, path)

        fi = FileInfo()
        fi.fh = fh
        return fi

    async def read(self, fh, off, size):
        if fh not in self._open_files:
            raise FUSEError(errno.EBADF)
        fobj, path = self._open_files[fh]
        fobj.seek(off)
        data = fobj.read(size)
        data = self.layer_chain.post_read(path, data)
        return data

    async def write(self, fh, off, buf):
        if fh not in self._open_files:
            raise FUSEError(errno.EBADF)
        fobj, path = self._open_files[fh]
        payload = bytes(buf)

        new_path, new_data = self.layer_chain.pre_write(path, payload)

        if new_path != path:
            fobj.close()
            real = self._real_path(new_path)
            real.parent.mkdir(parents=True, exist_ok=True)
            try:
                fobj = real.open("r+b")
            except FileNotFoundError:
                fobj = real.open("w+b")
            self._open_files[fh] = (fobj, new_path)

        fobj.seek(off)
        fobj.write(new_data)
        self.layer_chain.post_write(new_path if new_path != path else path)
        return len(buf)

    async def release(self, fh):
        if fh in self._open_files:
            try:
                self._open_files[fh][0].close()
            except Exception:
                pass
            del self._open_files[fh]

    async def flush(self, fh):
        if fh in self._open_files:
            self._open_files[fh][0].flush()

    async def fsync(self, fh, datasync):
        if fh in self._open_files:
            self._open_files[fh][0].sync()

    # -- create --

    async def create(self, parent_inode, name, mode, flags, ctx=None):
        parent_path = self._path(parent_inode)
        if parent_path is None:
            raise FUSEError(errno.ENOENT)

        name_str = name.decode("utf-8")
        path = parent_path.rstrip("/") + "/" + name_str
        if parent_path == "/":
            path = "/" + name_str
        path = self.layer_chain.pre_create(path)

        real = self._real_path(path)
        real.parent.mkdir(parents=True, exist_ok=True)
        fobj = real.open("w+b")

        self._fh_counter += 1
        fh = self._fh_counter
        self._open_files[fh] = (fobj, path)

        inode = self._inode(path)
        self.layer_chain.post_create(path)

        fi = FileInfo()
        fi.fh = fh
        fi.direct_io = True

        attr = EntryAttributes()
        attr.st_mode = mode
        attr.st_nlink = 1
        attr.st_size = 0

        return {"entry_attributes": attr, "inode": inode, "file_info": fi}

    # -- unlink --

    async def unlink(self, parent_inode, name, ctx=None):
        parent_path = self._path(parent_inode)
        if parent_path is None:
            raise FUSEError(errno.ENOENT)

        name_str = name.decode("utf-8")
        path = parent_path.rstrip("/") + "/" + name_str
        if parent_path == "/":
            path = "/" + name_str
        path = self.layer_chain.pre_unlink(path)

        real = self._real_path(path)
        if real.exists():
            real.unlink()

        self.layer_chain.post_unlink(path)
        if path in self._path_to_inode:
            inode = self._path_to_inode[path]
            del self._path_to_inode[path]
            del self._inode_to_path[inode]

    # -- rename --

    async def rename(self, parent_inode_old, name_old, parent_inode_new, name_new, flags, ctx=None):
        old_parent = self._path(parent_inode_old)
        new_parent = self._path(parent_inode_new)
        if old_parent is None or new_parent is None:
            raise FUSEError(errno.ENOENT)

        old_name = name_old.decode("utf-8")
        new_name = name_new.decode("utf-8")

        old_path = old_parent.rstrip("/") + "/" + old_name
        if old_parent == "/":
            old_path = "/" + old_name
        new_path = new_parent.rstrip("/") + "/" + new_name
        if new_parent == "/":
            new_path = "/" + new_name

        real_old = self._real_path(old_path)
        real_new = self._real_path(new_path)
        if real_old.exists():
            real_new.parent.mkdir(parents=True, exist_ok=True)
            real_old.rename(real_new)

        if old_path in self._path_to_inode:
            node = self._path_to_inode.pop(old_path)
            self._path_to_inode[new_path] = node
            self._inode_to_path[node] = new_path

    # -- mkdir / rmdir --

    async def mkdir(self, parent_inode, name, mode, ctx=None):
        parent_path = self._path(parent_inode)
        if parent_path is None:
            raise FUSEError(errno.ENOENT)

        name_str = name.decode("utf-8")
        path = parent_path.rstrip("/") + "/" + name_str
        if parent_path == "/":
            path = "/" + name_str

        real = self._real_path(path)
        real.mkdir(parents=True, exist_ok=True)
        inode = self._inode(path)

        attr = EntryAttributes()
        attr.st_mode = mode | stat_module.S_IFDIR
        attr.st_nlink = 2
        attr.st_size = 4096

        return {"entry_attributes": attr, "inode": inode}

    async def rmdir(self, parent_inode, name, ctx=None):
        parent_path = self._path(parent_inode)
        if parent_path is None:
            raise FUSEError(errno.ENOENT)

        name_str = name.decode("utf-8")
        path = parent_path.rstrip("/") + "/" + name_str
        if parent_path == "/":
            path = "/" + name_str

        real = self._real_path(path)
        if real.exists():
            real.rmdir()

        if path in self._path_to_inode:
            node = self._path_to_inode[path]
            del self._path_to_inode[path]
            del self._inode_to_path[node]

    # -- symlink / readlink --

    async def symlink(self, parent_inode, name, target, ctx=None):
        parent_path = self._path(parent_inode)
        if parent_path is None:
            raise FUSEError(errno.ENOENT)

        name_str = name.decode("utf-8")
        path = parent_path.rstrip("/") + "/" + name_str
        if parent_path == "/":
            path = "/" + name_str

        real = self._real_path(path)
        real.symlink_to(target.decode("utf-8"))
        inode = self._inode(path)

        attr = EntryAttributes()
        attr.st_mode = stat_module.S_IFLNK | 0o777
        attr.st_nlink = 1
        attr.st_size = len(target)

        return {"entry_attributes": attr, "inode": inode}

    async def readlink(self, inode, ctx=None):
        path = self._path(inode)
        if path is None:
            raise FUSEError(errno.ENOENT)

        real = self._real_path(path)
        if not real.is_symlink():
            raise FUSEError(errno.EINVAL)

        target = os.readlink(real)
        return target.encode("utf-8")

    # -- statfs --

    async def statfs(self, ctx=None):
        try:
            st = os.statvfs(self.source_path)
        except OSError:
            st = os.statvfs("/")

        fs = StatvfsData()
        fs.f_bsize = st.f_bsize
        fs.f_frsize = st.f_frsize
        fs.f_blocks = st.f_blocks
        fs.f_bfree = st.f_bfree
        fs.f_bavail = st.f_bavail
        fs.f_files = st.f_files
        fs.f_ffree = st.f_ffree
        fs.f_namemax = st.f_namemax
        return fs

    # -- xattr (unsupported) --

    async def setxattr(self, inode, name, value, ctx=None):
        raise FUSEError(errno.ENOTSUP)

    async def getxattr(self, inode, name, ctx=None):
        raise FUSEError(errno.ENOATTR)

    async def listxattr(self, inode, ctx=None):
        return []

    async def removexattr(self, inode, name, ctx=None):
        raise FUSEError(errno.ENOTSUP)


# ------------------------------------------------------------------
# Public helpers
# ------------------------------------------------------------------

def mount(source_path: str, mount_point: str, layers: list[str],
          foreground: bool = False, debug: bool = False):
    """Mount a layered FUSE filesystem."""
    from .layers import load_layers, LayerChain

    resolved = str(Path(source_path).resolve())
    os.environ["STACKEDFS_SOURCE"] = resolved

    chain = LayerChain(load_layers(layers))
    fs = StackedFS(resolved, chain)

    opts = pyfuse3.default_options
    if debug:
        opts.append("debug")

    pyfuse3_init(fs, mount_point, opts)
    try:
        pyfuse3_main(max_tasks=1)
    finally:
        pyfuse3_close(unmount=True)


def unmount(mount_point: str):
    """Unmount a StackedFS filesystem."""
    import subprocess
    subprocess.run(["fusermount", "-u", mount_point], check=True)
