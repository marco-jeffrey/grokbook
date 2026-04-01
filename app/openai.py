"""OpenAI-compatible endpoint for nb-staroid.

Any OpenAI-compatible client (vLLM, Ollama, LM Studio, Cursor) can control
the notebook by sending tool_calls. This is NOT an LLM proxy — the LLM runs
in the client. This endpoint executes tool calls and returns results.
"""

import json
import time
import uuid

from stario import Relay
from stario.http import Router
from stario.http.types import Context, Writer

from app.db import Database
from app.kernel import KernelPool

# ── Tool definitions in OpenAI function-calling format ────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_notebooks",
            "description": "List all notebooks. Returns id, name, and updated_at for each.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_notebook",
            "description": "Get a notebook with all its cells.",
            "parameters": {
                "type": "object",
                "properties": {"notebook_id": {"type": "integer", "description": "Notebook ID"}},
                "required": ["notebook_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_notebook",
            "description": "Create a new empty notebook.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Notebook name", "default": "Untitled"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_notebook",
            "description": "Rename a notebook.",
            "parameters": {
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "integer"},
                    "name": {"type": "string"},
                },
                "required": ["notebook_id", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_notebook",
            "description": "Delete a notebook and all its cells.",
            "parameters": {
                "type": "object",
                "properties": {"notebook_id": {"type": "integer"}},
                "required": ["notebook_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_cell",
            "description": "Add a new cell to a notebook.",
            "parameters": {
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "integer"},
                    "code": {"type": "string", "description": "Initial cell content", "default": ""},
                    "cell_type": {"type": "string", "enum": ["code", "markdown"], "default": "code"},
                },
                "required": ["notebook_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_cell",
            "description": "Read a cell's content, output, and status.",
            "parameters": {
                "type": "object",
                "properties": {"cell_id": {"type": "integer"}},
                "required": ["cell_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_cell",
            "description": "Update a cell's content without executing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cell_id": {"type": "integer"},
                    "code": {"type": "string"},
                },
                "required": ["cell_id", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_cell",
            "description": "Delete a cell permanently.",
            "parameters": {
                "type": "object",
                "properties": {"cell_id": {"type": "integer"}},
                "required": ["cell_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_cell",
            "description": "Execute a code cell and return output. Optionally update code first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cell_id": {"type": "integer"},
                    "code": {"type": "string", "description": "New code to write before executing"},
                },
                "required": ["cell_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_cell",
            "description": "Move a cell up or down.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cell_id": {"type": "integer"},
                    "direction": {"type": "string", "enum": ["up", "down"]},
                },
                "required": ["cell_id", "direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "duplicate_cell",
            "description": "Duplicate a cell right below it.",
            "parameters": {
                "type": "object",
                "properties": {"cell_id": {"type": "integer"}},
                "required": ["cell_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kernel_status",
            "description": "Check kernel state for a notebook.",
            "parameters": {
                "type": "object",
                "properties": {"notebook_id": {"type": "integer"}},
                "required": ["notebook_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_kernel",
            "description": "Restart the kernel, clearing all runtime state.",
            "parameters": {
                "type": "object",
                "properties": {"notebook_id": {"type": "integer"}},
                "required": ["notebook_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_projects",
            "description": "List all projects with their notebooks.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_project",
            "description": "Create a new project.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "default": "New Project"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_project",
            "description": "Rename a project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "name": {"type": "string"},
                },
                "required": ["project_id", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_project",
            "description": "Delete a project. Notebooks are moved to Default.",
            "parameters": {
                "type": "object",
                "properties": {"project_id": {"type": "integer"}},
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_notebook",
            "description": "Move a notebook to a different project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "integer"},
                    "project_id": {"type": "integer"},
                },
                "required": ["notebook_id", "project_id"],
            },
        },
    },
]


def _serialize_cell(cell) -> dict:
    return {
        "id": cell.id,
        "notebook_id": cell.notebook_id,
        "cell_type": cell.cell_type,
        "input": cell.input,
        "output": cell.output,
        "status": cell.status,
        "execution_count": cell.execution_count,
    }


def _serialize_notebook(nb) -> dict:
    return {
        "id": nb.id,
        "name": nb.name,
        "updated_at": nb.updated_at,
        "project_id": nb.project_id,
        "order_index": nb.order_index,
    }


def _serialize_project(p) -> dict:
    return {"id": p.id, "name": p.name, "order_index": p.order_index}


async def _execute_tool(
    name: str, args: dict, db: Database, pool: KernelPool, relay: Relay[str]
) -> dict:
    """Dispatch a tool call directly to DB/pool. Returns result dict."""
    if name == "list_notebooks":
        notebooks = await db.get_all_notebooks()
        return [_serialize_notebook(n) for n in notebooks]

    if name == "get_notebook":
        nb = await db.get_notebook(args["notebook_id"])
        if not nb:
            return {"error": "not found"}
        cells = await db.get_all_cells(nb.id)
        return {**_serialize_notebook(nb), "cells": [_serialize_cell(c) for c in cells]}

    if name == "create_notebook":
        nb_id = await db.create_notebook(args.get("name", "Untitled"), project_id=args.get("project_id", 1))
        nb = await db.get_notebook(nb_id)
        relay.publish(f"notebook.{nb_id}.created", "notebook")
        return _serialize_notebook(nb)

    if name == "rename_notebook":
        await db.rename_notebook(args["notebook_id"], args["name"])
        nb = await db.get_notebook(args["notebook_id"])
        relay.publish(f"notebook.{args['notebook_id']}.updated", "notebook")
        return _serialize_notebook(nb) if nb else {"error": "not found"}

    if name == "delete_notebook":
        nb_id = args["notebook_id"]
        await db.delete_notebook(nb_id)
        relay.publish(f"notebook.{nb_id}.deleted", "notebook")
        return {"ok": True}

    if name == "create_cell":
        nb_id = args["notebook_id"]
        cell_type = args.get("cell_type", "code")
        code = args.get("code", "")
        cell_id = await db.insert_cell(nb_id, cell_type=cell_type)
        if code:
            await db.update_input(cell_id, code)
        cell = await db.get_cell(cell_id)
        relay.publish(f"notebook.{nb_id}.cell_created", "cell")
        return _serialize_cell(cell)

    if name == "read_cell":
        cell = await db.get_cell(args["cell_id"])
        return _serialize_cell(cell) if cell else {"error": "not found"}

    if name == "write_cell":
        await db.update_input(args["cell_id"], args["code"])
        cell = await db.get_cell(args["cell_id"])
        if cell:
            relay.publish(f"notebook.{cell.notebook_id}.cell_updated", "cell")
        return _serialize_cell(cell) if cell else {"error": "not found"}

    if name == "delete_cell":
        cell_id = args["cell_id"]
        nb_id = await db.get_cell_notebook_id(cell_id)
        await db.delete_cell(cell_id)
        if nb_id:
            relay.publish(f"notebook.{nb_id}.cell_deleted", "cell")
        return {"ok": True}

    if name == "execute_cell":
        cell_id = args["cell_id"]
        code = args.get("code")
        if code is not None:
            await db.update_input(cell_id, code)
        cell = await db.get_cell(cell_id)
        if not cell:
            return {"error": "not found"}
        nb_id = cell.notebook_id
        km = await pool.get(nb_id)
        output, is_error, exec_count = await km.execute(cell.input)
        status = "error" if is_error else "ok"
        await db.update_cell(cell_id, input=cell.input, output=output, status=status, execution_count=exec_count)
        await db.touch_notebook(nb_id)
        cell = await db.get_cell(cell_id)
        relay.publish(f"notebook.{nb_id}.cell_executed", "cell")
        return _serialize_cell(cell)

    if name == "move_cell":
        await db.move_cell(args["cell_id"], args["direction"])
        cell = await db.get_cell(args["cell_id"])
        if cell:
            relay.publish(f"notebook.{cell.notebook_id}.cell_moved", "cell")
        return _serialize_cell(cell) if cell else {"error": "not found"}

    if name == "duplicate_cell":
        new_id = await db.duplicate_cell(args["cell_id"])
        if not new_id:
            return {"error": "not found"}
        cell = await db.get_cell(new_id)
        relay.publish(f"notebook.{cell.notebook_id}.cell_duplicated", "cell")
        return _serialize_cell(cell)

    if name == "kernel_status":
        state = await pool.get_state(args["notebook_id"])
        return {"state": state}

    if name == "restart_kernel":
        nb_id = args["notebook_id"]
        await pool.restart(nb_id)
        relay.publish(f"notebook.{nb_id}.kernel_restarted", "kernel")
        return {"state": "idle"}

    if name == "list_projects":
        projects = await db.get_all_projects()
        result = []
        for p in projects:
            nbs = await db.get_notebooks_by_project(p.id)
            result.append({
                **_serialize_project(p),
                "notebooks": [_serialize_notebook(nb) for nb in nbs],
            })
        return result

    if name == "create_project":
        project_id = await db.create_project(args.get("name", "New Project"))
        p = await db.get_project(project_id)
        return _serialize_project(p)

    if name == "rename_project":
        await db.rename_project(args["project_id"], args["name"])
        p = await db.get_project(args["project_id"])
        return _serialize_project(p) if p else {"error": "not found"}

    if name == "delete_project":
        await db.delete_project(args["project_id"])
        return {"ok": True}

    if name == "move_notebook":
        nb_id = args["notebook_id"]
        await db.move_notebook_to_project(nb_id, args["project_id"])
        nb = await db.get_notebook(nb_id)
        if not nb:
            return {"error": "not found"}
        relay.publish(f"notebook.{nb_id}.moved", "notebook")
        return _serialize_notebook(nb)

    return {"error": f"unknown tool: {name}"}


def openai_router(db: Database, pool: KernelPool, relay: Relay[str]) -> Router:
    router = Router()

    async def models(c: Context, w: Writer) -> None:
        w.json({
            "object": "list",
            "data": [
                {
                    "id": "nb-staroid",
                    "object": "model",
                    "created": 0,
                    "owned_by": "nb-staroid",
                }
            ],
        })

    async def chat_completions(c: Context, w: Writer) -> None:
        body = await c.req.json()
        messages = body.get("messages", [])

        # Find tool_calls in the last assistant message
        tool_calls = []
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                tool_calls = msg["tool_calls"]
                break

        if not tool_calls:
            # No tool calls — return available tools in a system message
            w.json({
                "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "nb-staroid",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "I can control the notebook. Send tool_calls to interact.",
                            "tool_calls": None,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })
            return

        # Execute tool calls and build tool result messages
        results = []
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            result = await _execute_tool(name, args, db, pool, relay)
            results.append({
                "tool_call_id": tc.get("id", ""),
                "role": "tool",
                "content": json.dumps(result),
            })

        # Return as a response with the tool results
        w.json({
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "nb-staroid",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps([r["content"] for r in results]),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    router.get("/models", models)
    router.post("/chat/completions", chat_completions)

    return router
