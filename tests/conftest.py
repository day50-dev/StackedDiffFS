"""Pytest configuration for StackedFS tests."""

import os
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


@pytest.fixture
def merge_repo(tmp_path, monkeypatch):
    """Create a merge-layer repository with base and agent overlays.

    Structure:
        repo/
            base/
                shared.txt       "base version"
                base_only.txt    "only in base"
                subdir/
                    base_nested.txt  "base nested"
            agents/
                agent1/
                    shared.txt       "agent version"
                    agent_only.txt   "only in agent"
                    subdir/
                        agent_nested.txt  "agent nested"
    """
    repo = tmp_path / "repo"
    base = repo / "base"
    agents = repo / "agents"
    agent_dir = agents / "agent1"

    base.mkdir(parents=True)
    agents.mkdir()
    agent_dir.mkdir()

    (base / "shared.txt").write_text("base version")
    (base / "base_only.txt").write_text("only in base")

    (agent_dir / "shared.txt").write_text("agent version")
    (agent_dir / "agent_only.txt").write_text("only in agent")

    sub = base / "subdir"
    sub.mkdir()
    (sub / "base_nested.txt").write_text("base nested")

    agent_sub = agent_dir / "subdir"
    agent_sub.mkdir()
    (agent_sub / "agent_nested.txt").write_text("agent nested")

    monkeypatch.setenv("STACKEDFS_SOURCE", str(repo))
    monkeypatch.setenv("AGENT_ID", "agent1")

    return repo
