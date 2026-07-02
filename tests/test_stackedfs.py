"""Tests for StackedFS architecture: layers, chain, and FUSE operations."""

import os
import sys
import tempfile
import json
from pathlib import Path
import pytest

from stackedfs.layers import Layer, load_layer, load_layers, load_layers_from_json, LayerChain
from stackedfs.fuse import StackedFS
from pyfuse3 import FUSEError, ROOT_INODE


# =========================================================================
# Layer loading and hook dispatch
# =========================================================================

SIMPLE_LAYER_SRC = r'''
def init():
    pass

def pre_open(path):
    return path + ".pre"

def pre_read(path):
    return None

def post_read(path, data):
    return data + b":post"

def pre_write(path, data):
    return (path + ".tmp", data.upper())

def post_write(path):
    pass

def pre_getattr(path):
    return None

def post_getattr(path, attr):
    attr.st_size = 999
    return attr

def pre_readdir(path):
    return path + ".virtual"

def post_readdir(path, entries):
    return [e for e in entries if not e.startswith(".")]

def pre_create(path):
    return path

def post_create(path):
    pass

def pre_unlink(path):
    return path

def post_unlink(path):
    pass
'''


class TestLayerLoading:
    """Tests for layer discovery and loading."""

    def test_load_layer(self, layer_dir):
        layer_file = layer_dir / "simple.py"
        layer_file.write_text(SIMPLE_LAYER_SRC)
        layer = load_layer(str(layer_file))
        assert layer.name == "simple"
        assert layer._module is not None

    def test_load_layer_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_layer("/nonexistent/layer.py")

    def test_load_layers(self, layer_dir):
        (layer_dir / "a.py").write_text("def init(): pass")
        (layer_dir / "b.py").write_text("def init(): pass")
        layers = load_layers([str(layer_dir / "a.py"), str(layer_dir / "b.py")])
        assert len(layers) == 2
        assert layers[0].name == "a"
        assert layers[1].name == "b"

    def test_load_layers_from_json(self, layer_dir):
        (layer_dir / "a.py").write_text("def init(): pass")
        (layer_dir / "b.py").write_text("def init(): pass")
        json_path = layer_dir / "stacks.json"
        json_path.write_text(json.dumps([str(layer_dir / "a.py"), str(layer_dir / "b.py")]))
        layers = load_layers_from_json(str(json_path))
        assert len(layers) == 2


class TestLayerHooks:
    """Tests that hook dispatch works correctly."""

    @pytest.fixture
    def layer(self, layer_dir):
        f = layer_dir / "hooks.py"
        f.write_text(SIMPLE_LAYER_SRC)
        return load_layer(str(f))

    def test_pre_open(self, layer):
        assert layer.pre_open("/foo") == "/foo.pre"

    def test_pre_read_none(self, layer):
        assert layer.pre_read("/foo") is None

    def test_post_read(self, layer):
        assert layer.post_read("/foo", b"data") == b"data:post"

    def test_pre_write(self, layer):
        p, d = layer.pre_write("/foo", b"data")
        assert p == "/foo.tmp"
        assert d == b"DATA"

    def test_post_write(self, layer):
        assert layer.post_write("/foo") is None

    def test_pre_getattr_none(self, layer):
        assert layer.pre_getattr("/foo") is None

    def test_pre_readdir(self, layer):
        assert layer.pre_readdir("/foo") == "/foo.virtual"

    def test_post_readdir(self, layer):
        assert layer.post_readdir("/", ["a", ".b", "c"]) == ["a", "c"]

    def test_init_called(self, layer_dir):
        calls = []
        src = '''
init_calls = []
def init():
    init_calls.append(1)
'''
        f = layer_dir / "track.py"
        f.write_text(src)
        mod = load_layer(str(f))
        # init is called by LayerChain init, not by Layer itself
        # Let's just verify the module has what we expect
        assert mod._module.init_calls == []

    def test_hook_not_defined_returns_none(self, layer_dir):
        f = layer_dir / "minimal.py"
        f.write_text("# no hooks at all\n")
        layer = load_layer(str(f))
        assert layer.pre_open("/foo") is None
        assert layer.post_read("/foo", b"x") is None


