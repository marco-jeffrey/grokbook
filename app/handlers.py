from stario.datastar.signals import get_signals
from stario.http import Router
from stario.http.types import Context, Writer

from app.db import Database
from app.kernel import KernelPool
from app.views import notebook, page, sidebar_view


def app_router(db: Database, pool: KernelPool) -> Router:
    router = Router()

    # ── pages ─────────────────────────────────────────────────────────────

    async def index(c: Context, w: Writer) -> None:
        nb_id = await db.get_latest_notebook_id()
        if nb_id is None:
            nb_id = await db.create_notebook()
        w.redirect(f"/nb/{nb_id}")

    async def nb_page(c: Context, w: Writer) -> None:
        try:
            nb_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        nb = await db.get_notebook(nb_id)
        if not nb:
            w.text("Not Found", 404)
            return
        notebooks = await db.get_all_notebooks()
        cells = await db.get_all_cells(nb_id)
        w.html(page(nb, notebooks, cells))

    # ── notebook switching ────────────────────────────────────────────────

    async def _patch_all(w: Writer, nb_id: int) -> None:
        """Patch notebook content + sidebar + sync notebook_id signal."""
        notebooks = await db.get_all_notebooks()
        cells = await db.get_all_cells(nb_id)
        w.patch(element=notebook(cells, nb_id), selector="#notebook")
        w.patch(element=sidebar_view(nb_id, notebooks), selector="#sidebar")
        w.sync({"notebook_id": nb_id, "last_status": "", "focus_cell": ""})

    async def switch_notebook(c: Context, w: Writer) -> None:
        try:
            nb_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        nb = await db.get_notebook(nb_id)
        if not nb:
            w.text("Not Found", 404)
            return
        await _patch_all(w, nb_id)

    async def new_notebook(c: Context, w: Writer) -> None:
        nb_id = await db.create_notebook()
        await _patch_all(w, nb_id)

    async def _patch_sidebar(w: Writer, active_id: int, **kwargs) -> None:
        notebooks = await db.get_all_notebooks()
        w.patch(
            element=sidebar_view(active_id, notebooks, **kwargs),
            selector="#sidebar",
        )

    async def _get_active_id(c: Context) -> int:
        signals = await get_signals(c.req)
        return int(signals.get("notebook_id", 0))

    async def nb_menu(c: Context, w: Writer) -> None:
        nb_id = int(c.req.tail)
        active_id = await _get_active_id(c)
        await _patch_sidebar(w, active_id, menu_id=nb_id)

    async def nb_menu_close(c: Context, w: Writer) -> None:
        active_id = await _get_active_id(c)
        await _patch_sidebar(w, active_id)

    async def nb_rename_mode(c: Context, w: Writer) -> None:
        nb_id = int(c.req.tail)
        active_id = await _get_active_id(c)
        await _patch_sidebar(w, active_id, renaming_id=nb_id)

    async def nb_rename(c: Context, w: Writer) -> None:
        nb_id = int(c.req.tail)
        signals = await get_signals(c.req)
        name = signals.get(f"rename_{nb_id}", "").strip()
        if name:
            await db.rename_notebook(nb_id, name)
        active_id = int(signals.get("notebook_id", 0))
        await _patch_sidebar(w, active_id)

    async def nb_duplicate(c: Context, w: Writer) -> None:
        nb_id = int(c.req.tail)
        new_id = await db.duplicate_notebook(nb_id)
        await _patch_all(w, new_id)

    async def nb_delete(c: Context, w: Writer) -> None:
        nb_id = int(c.req.tail)
        active_id = await _get_active_id(c)
        await db.delete_notebook(nb_id)
        # If we deleted the active notebook, switch to another
        if nb_id == active_id:
            fallback = await db.get_latest_notebook_id()
            if fallback is None:
                fallback = await db.create_notebook()
            await _patch_all(w, fallback)
        else:
            await _patch_sidebar(w, active_id)

    # ── cells ─────────────────────────────────────────────────────────────

    async def _patch_notebook(w: Writer, nb_id: int) -> None:
        cells = await db.get_all_cells(nb_id)
        w.patch(element=notebook(cells, nb_id), selector="#notebook")

    async def add_cell(c: Context, w: Writer) -> None:
        signals = await get_signals(c.req)
        nb_id = int(signals.get("notebook_id", 0))
        new_id = await db.insert_cell(nb_id)
        await _patch_notebook(w, nb_id)
        w.sync({"focus_cell": str(new_id)})

    async def execute_cell(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        nb_id = await db.get_cell_notebook_id(cell_id)
        if not nb_id:
            w.text("Not Found", 404)
            return

        signals = await get_signals(c.req)
        code = signals.get(f"cell_{cell_id}", "")

        km = await pool.get(nb_id)
        output, is_error = await km.execute(code)
        status = "error" if is_error else "ok"

        await db.update_cell(cell_id, input=code, output=output, status=status)
        await db.touch_notebook(nb_id)

        next_id = await db.get_next_cell_id(cell_id)
        await _patch_notebook(w, nb_id)
        w.sync(
            {
                "last_status": status,
                "focus_cell": str(next_id) if next_id else "",
            }
        )

    async def save_cell(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        body = await c.req.json()
        code = body.get(f"cell_{cell_id}", "")
        await db.update_input(cell_id, code)
        w.empty(204)

    # ── kernel ────────────────────────────────────────────────────────────

    async def kernel_restart(c: Context, w: Writer) -> None:
        signals = await get_signals(c.req)
        nb_id = int(signals.get("notebook_id", 0))
        await pool.restart(nb_id)
        w.sync({"kernel_state": "idle"})

    # ── autocomplete / inspect ────────────────────────────────────────────

    async def complete_handler(c: Context, w: Writer) -> None:
        body = await c.req.json()
        nb_id = body.get("notebook_id", 0)
        if not nb_id:
            w.json({"matches": [], "cursor_start": 0, "cursor_end": 0})
            return
        km = await pool.get(nb_id)
        result = await km.complete(body.get("code", ""), body.get("cursor_pos", 0))
        w.json(result)

    async def inspect_handler(c: Context, w: Writer) -> None:
        body = await c.req.json()
        nb_id = body.get("notebook_id", 0)
        if not nb_id:
            w.json({"text": ""})
            return
        km = await pool.get(nb_id)
        text = await km.inspect(body.get("code", ""), body.get("cursor_pos", 0))
        w.json({"text": text})

    # ── routes ────────────────────────────────────────────────────────────

    router.get("/", index)
    router.get("/nb/*", nb_page)
    router.post("/nb/new", new_notebook)
    router.post("/nb/switch/*", switch_notebook)
    router.post("/nb/menu/*", nb_menu)
    router.post("/nb/menu-close", nb_menu_close)
    router.post("/nb/rename-mode/*", nb_rename_mode)
    router.post("/nb/rename/*", nb_rename)
    router.post("/nb/duplicate/*", nb_duplicate)
    router.post("/nb/delete/*", nb_delete)
    router.post("/cells/new", add_cell)
    router.post("/cells/execute/*", execute_cell)
    router.post("/cells/save/*", save_cell)
    router.post("/kernel/restart", kernel_restart)
    router.post("/complete", complete_handler)
    router.post("/inspect", inspect_handler)

    return router
