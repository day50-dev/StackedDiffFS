"""Pytest configuration for StackedFS tests."""

import tempfile
from pathlib import Path
import pytest


@pytest.fixture
def source_dir(tmp_path):
    """Create a temporary source directory with test files."""
    src = tmp_path / "source"
    src.mkdir()
    (src / "hello.txt").write_text("hello world")
    (src / "subdir").mkdir()
    (src / "subdir" / "nested.txt").write_text("nested content")
    return src


@pytest.fixture
def layer_dir(tmp_path):
    """Create a temporary directory for layer files."""
    d = tmp_path / "layers"
    d.mkdir()
    return d
