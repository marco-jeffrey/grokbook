"""MCP server for grokbook — exposes notebook tools to LLMs.

Run with:
    uv run python mcp_server.py                          # stdio (Claude Desktop / Claude Code)
    uv run python mcp_server.py --host 0.0.0.0 --port 8081  # HTTP (remote / LM Studio)
    uv run fastmcp run mcp_server.py:mcp                 # stdio via CLI
"""

import os

import httpx
from fastmcp import FastMCP

def _get_api_url() -> str:
    return os.environ.get("GROKBOOK_API_URL", "http://localhost:8080/api")

mcp = FastMCP(
    "grokbook",
    instructions="""grokbook is a lightweight Jupyter-compatible notebook server built for education. It provides interactive Python notebooks where students learn computer science through a mix of explanations and executable code.

## Your Role

You are a CS tutor and teaching assistant. Your primary job is helping students learn computer science concepts through interactive notebooks — not just generating code for them.

## Teaching Philosophy

This is the most important part. When a student says they're stuck or asks for help:

1. First read their notebook (get_notebook) to understand what they've tried
2. Identify WHERE their mental model is incomplete or wrong
3. Teach the foundational concepts they're missing so they can solve it themselves
4. Use targeted questions to guide them toward the answer
5. NEVER just hand them the solution unless they explicitly say "just give me the answer"

The goal is to build understanding, not to be a code-generation machine.

When creating study notebooks, progressively build from basics to advanced concepts. Include "try it yourself" exercises where students write code to test their understanding.

## How Notebooks Work

Notebooks work exactly like Jupyter:
- A notebook is an ordered list of cells, displayed top to bottom
- Cells are either **code** (executable Python in an IPython kernel) or **markdown** (rich text)
- Each notebook has its own IPython kernel — variables, imports, and state persist across cell executions
- Run cell 1 to define `x = 5`, then cell 2 can use `x`

Projects are folders that organize notebooks. A default project (id=1) always exists.

## Typical Workflows

### Creating a study notebook
1. `create_notebook("Sorting Algorithms")` to start fresh
2. Alternate between markdown cells (concept explanations, analogies, diagrams) and code cells (runnable examples)
3. `execute_cell` each code cell to verify it works before the student sees it
4. Each notebook should be a complete, standalone learning resource

### Helping a stuck student
1. `get_notebook(id)` to see their full work
2. `read_cell(id)` for details on specific cells
3. Add markdown cells with hints and guiding questions
4. Do NOT write the solution code for them

### Building incrementally
- `insert_cell` to add cells at specific positions in the notebook
- `execute_cell` to run and verify code works
- `get_variables` to inspect what's in the kernel (useful for debugging with students)

## Key Tool Behaviors

- `execute_cell(cell_id)` runs the cell's current saved content. Pass `code="..."` to update AND execute in one step.
- `write_cell(cell_id, code)` saves content WITHOUT executing — use for drafting.
- Kernels persist state between executions (like Jupyter). Use `restart_kernel` to reset.
- `insert_cell` places a cell at a specific position. `create_cell` always appends to the end.
- `get_notebook` returns all cells in display order — use this to understand the full notebook context before making changes.
""",
)


def _code_execution_enabled() -> bool:
    return os.environ.get("GROKBOOK_ALLOW_CODE_EXECUTION", "").lower() in ("1", "true", "yes")


def _api(method: str, path: str, **kwargs) -> dict | list:
    try:
        api_url = _get_api_url()
        with httpx.Client(base_url=api_url, timeout=300) as client:
            r = getattr(client, method)(path, **kwargs)
            r.raise_for_status()
            if r.status_code == 204:
                return {"ok": True}
            return r.json()
    except httpx.ConnectError:
        return {"error": f"Cannot connect to grokbook at {api_url}. Is the server running?"}
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
        except Exception:
            body = e.response.text
        return {"error": f"HTTP {e.response.status_code}", "detail": body}
    except httpx.TimeoutException:
        return {
            "error": "Request timed out after 300s. The operation may still be running on the server."
        }


