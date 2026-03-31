import asyncio
import time

from stario import Relay
from stario.http import Router
from stario.http.types import Context, Writer

from app.db import Database
from app.kernel import KernelPool
from app.state import Cell, Notebook


def _serialize_cell(cell: Cell) -> dict:
    return {
        "id": cell.id,
        "notebook_id": cell.notebook_id,
        "cell_type": cell.cell_type,
        "input": cell.input,
        "output": cell.output,
        "status": cell.status,
        "execution_count": cell.execution_count,
        "execution_time": cell.execution_time,
    }


def _serialize_notebook(nb: Notebook) -> dict:
    return {"id": nb.id, "name": nb.name, "updated_at": nb.updated_at}


def api_router(db: Database, pool: KernelPool, relay: Relay[str]) -> Router:
    router = Router()

    # ── notebooks ─────────────────────────────────────────────────────────

    async def list_notebooks(c: Context, w: Writer) -> None:
        notebooks = await db.get_all_notebooks()
        w.json([_serialize_notebook(n) for n in notebooks])

    async def create_notebook(c: Context, w: Writer) -> None:
        body = await c.req.json()
        name = body.get("name", "Untitled")
        nb_id = await db.create_notebook(name)
        nb = await db.get_notebook(nb_id)
        w.json(_serialize_notebook(nb), 201)
        relay.publish(f"notebook.{nb_id}.created", "notebook")

    async def get_notebook(c: Context, w: Writer) -> None:
        try:
            nb_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid notebook id"}, 400)
            return
        nb = await db.get_notebook(nb_id)
        if not nb:
            w.json({"error": "not found"}, 404)
            return
        cells = await db.get_all_cells(nb_id)
        w.json(
            {
                **_serialize_notebook(nb),
                "cells": [_serialize_cell(cell) for cell in cells],
            }
        )

    async def update_notebook(c: Context, w: Writer) -> None:
        try:
            nb_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid notebook id"}, 400)
            return
        body = await c.req.json()
        name = body.get("name")
        if name:
            await db.rename_notebook(nb_id, name)
        nb = await db.get_notebook(nb_id)
        if not nb:
            w.json({"error": "not found"}, 404)
            return
        w.json(_serialize_notebook(nb))
        relay.publish(f"notebook.{nb_id}.updated", "notebook")

    async def duplicate_notebook(c: Context, w: Writer) -> None:
        try:
            nb_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid notebook id"}, 400)
            return
        nb = await db.get_notebook(nb_id)
        if not nb:
            w.json({"error": "not found"}, 404)
            return
        new_id = await db.duplicate_notebook(nb_id)
        new_nb = await db.get_notebook(new_id)
        cells = await db.get_all_cells(new_id)
        w.json(
            {
                **_serialize_notebook(new_nb),
                "cells": [_serialize_cell(cell) for cell in cells],
            },
            201,
        )
        relay.publish(f"notebook.{new_id}.created", "notebook")

    async def delete_notebook(c: Context, w: Writer) -> None:
        try:
            nb_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid notebook id"}, 400)
            return
        relay.publish(f"notebook.{nb_id}.deleted", "notebook")
        await db.delete_notebook(nb_id)
        w.empty(204)

    # ── cells ─────────────────────────────────────────────────────────────

    async def create_cell(c: Context, w: Writer) -> None:
        body = await c.req.json()
        nb_id = body.get("notebook_id")
        if not nb_id:
            w.json({"error": "notebook_id required"}, 400)
            return
        cell_type = body.get("cell_type", "code")
        code = body.get("code", "")
        cell_id = await db.insert_cell(nb_id, cell_type=cell_type)
        if code:
            await db.update_input(cell_id, code)
        cell = await db.get_cell(cell_id)
        w.json(_serialize_cell(cell), 201)
        relay.publish(f"notebook.{nb_id}.cell_created", "cell")

    async def get_cell(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid cell id"}, 400)
            return
        cell = await db.get_cell(cell_id)
        if not cell:
            w.json({"error": "not found"}, 404)
            return
        w.json(_serialize_cell(cell))

    async def update_cell(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid cell id"}, 400)
            return
        cell = await db.get_cell(cell_id)
        if not cell:
            w.json({"error": "not found"}, 404)
            return
        body = await c.req.json()
        code = body.get("code")
        if code is not None:
            await db.update_input(cell_id, code)
        cell = await db.get_cell(cell_id)
        w.json(_serialize_cell(cell))
        relay.publish(f"notebook.{cell.notebook_id}.cell_updated", "cell")

    async def move_cell(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid cell id"}, 400)
            return
        body = await c.req.json()
        direction = body.get("direction", "down")
        await db.move_cell(cell_id, direction)
        cell = await db.get_cell(cell_id)
        if not cell:
            w.json({"error": "not found"}, 404)
            return
        w.json(_serialize_cell(cell))
        relay.publish(f"notebook.{cell.notebook_id}.cell_moved", "cell")

    async def duplicate_cell_api(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid cell id"}, 400)
            return
        new_id = await db.duplicate_cell(cell_id)
        if not new_id:
            w.json({"error": "not found"}, 404)
            return
        cell = await db.get_cell(new_id)
        w.json(_serialize_cell(cell), 201)
        relay.publish(f"notebook.{cell.notebook_id}.cell_duplicated", "cell")

    async def delete_cell(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid cell id"}, 400)
            return
        nb_id = await db.get_cell_notebook_id(cell_id)
        await db.delete_cell(cell_id)
        w.empty(204)
        if nb_id:
            relay.publish(f"notebook.{nb_id}.cell_deleted", "cell")

    async def execute_cell(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid cell id"}, 400)
            return
        nb_id = await db.get_cell_notebook_id(cell_id)
        if not nb_id:
            w.json({"error": "not found"}, 404)
            return
        # Optionally update code before executing
        try:
            body = await c.req.json()
            code = body.get("code")
            if code is not None:
                await db.update_input(cell_id, code)
        except Exception:
            pass
        cell = await db.get_cell(cell_id)
        km = await pool.get(nb_id)

        # Shield so execution + DB write complete even if connection drops
        async def _run():
            t0 = time.monotonic()
            output, is_error, exec_count = await km.execute(cell.input)
            elapsed = time.monotonic() - t0
            status = "error" if is_error else "ok"
            await db.update_cell(cell_id, input=cell.input, output=output, status=status, execution_count=exec_count, execution_time=elapsed)
            await db.touch_notebook(nb_id)
            return output, status

        task = asyncio.create_task(_run())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            return

        cell = await db.get_cell(cell_id)
        w.json(_serialize_cell(cell))
        relay.publish(f"notebook.{nb_id}.cell_executed", "cell")

    # ── run all ─────────────────────────────────────────────────────────

    async def run_all_cells(c: Context, w: Writer) -> None:
        try:
            nb_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid notebook id"}, 400)
            return
        cells = await db.get_all_cells(nb_id)
        km = await pool.get(nb_id)
        results = []
        for cell in cells:
            if cell.cell_type != "code" or not cell.input.strip():
                continue
            t0 = time.monotonic()
            output, is_error, exec_count = await km.execute(cell.input)
            elapsed = time.monotonic() - t0
            status = "error" if is_error else "ok"
            await db.update_cell(cell.id, input=cell.input, output=output, status=status, execution_count=exec_count, execution_time=elapsed)
            results.append({"cell_id": cell.id, "status": status})
            if is_error:
                break
        await db.touch_notebook(nb_id)
        w.json({"results": results})
        relay.publish(f"notebook.{nb_id}.cell_executed", "cell")

    # ── insert cell at position ───────────────────────────────────────────

    async def insert_cell(c: Context, w: Writer) -> None:
        body = await c.req.json()
        notebook_id = body.get("notebook_id")
        after_cell_id = body.get("after_cell_id")
        cell_type = body.get("cell_type", "code")
        code = body.get("code", "")

        if not notebook_id:
            w.json({"error": "notebook_id required"}, 400)
            return

        if after_cell_id:
            cell_id = await db.insert_cell_at(notebook_id, after_cell_id, "below", cell_type=cell_type)
        else:
            cell_id = await db.insert_cell(notebook_id, cell_type=cell_type)

        if code:
            await db.update_input(cell_id, code)

        cell = await db.get_cell(cell_id)
        w.json(_serialize_cell(cell))
        relay.publish(f"notebook.{notebook_id}.cell_created", "cell")

    # ── change cell type ───────────────────────────────────────────────────

    async def change_cell_type(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid cell id"}, 400)
            return
        body = await c.req.json()
        cell_type = body.get("cell_type", "code")
        if cell_type not in ("code", "markdown"):
            w.json({"error": "cell_type must be 'code' or 'markdown'"}, 400)
            return
        await db.update_cell_type(cell_id, cell_type)
        cell = await db.get_cell(cell_id)
        if cell:
            w.json(_serialize_cell(cell))
            relay.publish(f"notebook.{cell.notebook_id}.cell_updated", "cell")
        else:
            w.json({"error": "not found"}, 404)

    # ── kernel ────────────────────────────────────────────────────────────

    async def kernel_status(c: Context, w: Writer) -> None:
        try:
            nb_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid notebook id"}, 400)
            return
        state = await pool.get_state(nb_id)
        w.json({"state": state})

    async def kernel_restart(c: Context, w: Writer) -> None:
        try:
            nb_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid notebook id"}, 400)
            return
        await pool.restart(nb_id)
        w.json({"state": "idle"})
        relay.publish(f"notebook.{nb_id}.kernel_restarted", "kernel")

    async def kernel_variables(c: Context, w: Writer) -> None:
        try:
            nb_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid notebook id"}, 400)
            return
        km = await pool.get(nb_id)
        variables = await km.get_variables()
        w.json({"variables": variables})

    async def kernel_interrupt(c: Context, w: Writer) -> None:
        try:
            nb_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid notebook id"}, 400)
            return
        km = await pool.get(nb_id)
        await km.interrupt()
        w.json({"ok": True})

    # ── routes ────────────────────────────────────────────────────────────

    router.get("/notebooks", list_notebooks)
    router.post("/notebooks", create_notebook)
    router.get("/notebooks/*", get_notebook)
    router.put("/notebooks/*", update_notebook)
    router.post("/notebooks/duplicate/*", duplicate_notebook)
    router.delete("/notebooks/*", delete_notebook)

    router.post("/cells", create_cell)
    router.post("/cells/insert", insert_cell)
    router.post("/cells/run-all/*", run_all_cells)
    router.post("/cells/execute/*", execute_cell)
    router.post("/cells/move/*", move_cell)
    router.post("/cells/duplicate/*", duplicate_cell_api)
    router.post("/cells/type/*", change_cell_type)
    router.get("/cells/*", get_cell)
    router.put("/cells/*", update_cell)
    router.delete("/cells/*", delete_cell)

    router.get("/kernel/status/*", kernel_status)
    router.get("/kernel/variables/*", kernel_variables)
    router.post("/kernel/interrupt/*", kernel_interrupt)
    router.post("/kernel/restart/*", kernel_restart)

    return router
