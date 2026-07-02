"""Echo/debug layer for StackedFS.

Logs all file operations to stderr. Useful for debugging and understanding
how layers interact with FUSE operations.

Usage:
    stackedfs mount -l examples/echo_layer.py /real/path /mnt/test
"""

import sys


def log(hook: str, path: str, extra: str = ""):
    msg = f"[echo_layer] {hook}: {path}"
    if extra:
        msg += f" ({extra})"
    print(msg, file=sys.stderr)


def init():
    log("init", "/")


def pre_open(path: str) -> None:
    log("pre_open", path)


def pre_read(path: str) -> None:
    log("pre_read", path)


def post_read(path: str, data: bytes) -> None:
    log("post_read", path, f"{len(data)} bytes")


def pre_write(path: str, data: bytes) -> None:
    log("pre_write", path, f"{len(data)} bytes")


def post_write(path: str) -> None:
    log("post_write", path)


def pre_getattr(path: str) -> None:
    log("pre_getattr", path)


def post_getattr(path: str, attr) -> None:
    log("post_getattr", path, f"mode={attr.st_mode:o}" if hasattr(attr, 'st_mode') else "")


def pre_readdir(path: str) -> None:
    log("pre_readdir", path)


def post_readdir(path: str, entries: list) -> None:
    log("post_readdir", path, f"{len(entries)} entries")


def pre_create(path: str) -> None:
    log("pre_create", path)


def post_create(path: str) -> None:
    log("post_create", path)


def pre_unlink(path: str) -> None:
    log("pre_unlink", path)


def post_unlink(path: str) -> None:
    log("post_unlink", path)