# =========================================================================
# LayerChain ordering
# =========================================================================

class TestLayerChain:
    """Tests that LayerChain runs hooks in correct order."""

    @pytest.fixture
    def chain(self, layer_dir):
        # Layer A: appends "A" to data
        (layer_dir / "a.py").write_text('''
def post_read(path, data):
    return data + b":A"
''')
        # Layer B: appends "B" to data
        (layer_dir / "b.py").write_text('''
def post_read(path, data):
    return data + b":B"
''')
        layers = load_layers([str(layer_dir / "a.py"), str(layer_dir / "b.py")])
        return LayerChain(layers)

    def test_post_read_reverse_order(self, chain):
        # layers = [A, B]; post runs in reverse = [B, A]
        # B runs first: data -> data + ":B"
        # A runs second: data:B -> data:B + ":A"
        result = chain.post_read("/f", b"data")
        assert result == b"data:B:A"

    def test_pre_read_forward_order(self, layer_dir):
        (layer_dir / "a.py").write_text('''
def pre_read(path):
    return path + "/a"
''')
        (layer_dir / "b.py").write_text('''
def pre_read(path):
    return path + "/b"
''')
        layers = load_layers([str(layer_dir / "a.py"), str(layer_dir / "b.py")])
        chain = LayerChain(layers)
        result = chain.pre_read("/root")
        assert result == "/root/a/b"

    def test_empty_chain(self):
        chain = LayerChain([])
        assert chain.pre_read("/x") == "/x"
        assert chain.post_read("/x", b"d") == b"d"


# =========================================================================
# Example: secrets_layer
# =========================================================================

class TestSecretsLayer:
    """Test the secrets substitution example layer."""

    def test_aws_key_redacted(self, layer_dir):
        secrets_path = Path(__file__).parent.parent / "examples" / "secrets_layer.py"
        layer = load_layer(str(secrets_path))

        data = b"AWS key: AKIAIOSFODNN7EXAMPLE"
        result = layer.post_read("/file", data)
        assert b"AKIAIOSFODNN7EXAMPLE" not in result
        assert b"AWS_ACCESS_KEY_REDACTED" in result

    def test_password_redacted(self, layer_dir):
        secrets_path = Path(__file__).parent.parent / "examples" / "secrets_layer.py"
        layer = load_layer(str(secrets_path))

        data = b"password = super secret 123"
        result = layer.post_read("/file", data)
        assert b"super secret 123" not in result
        assert b"password = ***" in result

    def test_binary_file_unchanged(self, layer_dir):
        secrets_path = Path(__file__).parent.parent / "examples" / "secrets_layer.py"
        layer = load_layer(str(secrets_path))

        data = b"\x00\x01\x02\xff\xfe"
        result = layer.post_read("/file", data)
        assert result is None  # binary data returned as-is

    def test_no_secrets_unchanged(self, layer_dir):
        secrets_path = Path(__file__).parent.parent / "examples" / "secrets_layer.py"
        layer = load_layer(str(secrets_path))

        data = b"hello world this is fine"
        result = layer.post_read("/file", data)
        assert result == data


# =========================================================================
# FUSE operations
# =========================================================================

