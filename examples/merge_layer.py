"""Merge layer — overlay filesystem with agent isolation and conflict detection.

Mirrors the original StackedDiffFS functionality as a layer.

Expects a repository directory containing:
  base/              — original (shared) files
  agents/            — agent-specific overlays, one subdirectory per agent

The active agent is selected via the AGENT_ID environment variable.
Writes go to the active agent's overlay; reads merge base + agent.
Conflicts are detected when the base file changes between reads and writes.

Usage:
    export AGENT_ID=my-agent
    stackedfs mount -l examples/merge_layer.py /path/to/repo /mnt/point
"""

import os
import hashlib
import time


_file_hashes: dict[str, str] = {}
_conflicts: list[dict] = []


# ---------------------------------------------------------------------------
# lazy helpers (evaluated per-call so env-var changes are picked up)
# ---------------------------------------------------------------------------

def _agent_id() -> str:
    return os.environ.get("AGENT_ID", "default")


def _src() -> str:
    return os.environ.get("STACKEDFS_SOURCE", ".")


def _agent_sub(path: str) -> str:
    pid = f"/agents/{_agent_id()}"
    if path.startswith(pid):
        return path
    return f"{pid}{path}" if path.startswith("/") else path


def _base_sub(path: str) -> str:
    if path == "/base" or path.startswith("/base/"):
        return path
    return f"/base{path}" if path.startswith("/") else path


def _exists(sub_path: str) -> bool:
    return os.path.exists(os.path.join(_src(), sub_path.lstrip("/")))


def _resolve(path: str) -> str | None:
    """Resolve a path to the overlay that contains it.

    Handles both bare virtual paths (/foo) and already-prefixed paths
    (/base/foo, /agents/agent1/foo) by normalising first.
    """
    normal = _virtual_path(path)
    if _exists(_agent_sub(normal)):
        return _agent_sub(normal)
    if _exists(_base_sub(normal)):
        return _base_sub(normal)
    return None


def _hash_of(sub_path: str) -> str | None:
    full = os.path.join(_src(), sub_path.lstrip("/"))
    try:
        with open(full, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _virtual_path(resolved: str) -> str:
    """Strip /base/ or /agents/<id>/ prefix to recover the virtual path."""
    for prefix in (f"/agents/{_agent_id()}", "/base/"):
        if resolved.startswith(prefix):
            rest = resolved[len(prefix):]
            return "/" + rest.lstrip("/") if rest else "/"
    if resolved == "/base":
        return "/"
    return resolved


def _is_internal(entry: str) -> bool:
    return entry in ("base", "agents", "agents.json", "work")


# ---------------------------------------------------------------------------
# hooks
# ---------------------------------------------------------------------------

def pre_getattr(path: str) -> str | None:
    return _resolve(path)


def pre_open(path: str) -> str | None:
    return _resolve(path)


def pre_readdir(path: str) -> str | None:
    if path == "/":
        return None
    return _resolve(path)


def post_readdir(path: str, entries: list[str]) -> list[str] | None:
    """Merge directory entries from both overlays."""
    if path == "/":
        entries = [e for e in entries if not _is_internal(e)]

    rel = _virtual_path(path).lstrip("/")
    merged = set(entries)

    for sub_dir in (f"/base/", f"/agents/{_agent_id()}/"):
        full = os.path.join(_src(), sub_dir.lstrip("/"), rel)
        if os.path.isdir(full):
            for e in os.listdir(full):
                if not e.startswith(".") and not _is_internal(e):
                    merged.add(e)

    return sorted(merged)


def post_read(path: str, data: bytes) -> None:
    """Record file hash for conflict detection."""
    resolved = _resolve(_virtual_path(path))
    if resolved:
        h = _hash_of(resolved)
        if h is not None:
            _file_hashes[_virtual_path(path)] = h
    return None


def pre_write(path: str, data: bytes) -> tuple[str, bytes] | None:
    """Detect conflicts before write, then redirect to agent overlay."""
    virtual = _virtual_path(path)

    if virtual in _file_hashes:
        current = _hash_of(_base_sub(virtual))
        if current is not None and current != _file_hashes[virtual]:
            _conflicts.append({
                "path": virtual,
                "agent": _agent_id(),
                "timestamp": time.time(),
                "message": "base file changed since last read",
            })

    return (_agent_sub(virtual), data)


def pre_create(path: str) -> str | None:
    return _agent_sub(_virtual_path(path))


def pre_unlink(path: str) -> str | None:
    ap = _agent_sub(_virtual_path(path))
    return ap if _exists(ap) else None
