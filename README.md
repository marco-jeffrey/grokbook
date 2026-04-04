# grokbook

Interactive notebook server for learning computer science. Works like Jupyter — code cells, markdown, persistent IPython kernels — with a built-in MCP server so AI tutors can create and manage notebooks for you.

## Install

Requires **Python 3.14+** and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/marco-jeffrey/grokbook.git
cd grokbook
uv sync
```

## Usage

```bash
grokbook
```

That's it. Opens the notebook UI on [localhost:8080](http://localhost:8080) and the MCP server on port 8081. A welcome notebook is created on first run.

### Custom kernel environment

By default, grokbook uses its own Python for the kernel. To use a separate environment with your libraries:

```bash
# Create an env with your packages
mkdir /tmp/my-env && cd /tmp/my-env
uv init && uv add pandas numpy matplotlib ipykernel

# Start grokbook with that env
grokbook serve --python /tmp/my-env/.venv/bin/python
```

### Remote access (Tailscale / LAN)

By default, grokbook binds to `127.0.0.1` (localhost only). To access from other machines:

```bash
grokbook serve --host 0.0.0.0
```

Both the notebook server and MCP server bind to all interfaces. Access from another machine at `http://<ip>:8080`.

> **Warning**: Grokbook executes arbitrary Python code. Do not expose it to untrusted networks.

### CLI reference

```
grokbook                          # Start everything (default)
grokbook serve [OPTIONS]          # Start notebook + MCP servers
  --host TEXT                     # Bind address (default: 127.0.0.1)
  --port, -p INT                  # Notebook server port (default: 8080)
  --mcp-port INT                  # MCP server port (default: 8081)
  --python PATH                   # Python interpreter for kernels
  --db PATH                       # Database file (default: ~/.grokbook/grokbook.db)
  --allow-code-execution          # Enable execute/kernel tools in MCP

grokbook mcp [OPTIONS]            # MCP server standalone (stdio, for Claude Desktop)
  --allow-code-execution          # Enable execute/kernel tools
```

## MCP Integration

On startup, grokbook prints an MCP config block you can paste directly into Claude Desktop or LM Studio:

```json
{
  "mcpServers": {
    "grokbook": {
      "command": "grokbook",
      "args": ["mcp", "--allow-code-execution"],
      "env": {
        "GROKBOOK_API_URL": "http://localhost:8080/api"
      }
    }
  }
}
```

Omit `--allow-code-execution` to restrict the MCP server to read/write operations only (no code execution).

The `grokbook mcp` command runs in stdio mode for Claude Desktop. For HTTP-based MCP clients (LM Studio, remote agents), the built-in MCP server on port 8081 is already running when you start `grokbook serve`.

**Always available**: `list_notebooks`, `get_notebook`, `create_notebook`, `rename_notebook`, `duplicate_notebook`, `list_projects`, `create_project`, `rename_project`, `move_notebook`, `create_cell`, `insert_cell`, `read_cell`, `write_cell`, `delete_cell`, `move_cell`, `duplicate_cell`, `change_cell_type`, `clear_output`, `clear_all_outputs`

**With `--allow-code-execution`**: `execute_cell`, `run_all_cells`, `kernel_status`, `restart_kernel`, `get_variables`, `interrupt_kernel`

To enable code execution via MCP:

```bash
grokbook serve --allow-code-execution
```

## Features

- **Code cells** with streaming execution, rich output (images, HTML, SVG, pandas tables)
- **Markdown cells** with GitHub-flavored rendering
- **Persistent IPython kernels** — one per notebook, variables carry over between cells
- **Keyboard-driven** — Vim-like command/edit modes (j/k, a/b, dd, Shift+Enter)
- **Import/export** Jupyter `.ipynb` files
- **Variables inspector** panel
- **Dark/light theme**, wide mode, autocomplete, signature tooltips
- **Live sync** across browser tabs via SSE

## Keyboard Shortcuts

Grokbook uses two modes, inspired by Vim:

**Command mode** (press `Escape` to enter):

| Key | Action |
|-----|--------|
| `j` / `k` | Navigate between cells |
| `Enter` | Edit selected cell |
| `a` / `b` | Insert cell above / below |
| `m` | Convert to markdown |
| `y` | Convert to code |
| `dd` | Delete cell |
| `Cmd+Shift+Up/Down` | Move cell up / down |

**Edit mode** (press `Enter` or click a cell):

| Key | Action |
|-----|--------|
| `Shift+Enter` | Execute cell, move to next |
| `Cmd+Enter` / `Ctrl+Enter` | Execute cell, stay in place |
| `Escape` | Back to command mode |
| `Tab` / `Shift+Tab` | Indent / dedent |

### Vim mode

Enable Vim keybindings from the editor settings panel (gear icon). When active:

- Full Vim motions in code cells (normal, insert, visual modes)
- `jk` is mapped to `Escape` in insert mode for quick mode switching
- Block cursor in normal mode, line cursor in insert mode

## Architecture

```
Browser ──SSE──▶ Stario server (:8080) ──ZMQ──▶ IPython kernel
   │                  │
   │  Datastar        │  SQLite (~/.grokbook/grokbook.db)
   │  (reactive       │
   │   signals)       ├── REST API (/api)
   │                  │
   ▼                  ▼
 DOM patches      MCP server (:8081)
 via SSE          (FastMCP, for LLM agents)
```

## License

MIT
