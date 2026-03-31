# nb-staroid

A lightweight, reactive notebook server built on [Stario](stario/) and [Datastar](https://data-star.dev). Runs IPython kernels, persists to SQLite, streams output over SSE. No npm, no webpack, no build step.

## Quick Start

Requires **Python 3.14+** and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/your-user/nb-staroid.git
cd nb-staroid
uv sync
uv run python main.py
```

Open [http://localhost:8080](http://localhost:8080).

### Use a custom Python environment for kernels

Create a separate environment with your favorite libraries, then point the server at it:

```bash
# Create a temp env with data science libs
mkdir /tmp/my-env && cd /tmp/my-env
uv init && uv add pandas numpy matplotlib ipykernel

# Start nb-staroid using that env for kernels
cd /path/to/nb-staroid
uv run python main.py --python /tmp/my-env/.venv/bin/python
```

## Features

**Notebook editing**
- Code cells with streaming execution output
- Markdown cells with GFM rendering (double-click to edit)
- Rich output: images (PNG/JPEG), HTML, SVG, matplotlib plots
- Autocomplete and function signature tooltips
- Auto-expanding textareas with 1.5s debounced autosave
- Collapsible output for long results (20+ lines)

**Notebook management**
- Multiple notebooks with sidebar navigation
- Rename, duplicate, delete
- Import/export Jupyter `.ipynb` files (nbformat v4)

**Kernel**
- One persistent IPython kernel per notebook (lazy-started)
- Kernel pool with LRU eviction (default: 5 max)
- Variables inspector panel (name, type, shape, dtype)
- Restart kernel without losing notebook content

**UI**
- Dark/light theme toggle (persisted to localStorage)
- Wide mode toggle for larger displays
- Live SSE streaming with auto-reconnect and heartbeat
- Execution status indicator (running/ok/error)

**Keyboard shortcuts**

| Mode | Key | Action |
|------|-----|--------|
| Command | `j` / `k` | Navigate cells |
| Command | `a` / `b` | Insert cell above / below |
| Command | `m` / `y` | Convert to markdown / code |
| Command | `dd` | Delete cell |
| Command | `Cmd+Shift+Arrow` | Move cell up / down |
| Edit | `Shift+Enter` | Execute, move to next |
| Edit | `Cmd+Enter` | Execute, stay in cell |
| Edit | `Tab` / `Shift+Tab` | Indent / dedent |
| Edit | `Escape` | Exit to command mode |

**APIs**

Three programmatic interfaces, all running from the same server:

- **REST API** at `/api` — CRUD for notebooks, cells, and kernel operations
- **OpenAI-compatible endpoint** at `/v1/chat/completions` — tool-calling interface for LLM agents
- **MCP server** (`mcp_server.py`) — Model Context Protocol for Claude Desktop, Cursor, etc.

## Architecture

```
Browser ──SSE──▶ Stario server ──ZMQ──▶ IPython kernel
   │                  │
   │  Datastar        │  SQLite
   │  (reactive       │  (notebooks,
   │   signals)       │   cells)
   │                  │
   ▼                  ▼
 DOM patches      Relay pub/sub
 via SSE          (live sync)
```

- **Stario** — async Python web framework with SSE, HTML DSL, and Datastar integration
- **Datastar** — lightweight reactive library replacing React/Vue/Svelte
- **IPython** — real Jupyter kernels via `jupyter-client`
- **SQLite** — zero-config persistence with schema migrations
- **Relay** — in-process pub/sub for broadcasting changes to SSE clients

## CLI Options

```
uv run python main.py [OPTIONS]

  --host TEXT     Bind address (default: 0.0.0.0)
  --port INT      Bind port (default: 8080)
  --python PATH   Python interpreter for kernels (default: current env)
```

## MCP Server

Run the MCP server for integration with Claude Desktop, Cursor, or other MCP clients:

```bash
# HTTP mode
uv run python mcp_server.py --host 0.0.0.0 --port 8081

# stdio mode (for Claude Desktop)
uv run python mcp_server.py
```

Set `NB_STAROID_API_URL` to point at a running nb-staroid instance (default: `http://localhost:8080/api`).

**Available tools**: `list_notebooks`, `get_notebook`, `create_notebook`, `rename_notebook`, `duplicate_notebook`, `delete_notebook`, `create_cell`, `read_cell`, `write_cell`, `delete_cell`, `move_cell`, `duplicate_cell`, `execute_cell`, `kernel_status`, `restart_kernel`

## Project Structure

```
main.py              Server entry point
mcp_server.py        MCP server (stdio + HTTP)
app/
  handlers.py        Page rendering, SSE, cell operations
  views.py           HTML components (Stario HTML DSL)
  kernel.py          KernelManager, KernelPool, variable inspection
  db.py              SQLite database, schema migrations
  api.py             REST API routes
  openai.py          OpenAI-compatible endpoint
  ipynb.py           Import/export Jupyter .ipynb
  state.py           Notebook, Cell dataclasses
  static/js/app.js   Client-side keyboard shortcuts, autocomplete, autosave
stario/              Stario framework (included as editable subpackage)
```

## License

MIT
