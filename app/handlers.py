from stario.datastar.signals import get_signals
from stario.http import Router
from stario.http.types import Context, Writer

from app.db import Database
from app.kernel import KernelManager
from app.views import notebook, page


def notebook_router(db: Database, kernel: KernelManager) -> Router:
    router = Router()

    async def _patch_notebook(w: Writer) -> None:
        cells = await db.get_all_cells()
        w.patch(element=notebook(cells), selector="#notebook")

    async def index(c: Context, w: Writer) -> None:
        cells = await db.get_all_cells()
        w.html(page(cells))

    async def add_cell(c: Context, w: Writer):
        await db.insert_cell()
        await _patch_notebook(w)
        return

    async def execute_cell(c: Context, w: Writer) -> None:
        # tail = "new"   → insert empty cell
        # tail = "{int}" → update input + execute + save output
        tail = c.req.tail
        print(tail)
        try:
            cell_id = int(tail)
        except ValueError:
            w.text("Not Found", 404)
            return

        signals = await get_signals(c.req)
        code = signals.get(f"cell_{cell_id}", "")
        output, _ = await kernel.execute(code)
        await db.update_cell(cell_id, input=code, output=output)
        await _patch_notebook(w)

    async def inspect(c: Context, w: Writer) -> None:
        body = await c.req.json()
        text = await kernel.inspect(body.get("code", ""), body.get("cursor_pos", 0))
        w.json({"text": text})

    async def complete(c: Context, w: Writer) -> None:
        body = await c.req.json()
        result = await kernel.complete(body.get("code", ""), body.get("cursor_pos", 0))
        w.json(result)

    router.get("/", index)
    router.post("/cells/new", add_cell)
    router.post("/cells/execute/*", execute_cell)
    router.post("/complete", complete)
    router.post("/inspect", inspect)

    return router
