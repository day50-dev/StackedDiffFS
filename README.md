# StackedFS

**A layered FUSE filesystem with pre/post hook layers.**

StackedFS is a FUSE-based filesystem that mirrors a real directory through a configurable chain of Python "layers". Each layer can intercept file operations (read, write, open, getattr, readdir, etc.) with pre and post hooks — enabling transparent content transformation, redaction, logging, access control, and more.

## Rationale

AI agents often need to read sensitive files (`.env`, configs with secrets, API keys) during development. StackedFS makes it possible to surface a **sanitized view** of a real directory by running file data through a layer chain:

- A **secrets layer** replaces real credentials with substitutes on-the-fly
- A **logging layer** records every filesystem access
- A **filter layer** can hide or redirect certain paths
- Layers compose — chain them together for powerful pipelines

Beyond security, layers open the door to exposing other datastores as filesystem primitives (e.g., a Redis key-value store as a `/redis/` directory, or an agent's conversational context as editable files).

## How It Works

```
stackedfs mount -l secrets_layer.py -l echo_layer.py /real/project /mnt/safe
```

The command above:
1. Loads `secrets_layer.py` and `echo_layer.py` as the layer chain
2. Mirrors `/real/project` at the mount point `/mnt/safe`
3. Every file read passes through both layers' hooks — secrets get redacted, operations get logged

```
User/program  →  [echo pre]  →  [secrets pre]  →  [FUSE]  →  [secrets post]  →  [echo post]  →  result
```

Pre-hooks run forward through the chain (outermost first); post-hooks run in reverse (innermost first).

## Features

- **Mirror any directory** through a FUSE mount point
- **Pluggable Python layers** — each layer is a `.py` file with optional hook functions
- **Pre/post hook model** — transform paths, filter data, intercept operations
- **Dynamic loading** — layers loaded at mount time via `-l` flag or JSON config
- **Composable** — multiple layers chain together in order
- **FUSE-based** — works with any tool that reads files through the mounted filesystem

## Quick Start

### 1. Validate Layers

```bash
stackedfs -l examples/secrets_layer.py
# Loaded 1 layer(s):
#   - secrets_layer
# Layers validated successfully.
```

### 2. Mount the Filesystem

```bash
# Mount with a single layer
stackedfs mount -l examples/secrets_layer.py /real/project /mnt/safe

# Mount with multiple layers
stackedfs mount -l examples/secrets_layer.py -l examples/echo_layer.py /real/project /mnt/safe

# Run in foreground with debug
stackedfs mount -l examples/secrets_layer.py /real/project /mnt/safe -f -d
```

### 3. Use the Mount Point

Files at the mount point mirror the source directory, but data passes through all enabled layers:

```bash
# A .env file with real secrets
cat /real/project/.env
# API_KEY=sk-abc123...
# PASSWORD=hunter2

# Read through the mount point — secrets redacted
cat /mnt/safe/.env
# API_KEY=<OPENAI_API_KEY_REDACTED>
# PASSWORD=***
```

### 4. Unmount

```bash
stackedfs unmount /mnt/safe
```

### Layer Configuration via JSON

```json
["examples/secrets_layer.py", "examples/echo_layer.py"]
```

```bash
stackedfs mount -f stacks.json /real/project /mnt/safe
```

## Layer API

Each layer is a Python file that defines any combination of these hook functions:

### Hooks

| Hook | Signature | Called When | Pre/Post |
|------|-----------|-------------|----------|
| `init` | `()` | Layer is loaded | — |
| `pre_open` | `(path) -> str\|None` | Before opening a file | Pre |
| `pre_read` | `(path) -> str\|None` | Before reading an open file | Pre |
| `post_read` | `(path, data) -> bytes\|None` | After reading from a file | Post |
| `pre_write` | `(path, data) -> (str, bytes)\|None` | Before writing to a file | Pre |
| `post_write` | `(path)` | After writing to a file | Post |
| `pre_getattr` | `(path) -> str\|None` | Before getting file attributes | Pre |
| `post_getattr` | `(path, attr)` | After getting file attributes | Post |
| `pre_readdir` | `(path) -> str\|None` | Before listing a directory | Pre |
| `post_readdir` | `(path, entries) -> list\|None` | After listing a directory | Post |
| `pre_create` | `(path) -> str\|None` | Before creating a file | Pre |
| `post_create` | `(path)` | After creating a file | Post |
| `pre_unlink` | `(path) -> str\|None` | Before deleting a file | Pre |
| `post_unlink` | `(path)` | After deleting a file | Post |

Return `None` to pass through unchanged, or a modified value to override.

### Ordering

Pre-hooks run **forward** through the layer list (first layer → last layer).
Post-hooks run **in reverse** (last layer → first layer).

Example with layers `[A, B]`:
- `pre_read`: `A(path) → B(path) → actual read`
- `post_read`: `actual data → B(data) → A(data) → result`

### Example Layer

```python
# secrets_layer.py
import re

SECRET_PATTERNS = [
    (re.compile(r'(AKIA[0-9A-Z]{16})'), '<AWS_KEY_REDACTED>'),
    (re.compile(r'(?i)(password\s*[=:]\s*)\S+'), r'\1***'),
]

def post_read(path, data):
    """Replace secrets in text files."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None  # pass binary through unchanged
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.encode("utf-8")
```

## Installation

### Prerequisites

- Python 3.8+
- FUSE (Linux) or macFUSE (macOS)
- `pyfuse3` package (requires FUSE development libraries)

#### Recommended: Use conda-forge

```bash
conda create -n stackedfs python=3.10
conda activate stackedfs
conda install -c conda-forge fuse3 pyfuse3
pip install -e .
```

#### Manual Installation

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get install libfuse3-dev python3-dev
pip install pyfuse3
```

**macOS:**
```bash
brew install macfuse
pip install pyfuse3
```

### Setup

```bash
git clone https://github.com/yourusername/stackedfs.git
cd stackedfs
pip install -e .
stackedfs --help
```

## CLI Commands

```bash
# Validate layers
stackedfs -l layer1.py -l layer2.py
stackedfs -f stacks.json

# Mount filesystem
stackedfs mount -l layer.py /source/path /mount/point
stackedfs mount -f stacks.json /source/path /mount/point

# Mount options
stackedfs mount -l layer.py /source /mnt/point -f -d

# Unmount
stackedfs unmount /mount/point
```

## Examples

### secrets_layer.py

Redacts AWS keys, OpenAI tokens, GitHub tokens, passwords, secrets, and API keys from text files. Binary files pass through unchanged.

```bash
stackedfs mount -l examples/secrets_layer.py /home/user/project /mnt/safe
```

### echo_layer.py

Logs every filesystem operation to stderr. Useful for debugging and understanding layer interaction.

```bash
stackedfs mount -l examples/echo_layer.py /home/user/project /mnt/test
```

### merge_layer.py — *killer feature*

Multi-agent overlay filesystem with conflict detection. Mirrors the original StackedDiffFS functionality as a layer.

Each agent gets their own overlay directory (`agents/<AGENT_ID>/`). The layer merges these with a shared `base/` directory. Writes go to the active agent's overlay; reads show the merged view with agent changes taking priority.

Conflicts are detected when a file's base content changes between read and write — the layer records the conflict and still allows the write to proceed.

```
stackedfs mount -l examples/merge_layer.py /path/to/repo /mnt/point
```

**Repository layout:**
```
repo/
├── base/              # original files (shared)
│   ├── utils.py
│   └── config.json
├── agents/            # per-agent overlays
│   ├── claude/
│   │   ├── utils.py   # claude's version
│   │   └── notes.txt  # only claude sees this
│   └── cline/
│       └── utils.py   # cline's version
└── agents.json        # (optional) agent metadata
```

**Example scenario — two agents on the same project:**

```bash
# Terminal 1 — Claude
export AGENT_ID=claude
stackedfs mount -l examples/merge_layer.py /path/to/repo /mnt/project
echo "def claude_refactor(): pass" >> /mnt/project/utils.py

# Terminal 2 — Cline
export AGENT_ID=cline
stackedfs mount -l examples/merge_layer.py /path/to/repo /mnt/project
echo "def cline_feature(): pass" >> /mnt/project/utils.py
```

Each agent writes to their own overlay — neither overwrites the other. Both see the merged view through their mount point.

**Conflict detection:**
```python
# merge_layer records conflicts in a module-level list
# Check after a session:
from examples.merge_layer import _conflicts
print(_conflicts)
```

## Testing

```bash
pip install -e . pytest pytest-asyncio
pytest -v
```

### Test Structure

- `tests/conftest.py` — Shared fixtures (`source_dir`, `layer_dir`)
- `tests/test_stackedfs.py` — Unit and integration tests:
  - Layer loading and hook dispatch
  - LayerChain ordering (forward/reverse)
  - Secrets layer example behavior
  - Merge layer (overlay resolution, merged readdir, agent writes, conflict detection)
  - FUSE operations (getattr, lookup, readdir, open, read, write, create, unlink, mkdir, rmdir, rename)
  - FUSE operations with active layers

## License

MIT License

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests: `pytest`
5. Ensure all tests pass
6. Submit a pull request
