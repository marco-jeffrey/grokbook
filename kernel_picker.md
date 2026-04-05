# v1.1 Kernel Picker Implementation Plan

Per-notebook kernel selector with auto-discovery via uv, kernelspecs, and cwd `.venv`.

## Architecture

Currently `KernelPool` holds one pool-wide `python_path` — every notebook's kernel uses the same interpreter. This plan moves the interpreter to per-notebook, adds discovery, and a UI switcher.

```
┌──────────────┐   selects env   ┌──────────────┐   writes   ┌────┐
│ header UI    │────────────────▶│ handlers     │───────────▶│ DB │
└──────────────┘                 └──────┬───────┘            └────┘
                                        │ restart kernel
                                        ▼
                            ┌──────────────────────┐
                            │ KernelPool[nb_id]    │──┐
                            │ (per-notebook python)│  │
                            └──────────────────────┘  │
                                                      │ launches
                                                      ▼
                            ┌─────────┐      ┌──────────────┐
            discovers <──── │ envs.py │◀────▶│ uv python    │
                            │         │      │ kernelspecs  │
                            └─────────┘      │ cwd .venv    │
                                             └──────────────┘
```

---

## Phase 1 — `grokbook/envs.py` (new module)

Pure discovery + probing. No DB, no HTTP.

**Public API:**
```python
@dataclass
class EnvInfo:
    name: str                # display name, e.g. ".venv (py3.12, cwd)"
    path: str                # absolute path to python binary
    version: tuple[int,int,int]
    source: str              # "cwd" | "uv" | "kernelspec" | "registered" | "current"
    has_ipykernel: bool
    label_glyph: str         # "V" | "U" | "K" | "S"

async def discover_envs(cwd: Path) -> list[EnvInfo]: ...
async def probe_env(python_path: str) -> EnvInfo | None: ...
async def install_ipykernel(python_path: str) -> AsyncIterator[str]: ...
async def pick_default(cwd: Path) -> EnvInfo | None: ...
async def refresh() -> None: ...
def get_all() -> list[EnvInfo]: ...
def find_by_path(path: str) -> EnvInfo | None: ...
```

**Discovery sources (merged, deduped by resolved path):**
1. `EnvInfo(source="current")` — `sys.executable`
2. `$VIRTUAL_ENV/bin/python` if set
3. `cwd/.venv/bin/python` and `cwd/venv/bin/python`
4. `uv python list --only-installed --output-format json`
5. `jupyter_client.kernelspec.KernelSpecManager().find_kernel_specs()`

**Probe** (~80ms per env via `asyncio.to_thread`):
```python
py -c "import sys,json; d={'v':list(sys.version_info[:3])};
try: import ipykernel; d['ipk']=True
except: d['ipk']=False
print(json.dumps(d))"
```

Parallel via `asyncio.gather`, 5s total cap.

**Cache:** module-level `_cache: list[EnvInfo] | None`, `refresh()` rebuilds.

---

## Phase 2 — DB migration + KernelPool refactor

### `grokbook/db.py`
Migration pattern:
```python
if "kernel_env" not in notebooks_cols:
    await conn.execute("ALTER TABLE notebooks ADD COLUMN kernel_env TEXT")
```

Update 5 SELECT notebooks queries to include `kernel_env`. Add:
```python
async def set_notebook_kernel_env(self, nb_id: int, kernel_env: str | None) -> None
```

### `grokbook/state.py`
```python
@dataclass
class Notebook:
    # ... existing fields
    kernel_env: str | None = None
```

### `grokbook/kernel.py`
`KernelPool` changes:
- Rename `python_path` → `default_python_path`
- `get(nb_id, python_path: str | None) -> KernelManager`
- If existing manager's path differs from requested → shutdown old, start new
- Store `python_path` as attribute on `KernelManager`

### `grokbook/handlers.py`, `grokbook/api.py`
All `await pool.get(nb_id)` → `await pool.get(nb_id, nb.kernel_env)`. Add helper `_resolve_env(nb_id) -> str | None`.

---

## Phase 3 — UI: dropdown in header

### `grokbook/views.py`
`_kernel_selector(notebook, envs)` component next to kernel state indicator.

Markup outline:
```python
Div({"id": "kernel-selector", "class": "relative"},
    data.signals({"show_kernels": False}),
    Button(data.on("click", "$show_kernels = !$show_kernels"), active_label, "▾"),
    Div(data.show("$show_kernels"), data.on("click", "$show_kernels = false", outside=True),
        *[_env_item(env, notebook.kernel_env) for env in envs],
        Button(data.on("click", at.post("/kernel/envs/refresh")), "↻ Refresh"),
    ),
)
```

Env item shows: glyph, name, version, active highlight, ⚠ if missing ipykernel.

---

## Phase 4 — Handlers: switch env + install ipykernel

New routes in `grokbook/handlers.py`:

- `POST /kernel/env/set` — validates env, updates DB, restarts kernel for notebook
- `POST /kernel/env/install` — streams `uv pip install --python X ipykernel` output
- `POST /kernel/envs/refresh` — re-scans, re-patches selector

Add relay event `kernel_env_changed` so all tabs viewing the notebook update.

---

## Phase 5 — Install modal

When user clicks env with ⚠:
1. Set client signal `$install_env_path = <path>` (no server call yet)
2. Modal opens bound to that signal
3. "Install" button POSTs to `/kernel/env/install`
4. Log streams into `#install-log` via SSE `mode="append"`
5. On completion, modal auto-closes, env becomes selectable

---

## Phase 6 — Startup bootstrap

### `grokbook/_server.py`
```python
async def bootstrap(app, span):
    db = await Database.connect(db_path)
    await envs.refresh()
    if python_path is None:
        default_env = await envs.pick_default(cwd=Path.cwd())
        python_path = default_env.path if default_env else None
        if default_env and not default_env.has_ipykernel:
            print(f"Installing ipykernel into {default_env.name}…")
            async for line in envs.install_ipykernel(default_env.path):
                print(line, end="")
    pool = KernelPool(default_python_path=python_path)
```

`pick_default(cwd)` priority: `$VIRTUAL_ENV` → `cwd/.venv` → `uv python find` → `sys.executable`.

---

## File change summary

| File | Change | Est. LOC |
|---|---|---|
| `grokbook/envs.py` | **NEW** discovery module | ~120 |
| `grokbook/db.py` | migration + `kernel_env` in SELECTs | ~25 |
| `grokbook/state.py` | `Notebook.kernel_env` | ~1 |
| `grokbook/kernel.py` | per-notebook python_path | ~25 |
| `grokbook/handlers.py` | 3 new handlers + routes | ~80 |
| `grokbook/api.py` | pass env through | ~10 |
| `grokbook/views.py` | selector + modal components | ~90 |
| `grokbook/_server.py` | startup discovery + auto-install | ~15 |

**Total: ~370 LOC, 8 files, no new deps.**

---

## Progress

- [x] Phase 1 — envs.py discovery module
- [x] Phase 2 — DB migration + KernelPool refactor
- [x] Phase 3 — Header dropdown UI
- [x] Phase 4 — Switch/install handlers
- [x] Phase 5 — Install modal
- [x] Phase 6 — Startup bootstrap