# ── notebooks ─────────────────────────────────────────────────────────────


@mcp.tool
def list_notebooks() -> list[dict]:
    """List all notebooks.

    Returns a list of notebook summaries, each with:
        id (int), name (str), project_id (int), updated_at (str).

    Use get_notebook(id) to see a notebook's cells and content.
    """
    return _api("get", "/notebooks")


@mcp.tool
def get_notebook(notebook_id: int) -> dict:
    """Get a notebook with all its cells in display order (top to bottom).

    This is the main way to see what's in a notebook. Use it to understand
    the full context before making changes.

    Args:
        notebook_id: The notebook to retrieve.

    Returns:
        id, name, project_id, updated_at, and a "cells" list.
        Each cell has: id (int), cell_type ("code"|"markdown"), input (str),
        output (str), status (""|"ok"|"error"), execution_count (int),
        execution_time (float).
    """
    return _api("get", f"/notebooks/{notebook_id}")


@mcp.tool
def create_notebook(name: str = "Untitled", project_id: int = 1) -> dict:
    """Create a new empty notebook in the specified project.

    Args:
        name: Display name for the notebook.
        project_id: Which project to create it in. Default 1 is the "Default" project.

    Returns the new notebook (id, name, project_id, updated_at).
    The notebook starts with no cells — use create_cell() to add them.
    """
    return _api("post", "/notebooks", json={"name": name, "project_id": project_id})


@mcp.tool
def rename_notebook(notebook_id: int, name: str) -> dict:
    """Rename a notebook.

    Args:
        notebook_id: The notebook to rename.
        name: New display name. Must be non-empty.

    Returns the updated notebook.
    """
    return _api("put", f"/notebooks/{notebook_id}", json={"name": name})


@mcp.tool
def duplicate_notebook(notebook_id: int) -> dict:
    """Duplicate a notebook and all its cells. The copy gets a new id and
    a name like "Original Name (copy)".

    Args:
        notebook_id: The notebook to duplicate.

    Returns the new notebook with its cells.
    """
    return _api("post", f"/notebooks/duplicate/{notebook_id}")


# @mcp.tool
# def delete_notebook(notebook_id: int) -> dict:
#     """Delete a notebook and all its cells permanently. This cannot be undone."""
#     return _api("delete", f"/notebooks/{notebook_id}")


# ── projects ──────────────────────────────────────────────────────────────


@mcp.tool
def list_projects() -> dict | list:
    """List all projects with their notebooks.

    Returns a list of projects, each with: id (int), name (str),
    order_index (int), and a "notebooks" list of notebook summaries.
    """
    return _api("get", "/projects")


@mcp.tool
def create_project(name: str = "New Project") -> dict:
    """Create a new project for organizing notebooks.

    Args:
        name: Display name for the project.

    Returns the new project (id, name, order_index).
    """
    return _api("post", "/projects", json={"name": name})


@mcp.tool
def rename_project(project_id: int, name: str) -> dict:
    """Rename a project.

    Args:
        project_id: The project to rename.
        name: New display name.

    Returns the updated project.
    """
    return _api("put", f"/projects/{project_id}", json={"name": name})


# @mcp.tool
# def delete_project(project_id: int) -> dict:
#     """Delete a project. Notebooks are moved to the Default project."""
#     return _api("delete", f"/projects/{project_id}")


@mcp.tool
def move_notebook(notebook_id: int, project_id: int) -> dict:
    """Move a notebook to a different project.

    Args:
        notebook_id: The notebook to move.
        project_id: The destination project.

    Returns the updated notebook.
    """
    return _api("post", f"/notebooks/move/{notebook_id}", json={"project_id": project_id})


# ── cells ─────────────────────────────────────────────────────────────────