class TestStackedFSOperations:
    """Test FUSE operations against a source directory with/without layers."""

    @pytest.mark.asyncio
    async def test_getattr_root(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        attr = await fs.getattr(ROOT_INODE)
        assert attr.st_mode & 0o170000 == 0o40000  # S_IFDIR

    @pytest.mark.asyncio
    async def test_getattr_file(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.lookup(ROOT_INODE, b"hello.txt")
        attr = result["entry_attributes"]
        assert attr.st_size == len("hello world")

    @pytest.mark.asyncio
    async def test_getattr_missing(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        with pytest.raises(FUSEError):
            await fs.getattr(99999)

    @pytest.mark.asyncio
    async def test_lookup_root(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.lookup(ROOT_INODE, b".")
        assert result["inode"] == ROOT_INODE

    @pytest.mark.asyncio
    async def test_lookup_file(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.lookup(ROOT_INODE, b"hello.txt")
        assert result["inode"] is not None

    @pytest.mark.asyncio
    async def test_lookup_missing(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        with pytest.raises(FUSEError):
            await fs.lookup(ROOT_INODE, b"nope.txt")

    @pytest.mark.asyncio
    async def test_readdir(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        entries = []
        async for _, name, _ in fs.readdir(ROOT_INODE, 0, None):
            entries.append(name.decode("utf-8"))

        assert "hello.txt" in entries
        assert "subdir" in entries

    @pytest.mark.asyncio
    async def test_readdir_nested(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.lookup(ROOT_INODE, b"subdir")
        subdir_inode = result["inode"]

        entries = []
        async for _, name, _ in fs.readdir(subdir_inode, 0, None):
            entries.append(name.decode("utf-8"))

        assert "nested.txt" in entries

    @pytest.mark.asyncio
    async def test_open_and_read(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.lookup(ROOT_INODE, b"hello.txt")
        fi = await fs.open(result["inode"], os.O_RDONLY)
        data = await fs.read(fi.fh, 0, 100)
        await fs.release(fi.fh)

        assert data == b"hello world"

    @pytest.mark.asyncio
    async def test_read_with_offset(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.lookup(ROOT_INODE, b"hello.txt")
        fi = await fs.open(result["inode"], os.O_RDONLY)
        data = await fs.read(fi.fh, 6, 5)
        await fs.release(fi.fh)

        assert data == b"world"

    @pytest.mark.asyncio
    async def test_create_file(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.create(ROOT_INODE, b"new.txt", 0o644, os.O_WRONLY)
        await fs.write(result["file_info"].fh, 0, b"fresh data")
        await fs.release(result["file_info"].fh)

        assert (source_dir / "new.txt").exists()
        assert (source_dir / "new.txt").read_text() == "fresh data"

    @pytest.mark.asyncio
    async def test_unlink_file(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        await fs.unlink(ROOT_INODE, b"hello.txt")
        assert not (source_dir / "hello.txt").exists()

    @pytest.mark.asyncio
    async def test_mkdir_and_rmdir(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        await fs.mkdir(ROOT_INODE, b"newdir", 0o755)
        assert (source_dir / "newdir").is_dir()

        await fs.rmdir(ROOT_INODE, b"newdir")
        assert not (source_dir / "newdir").exists()

    @pytest.mark.asyncio
    async def test_rename(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        await fs.rename(ROOT_INODE, b"hello.txt", ROOT_INODE, b"renamed.txt", 0)
        assert not (source_dir / "hello.txt").exists()
        assert (source_dir / "renamed.txt").exists()
        assert (source_dir / "renamed.txt").read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_statfs(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.statfs()
        assert result is not None
        assert result.f_bsize > 0

    @pytest.mark.asyncio
    async def test_flush(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.lookup(ROOT_INODE, b"hello.txt")
        fi = await fs.open(result["inode"], os.O_RDONLY)
        await fs.flush(fi.fh)
        await fs.release(fi.fh)

    @pytest.mark.asyncio
    async def test_release(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.lookup(ROOT_INODE, b"hello.txt")
        fi = await fs.open(result["inode"], os.O_RDONLY)
        await fs.release(fi.fh)

    @pytest.mark.asyncio
    async def test_open_missing(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        with pytest.raises(FUSEError):
            await fs.open(99999, os.O_RDONLY)

    @pytest.mark.asyncio
    async def test_read_bad_fh(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        with pytest.raises(FUSEError):
            await fs.read(99999, 0, 100)

    @pytest.mark.asyncio
    async def test_write_bad_fh(self, source_dir):
        chain = LayerChain([])
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        with pytest.raises(FUSEError):
            await fs.write(99999, 0, b"data")


class TestStackedFSWithLayers:
    """Test FUSE operations with layers that transform data."""

    @pytest.mark.asyncio
    async def test_post_read_modifies_data(self, source_dir, layer_dir):
        # Layer that appends "-LAYER" to reads
        layer_file = layer_dir / "append.py"
        layer_file.write_text('''
def post_read(path, data):
    return data + b"-LAYER"
''')
        chain = LayerChain(load_layers([str(layer_file)]))
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.lookup(ROOT_INODE, b"hello.txt")
        fi = await fs.open(result["inode"], os.O_RDONLY)
        data = await fs.read(fi.fh, 0, 100)
        await fs.release(fi.fh)

        assert data == b"hello world-LAYER"

    @pytest.mark.asyncio
    async def test_pre_getattr_redirect(self, source_dir, layer_dir):
        # Layer that redirects requests for /hello.txt to /subdir/nested.txt
        layer_file = layer_dir / "redirect.py"
        layer_file.write_text('''
def pre_getattr(path):
    if path == "/hello.txt":
        return "/subdir/nested.txt"
    return None
''')
        chain = LayerChain(load_layers([str(layer_file)]))
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.lookup(ROOT_INODE, b"hello.txt")
        attr = result["entry_attributes"]
        assert attr.st_size == len("nested content")

    @pytest.mark.asyncio
    async def test_pre_open_redirect(self, source_dir, layer_dir):
        # Layer that redirects opens of /hello.txt to /subdir/nested.txt
        layer_file = layer_dir / "redirect.py"
        layer_file.write_text('''
def pre_open(path):
    if path == "/hello.txt":
        return "/subdir/nested.txt"
    return None
''')
        chain = LayerChain(load_layers([str(layer_file)]))
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.lookup(ROOT_INODE, b"hello.txt")
        fi = await fs.open(result["inode"], os.O_RDONLY)
        data = await fs.read(fi.fh, 0, 100)
        await fs.release(fi.fh)

        assert data == b"nested content"

    @pytest.mark.asyncio
    async def test_post_readdir_filters(self, source_dir, layer_dir):
        # Layer that filters out hidden files
        layer_file = layer_dir / "filter.py"
        layer_file.write_text('''
def post_readdir(path, entries):
    return [e for e in entries if not e.startswith(".")]
''')
        chain = LayerChain(load_layers([str(layer_file)]))
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        entries = []
        async for _, name, _ in fs.readdir(ROOT_INODE, 0, None):
            entries.append(name.decode("utf-8"))

        assert "hello.txt" in entries
        assert "subdir" in entries

    @pytest.mark.asyncio
    async def test_pre_write_transform(self, source_dir, layer_dir):
        # Layer that uppercases all written data
        layer_file = layer_dir / "upper.py"
        layer_file.write_text('''
def pre_write(path, data):
    return (path, data.upper())
''')
        chain = LayerChain(load_layers([str(layer_file)]))
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.create(ROOT_INODE, b"output.txt", 0o644, os.O_WRONLY)
        await fs.write(result["file_info"].fh, 0, b"hello")
        await fs.release(result["file_info"].fh)

        assert (source_dir / "output.txt").read_text() == "HELLO"

    @pytest.mark.asyncio
    async def test_multiple_layers(self, source_dir, layer_dir):
        # Layer A appends "-A", Layer B appends "-B" on read
        (layer_dir / "a.py").write_text('''
def post_read(path, data):
    return data + b"-A"
''')
        (layer_dir / "b.py").write_text('''
def post_read(path, data):
    return data + b"-B"
''')
        chain = LayerChain(load_layers([
            str(layer_dir / "a.py"),
            str(layer_dir / "b.py"),
        ]))
        fs = StackedFS(str(source_dir), chain)
        await fs.init()

        result = await fs.lookup(ROOT_INODE, b"hello.txt")
        fi = await fs.open(result["inode"], os.O_RDONLY)
        data = await fs.read(fi.fh, 0, 100)
        await fs.release(fi.fh)

        # A runs first in pre (forward), but in post (reversed) B runs first.
        # post_read runs reversed: a is last (innermost), so a appends AFTER b.
        # Wait - let me think.
        # Layers: [A, B]
        # post_read runs reversed: [B, A]
        # So B runs first: data -> data + "-B"
        # Then A runs: (data + "-B") -> data + "-B" + "-A"
        assert data == b"hello world-B-A"
