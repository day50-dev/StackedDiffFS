"""Layer system for StackedFS - pre/post hook chain for FUSE operations."""

import importlib.util
import json
from pathlib import Path
from typing import Any, Optional


class Layer:
    """A single layer wrapping a module with optional hook functions."""

    def __init__(self, name: str, module: Any):
        self.name = name
        self._module = module

    def _call(self, hook: str, *args):
        fn = getattr(self._module, hook, None)
        return fn(*args) if fn else None

    def pre_open(self, path: str) -> Optional[str]:
        return self._call('pre_open', path)

    def pre_read(self, path: str) -> Optional[str]:
        return self._call('pre_read', path)

    def post_read(self, path: str, data: bytes) -> Optional[bytes]:
        return self._call('post_read', path, data)

    def pre_write(self, path: str, data: bytes) -> Optional[tuple[str, bytes]]:
        return self._call('pre_write', path, data)

    def post_write(self, path: str) -> None:
        self._call('post_write', path)

    def pre_getattr(self, path: str) -> Optional[str]:
        return self._call('pre_getattr', path)

    def post_getattr(self, path: str, attr) -> Optional[Any]:
        return self._call('post_getattr', path, attr)

    def pre_readdir(self, path: str) -> Optional[str]:
        return self._call('pre_readdir', path)

    def post_readdir(self, path: str, entries: list[str]) -> Optional[list[str]]:
        return self._call('post_readdir', path, entries)

    def pre_create(self, path: str) -> Optional[str]:
        return self._call('pre_create', path)

    def post_create(self, path: str) -> None:
        self._call('post_create', path)

    def pre_unlink(self, path: str) -> Optional[str]:
        return self._call('pre_unlink', path)

    def post_unlink(self, path: str) -> None:
        self._call('post_unlink', path)

    def init(self) -> None:
        self._call('init')


def load_layer(path: str) -> Layer:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Layer not found: {path}")
    spec = importlib.util.spec_from_file_location(p.stem, p)
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load layer: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return Layer(p.stem, mod)


def load_layers(specs: list[str]) -> list[Layer]:
    return [load_layer(s) for s in specs]


def load_layers_from_json(json_path: str) -> list[Layer]:
    with open(json_path) as f:
        specs = json.load(f)
    return load_layers(specs)


class LayerChain:
    """Runs hooks through an ordered chain of layers.

    Pre-hooks run forward through the chain; post-hooks run in reverse.
    Each hook can return None to pass through unchanged, or a value to override.
    """

    def __init__(self, layers: list[Layer]):
        self.layers = layers
        for layer in layers:
            layer.init()

    def pre_open(self, path: str) -> str:
        for layer in self.layers:
            r = layer.pre_open(path)
            if r is not None:
                path = r
        return path

    def pre_read(self, path: str) -> str:
        for layer in self.layers:
            r = layer.pre_read(path)
            if r is not None:
                path = r
        return path

    def post_read(self, path: str, data: bytes) -> bytes:
        for layer in reversed(self.layers):
            r = layer.post_read(path, data)
            if r is not None:
                data = r
        return data

    def pre_write(self, path: str, data: bytes) -> tuple[str, bytes]:
        for layer in self.layers:
            r = layer.pre_write(path, data)
            if r is not None:
                path, data = r
        return path, data

    def post_write(self, path: str) -> None:
        for layer in reversed(self.layers):
            layer.post_write(path)

    def pre_getattr(self, path: str) -> str:
        for layer in self.layers:
            r = layer.pre_getattr(path)
            if r is not None:
                path = r
        return path

    def post_getattr(self, path: str, attr) -> Any:
        for layer in reversed(self.layers):
            r = layer.post_getattr(path, attr)
            if r is not None:
                attr = r
        return attr

    def pre_readdir(self, path: str) -> str:
        for layer in self.layers:
            r = layer.pre_readdir(path)
            if r is not None:
                path = r
        return path

    def post_readdir(self, path: str, entries: list[str]) -> list[str]:
        for layer in reversed(self.layers):
            r = layer.post_readdir(path, entries)
            if r is not None:
                entries = r
        return entries

    def pre_create(self, path: str) -> str:
        for layer in self.layers:
            r = layer.pre_create(path)
            if r is not None:
                path = r
        return path

    def post_create(self, path: str) -> None:
        for layer in reversed(self.layers):
            layer.post_create(path)

    def pre_unlink(self, path: str) -> str:
        for layer in self.layers:
            r = layer.pre_unlink(path)
            if r is not None:
                path = r
        return path

    def post_unlink(self, path: str) -> None:
        for layer in reversed(self.layers):
            layer.post_unlink(path)