@mcp.tool
def create_cell(
    notebook_id: int,
    code: str = "",
    cell_type: str = "code",
) -> dict:
    """Add a new cell to the end of a notebook.

    Args:
        notebook_id: The notebook to add the cell to.
        code: Initial content for the cell (Python code or markdown text).
        cell_type: "code" for executable Python, "markdown" for rich text.

    Returns the new cell (id, notebook_id, cell_type, input, output, status).

    To insert at a specific position instead of appending, use insert_cell().
    """
    return _api(
        "post",
        "/cells",
        json={"notebook_id": notebook_id, "code": code, "cell_type": cell_type},
    )


@mcp.tool
def insert_cell(
    notebook_id: int,
    after_cell_id: int | None = None,
    cell_type: str = "code",
    code: str = "",
) -> dict:
    """Insert a new cell at a specific position in the notebook.

    Args:
        notebook_id: The notebook to insert into.
        after_cell_id: Place the new cell immediately after this cell.
            If None, appends to the end (same as create_cell).
        cell_type: "code" or "markdown".
        code: Initial content for the cell.

    Returns the new cell (id, notebook_id, cell_type, input, output, status).

    See also: create_cell() to simply append to the end.
    """
    body: dict = {"notebook_id": notebook_id, "cell_type": cell_type, "code": code}
    if after_cell_id is not None:
        body["after_cell_id"] = after_cell_id
    return _api("post", "/cells/insert", json=body)


@mcp.tool
def change_cell_type(cell_id: int, cell_type: str) -> dict:
    """Change a cell's type between "code" and "markdown".

    Args:
        cell_id: The cell to change.
        cell_type: Must be "code" or "markdown".

    Returns the updated cell.
    """
    return _api("post", f"/cells/type/{cell_id}", json={"cell_type": cell_type})


@mcp.tool
def read_cell(cell_id: int) -> dict:
    """Read a single cell's current content, output, and status.

    Args:
        cell_id: The cell to read.

    Returns: id, notebook_id, cell_type, input (source code/text),
    output (execution result), status (""|"ok"|"error"),
    execution_count, execution_time.

    Use get_notebook() to see all cells at once; use read_cell() when you
    need details on a specific cell.
    """
    return _api("get", f"/cells/{cell_id}")


@mcp.tool
def write_cell(cell_id: int, code: str) -> dict:
    """Update a cell's content without executing it.

    This saves the code/text but does NOT run it. Use this for drafting
    content before it's ready to execute.

    Args:
        cell_id: The cell to update.
        code: New content (Python code or markdown text).

    Returns the updated cell.

    To update and execute in one step, use execute_cell(cell_id, code="...") instead.
    """
    return _api("put", f"/cells/{cell_id}", json={"code": code})


@mcp.tool
def delete_cell(cell_id: int) -> dict:
    """Delete a cell permanently. This cannot be undone.

    Args:
        cell_id: The cell to delete.
    """
    return _api("delete", f"/cells/{cell_id}")


@mcp.tool
def move_cell(cell_id: int, direction: str = "down") -> dict:
    """Move a cell one position up or down in the notebook.

    Args:
        cell_id: The cell to move.
        direction: "up" moves the cell earlier (toward the top),
            "down" moves it later (toward the bottom). Default is "down".

    Returns the updated cell.
    """
    return _api("post", f"/cells/move/{cell_id}", json={"direction": direction})


@mcp.tool
def duplicate_cell(cell_id: int) -> dict:
    """Duplicate a cell, inserting the copy immediately below the original.

    Args:
        cell_id: The cell to duplicate.

    Returns the new (duplicated) cell.
    """
    return _api("post", f"/cells/duplicate/{cell_id}")


def _execute_cell(cell_id: int, code: str | None = None) -> dict:
    """Execute a code cell in the notebook's IPython kernel.

    Args:
        cell_id: The cell to execute.
        code: If provided, the cell content is updated to this value before
            executing. If None, executes the cell's current saved content.

    Returns the cell after execution:
        id, input, output (execution result as text/HTML),
        status ("ok" or "error"), execution_count, execution_time.

    The kernel starts automatically on first execution. State (variables,
    imports) persists across executions within the same notebook — just like
    Jupyter. Supports long-running computations with no timeout.

    See also: write_cell() to save without executing, run_all_cells() to
    execute an entire notebook.
    """
    body = {}
    if code is not None:
        body["code"] = code
    return _api(
        "post",
        f"/cells/execute/{cell_id}",
        json=body if body else None,
    )


