import asyncio

from stario.http import Router
from stario.http.types import Context, Writer

from app.db import Database
from app.kernel import KernelPool


def api_router(db: Database, pool: KernelPool) -> Router:
    router = Router()

    # ── notebooks ─────────────────────────────────────────────────────────

    async def list_notebooks(c: Context, w: Writer) -> None:
        notebooks = await db.get_all_notebooks()
        w.json(
            [
                {"id": n.id, "name": n.name, "updated_at": n.updated_at}
                for n in notebooks
            ]
        )

    async def create_notebook(c: Context, w: Writer) -> None:
        body = await c.req.json()
        name = body.get("name", "Untitled")
        nb_id = await db.create_notebook(name)
        nb = await db.get_notebook(nb_id)
        w.json({"id": nb.id, "name": nb.name, "updated_at": nb.updated_at}, 201)

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
                "id": nb.id,
                "name": nb.name,
                "updated_at": nb.updated_at,
                "cells": [
                    {
                        "id": cell.id,
                        "cell_type": cell.cell_type,
                        "input": cell.input,
                        "output": cell.output,
                        "status": cell.status,
                    }
                    for cell in cells
                ],
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
        w.json({"id": nb.id, "name": nb.name, "updated_at": nb.updated_at})

    async def delete_notebook(c: Context, w: Writer) -> None:
        try:
            nb_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid notebook id"}, 400)
            return
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
        w.json(
            {
                "id": cell.id,
                "notebook_id": cell.notebook_id,
                "cell_type": cell.cell_type,
                "input": cell.input,
                "output": cell.output,
                "status": cell.status,
            },
            201,
        )

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
        w.json(
            {
                "id": cell.id,
                "notebook_id": cell.notebook_id,
                "cell_type": cell.cell_type,
                "input": cell.input,
                "output": cell.output,
                "status": cell.status,
            }
        )

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
        w.json(
            {
                "id": cell.id,
                "notebook_id": cell.notebook_id,
                "cell_type": cell.cell_type,
                "input": cell.input,
                "output": cell.output,
                "status": cell.status,
            }
        )

    async def delete_cell(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.json({"error": "invalid cell id"}, 400)
            return
        await db.delete_cell(cell_id)
        w.empty(204)

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
            output, is_error = await km.execute(cell.input)
            status = "error" if is_error else "ok"
            await db.update_cell(cell_id, input=cell.input, output=output, status=status)
            await db.touch_notebook(nb_id)
            return output, status

        task = asyncio.create_task(_run())
        try:
            output, status = await asyncio.shield(task)
        except asyncio.CancelledError:
            return

        w.json(
            {
                "id": cell_id,
                "input": cell.input,
                "output": output,
                "status": status,
            }
        )

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

    # ── routes ────────────────────────────────────────────────────────────

    router.get("/notebooks", list_notebooks)
    router.post("/notebooks", create_notebook)
    router.get("/notebooks/*", get_notebook)
    router.put("/notebooks/*", update_notebook)
    router.delete("/notebooks/*", delete_notebook)

    router.post("/cells", create_cell)
    router.post("/cells/execute/*", execute_cell)
    router.get("/cells/*", get_cell)
    router.put("/cells/*", update_cell)
    router.delete("/cells/*", delete_cell)

    router.get("/kernel/status/*", kernel_status)
    router.post("/kernel/restart/*", kernel_restart)

    return router