def _run_all_cells(notebook_id: int) -> dict:
    """Execute all code cells in a notebook sequentially, top to bottom.

    Skips markdown cells and empty code cells. Stops on the first error.

    Args:
        notebook_id: The notebook to run.

    Returns a list of results with cell_id and status for each executed cell.
    """
    return _api("post", f"/cells/run-all/{notebook_id}")


@mcp.tool
def clear_output(cell_id: int) -> dict:
    """Clear a single cell's output and reset its status.

    Args:
        cell_id: The cell whose output to clear.
    """
    return _api("post", f"/cells/clear/{cell_id}")


@mcp.tool
def clear_all_outputs(notebook_id: int) -> dict:
    """Clear all cell outputs in a notebook. Does not affect cell content or
    kernel state — only removes the displayed output.

    Args:
        notebook_id: The notebook whose outputs to clear.
    """
    return _api("post", f"/cells/clear-all/{notebook_id}")


# ── kernel ────────────────────────────────────────────────────────────────


def _kernel_status(notebook_id: int) -> dict:
    """Check the IPython kernel state for a notebook.

    Args:
        notebook_id: The notebook whose kernel to check.

    Returns: status ("idle", "busy", "dead", or "not_started").
    The kernel starts automatically on the first execute_cell() call.
    """
    return _api("get", f"/kernel/status/{notebook_id}")


def _restart_kernel(notebook_id: int) -> dict:
    """Restart the IPython kernel for a notebook.

    This clears ALL runtime state: variables, imports, and any running
    computations. The kernel will be ready for fresh execution after restart.
    Use this when the kernel is in a bad state or you want a clean slate.

    Args:
        notebook_id: The notebook whose kernel to restart.
    """
    return _api("post", f"/kernel/restart/{notebook_id}")


def _get_variables(notebook_id: int) -> dict:
    """Get all user-defined variables from the notebook's kernel.

    Useful for inspecting kernel state, debugging with students, or verifying
    that earlier cells set up the expected variables.

    Args:
        notebook_id: The notebook whose kernel variables to inspect.

    Returns a list of variables with: name (str), type (str),
    and optionally shape (str) and dtype (str) for numpy arrays,
    and size (str) for sized objects.

    Only works when the kernel is running (after at least one execute_cell).
    """
    return _api("get", f"/kernel/variables/{notebook_id}")


def _interrupt_kernel(notebook_id: int) -> dict:
    """Interrupt the running kernel, like pressing Ctrl+C.

    Stops the currently executing code without killing the kernel.
    Variables and imports are preserved. Use this to stop long-running
    or stuck computations.

    Args:
        notebook_id: The notebook whose kernel to interrupt.
    """
    return _api("post", f"/kernel/interrupt/{notebook_id}")


# ── code execution tools (opt-in via --allow-code-execution) ─────────────

if _code_execution_enabled():
    execute_cell = mcp.tool(_execute_cell)
    run_all_cells = mcp.tool(_run_all_cells)
    kernel_status = mcp.tool(_kernel_status)
    restart_kernel = mcp.tool(_restart_kernel)
    get_variables = mcp.tool(_get_variables)
    interrupt_kernel = mcp.tool(_interrupt_kernel)


def run_mcp(host: str | None = None, port: int = 8081) -> None:
    """Run the MCP server. If host is provided, uses HTTP transport; otherwise stdio."""
    if host:
        mcp.run(transport="streamable-http", host=host, port=port)
    else:
        mcp.run()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="grokbook MCP server")
    parser.add_argument("--host", default=None, help="Host for HTTP transport (enables HTTP mode)")
    parser.add_argument("--port", type=int, default=None, help="Port for HTTP transport")
    args = parser.parse_args()
    run_mcp(host=args.host, port=args.port or 8081)
