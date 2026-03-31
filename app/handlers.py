import asyncio

from stario import Relay
from stario.datastar.signals import get_signals
from stario.http import Router
from stario.http.types import Context, Writer

from app.db import Database
from app.ipynb import export_ipynb, import_ipynb
from app.kernel import KernelPool
from app.views import _render_output, notebook, page, sidebar_view


def app_router(db: Database, pool: KernelPool, relay: Relay[str]) -> Router:
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
        w.html(page(nb, notebooks, cells, c.url_for))

    # ── SSE live-push ───────────────────────────────────────────────────

    async def events(c: Context, w: Writer) -> None:
        """SSE endpoint — browser connects on page load for live updates."""
        signals = await get_signals(c.req)
        nb_id = int(signals.get("notebook_id", 0))
        if not nb_id:
            w.empty(204)
            return

        # Send initial state
        notebooks = await db.get_all_notebooks()
        cells = await db.get_all_cells(nb_id)
        w.patch(element=notebook(cells, nb_id), selector="#notebook")
        w.patch(element=sidebar_view(nb_id, notebooks), selector="#sidebar")

        # Loop: wait for relay events, re-patch. Heartbeat every 15s
        # to prevent proxies/browsers from closing idle connections.
        # IMPORTANT: We use asyncio.wait (not wait_for) so the pending
        # __anext__ task is NOT cancelled on timeout — cancelling it would
        # kill the async generator and destroy the relay subscription.
        sub = relay.subscribe("notebook.*")
        sub_iter = sub.__aiter__()
        pending_next: asyncio.Task | None = None
        alive = w.alive()
        async with alive:
            try:
                while True:
                    if pending_next is None:
                        pending_next = asyncio.create_task(sub_iter.__anext__())
                    done, _ = await asyncio.wait({pending_next}, timeout=15)
                    if not done:
                        # Timeout — send heartbeat, reuse the same pending task
                        w.write(b": heartbeat\n\n")
                        continue
                    try:
                        subject, payload = pending_next.result()
                    except StopAsyncIteration:
                        break
                    pending_next = None

                    parts = subject.split(".")
                    event_nb_id = int(parts[1]) if len(parts) >= 2 else 0

                    notebooks = await db.get_all_notebooks()
                    w.patch(element=sidebar_view(nb_id, notebooks), selector="#sidebar")

                    if event_nb_id == nb_id:
                        cells = await db.get_all_cells(nb_id)
                        w.patch(element=notebook(cells, nb_id), selector="#notebook")
            finally:
                if pending_next is not None:
                    pending_next.cancel()
                await sub_iter.aclose()

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
        relay.publish(f"notebook.{nb_id}.created", "notebook")

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
        relay.publish(f"notebook.{nb_id}.updated", "notebook")

    async def nb_duplicate(c: Context, w: Writer) -> None:
        nb_id = int(c.req.tail)
        new_id = await db.duplicate_notebook(nb_id)
        await _patch_all(w, new_id)
        relay.publish(f"notebook.{new_id}.created", "notebook")

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
        relay.publish(f"notebook.{nb_id}.deleted", "notebook")

    # ── cells ─────────────────────────────────────────────────────────────

    async def _patch_notebook(w: Writer, nb_id: int) -> None:
        cells = await db.get_all_cells(nb_id)
        w.patch(element=notebook(cells, nb_id), selector="#notebook")

    async def add_cell(c: Context, w: Writer) -> None:
        signals = await get_signals(c.req)
        nb_id = int(signals.get("notebook_id", 0))
        new_id = await db.insert_cell(nb_id, cell_type="code")
        await _patch_notebook(w, nb_id)
        w.sync({"focus_cell": str(new_id)})
        relay.publish(f"notebook.{nb_id}.cell_created", "cell")

    async def add_md_cell(c: Context, w: Writer) -> None:
        signals = await get_signals(c.req)
        nb_id = int(signals.get("notebook_id", 0))
        await db.insert_cell(nb_id, cell_type="markdown")
        await _patch_notebook(w, nb_id)
        relay.publish(f"notebook.{nb_id}.cell_created", "cell")

    async def save_md_cell(c: Context, w: Writer) -> None:
        """Save markdown cell content and re-render notebook."""
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        signals = await get_signals(c.req)
        code = signals.get(f"cell_{cell_id}", "")
        await db.update_input(cell_id, code)
        nb_id = await db.get_cell_notebook_id(cell_id)
        if nb_id:
            await _patch_notebook(w, nb_id)
            relay.publish(f"notebook.{nb_id}.cell_updated", "cell")

    async def _stream_execute(cell_id: int, nb_id: int, code: str, w: Writer) -> str:
        """Stream execution output to the browser, then save to DB."""
        km = await pool.get(nb_id)
        output = "(no output)"
        is_error = False
        exec_count = 0

        async for output, is_error, is_final, exec_count in km.execute_streaming(code):
            w.patch(
                element=_render_output(output, is_error, cell_id),
                selector=f"#output-{cell_id}",
            )
            if is_final:
                break

        status = "error" if is_error else "ok"
        await db.update_cell(cell_id, input=code, output=output, status=status, execution_count=exec_count)
        await db.touch_notebook(nb_id)
        return status

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

        # Shield so execution + DB write complete even if connection drops
        task = asyncio.create_task(_stream_execute(cell_id, nb_id, code, w))
        try:
            status = await asyncio.shield(task)
        except asyncio.CancelledError:
            return  # connection dropped, task keeps running in background

        next_id = await db.get_next_cell_id(cell_id)
        await _patch_notebook(w, nb_id)
        w.sync(
            {
                "last_status": status,
                "focus_cell": str(next_id) if next_id else "",
            }
        )
        relay.publish(f"notebook.{nb_id}.cell_executed", "cell")

    async def save_cell(c: Context, w: Writer) -> None:
        """Autosave — silently persists input without triggering UI refresh."""
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        body = await c.req.json()
        code = body.get(f"cell_{cell_id}", "")
        await db.update_input(cell_id, code)
        w.empty(204)
        # NOTE: No relay.publish() here — autosave is a quiet background
        # operation. Publishing would re-patch the notebook and kick the
        # user out of markdown edit mode or lose cursor position.

    # ── firePost-targeted handlers (return JSON, relay pushes UI update) ──

    async def add_cell_above(c: Context, w: Writer) -> None:
        try:
            ref_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        nb_id = await db.get_cell_notebook_id(ref_id)
        if not nb_id:
            w.text("Not Found", 404)
            return
        new_id = await db.insert_cell_at(nb_id, ref_id, "above")
        w.json({"id": new_id})
        relay.publish(f"notebook.{nb_id}.cell_created", "cell")

    async def add_cell_below(c: Context, w: Writer) -> None:
        try:
            ref_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        nb_id = await db.get_cell_notebook_id(ref_id)
        if not nb_id:
            w.text("Not Found", 404)
            return
        new_id = await db.insert_cell_at(nb_id, ref_id, "below")
        w.json({"id": new_id})
        relay.publish(f"notebook.{nb_id}.cell_created", "cell")

    async def convert_cell(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        cell = await db.get_cell(cell_id)
        if not cell:
            w.text("Not Found", 404)
            return
        new_type = "markdown" if cell.cell_type == "code" else "code"
        await db.update_cell_type(cell_id, new_type)
        w.json({"ok": True})
        relay.publish(f"notebook.{cell.notebook_id}.cell_updated", "cell")

    async def delete_cell(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        nb_id = await db.get_cell_notebook_id(cell_id)
        if not nb_id:
            w.text("Not Found", 404)
            return
        await db.delete_cell(cell_id)
        w.json({"ok": True})
        relay.publish(f"notebook.{nb_id}.cell_deleted", "cell")

    async def move_cell_up(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        nb_id = await db.get_cell_notebook_id(cell_id)
        if not nb_id:
            w.text("Not Found", 404)
            return
        await db.move_cell(cell_id, "up")
        w.json({"ok": True})
        relay.publish(f"notebook.{nb_id}.cell_moved", "cell")

    async def move_cell_down(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        nb_id = await db.get_cell_notebook_id(cell_id)
        if not nb_id:
            w.text("Not Found", 404)
            return
        await db.move_cell(cell_id, "down")
        w.json({"ok": True})
        relay.publish(f"notebook.{nb_id}.cell_moved", "cell")

    async def duplicate_cell(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        nb_id = await db.get_cell_notebook_id(cell_id)
        if not nb_id:
            w.text("Not Found", 404)
            return
        await db.duplicate_cell(cell_id)
        w.json({"ok": True})
        relay.publish(f"notebook.{nb_id}.cell_duplicated", "cell")

    # ── run all ─────────────────────────────────────────────────────────

    async def run_all(c: Context, w: Writer) -> None:
        signals = await get_signals(c.req)
        nb_id = int(signals.get("notebook_id", 0))
        if not nb_id:
            w.empty(204)
            return
        cells = await db.get_all_cells(nb_id)
        km = await pool.get(nb_id)

        async def _run_all():
            for cell in cells:
                if cell.cell_type != "code" or not cell.input.strip():
                    continue
                output, is_error, exec_count = await km.execute(cell.input)
                status = "error" if is_error else "ok"
                await db.update_cell(cell.id, input=cell.input, output=output, status=status, execution_count=exec_count)
                # Patch after each cell so user sees progress
                w.patch(element=_render_output(output, is_error, cell.id), selector=f"#output-{cell.id}")
                if is_error:
                    break
            await db.touch_notebook(nb_id)

        task = asyncio.create_task(_run_all())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            return

        await _patch_notebook(w, nb_id)
        relay.publish(f"notebook.{nb_id}.cell_executed", "cell")

    # ── kernel ────────────────────────────────────────────────────────────

    async def kernel_variables(c: Context, w: Writer) -> None:
        body = await c.req.json()
        nb_id = int(body.get("notebook_id", 0))
        if not nb_id:
            w.json({"variables": []})
            return
        km = await pool.get(nb_id)
        variables = await km.get_variables()
        w.json({"variables": variables})

    async def kernel_interrupt(c: Context, w: Writer) -> None:
        signals = await get_signals(c.req)
        nb_id = int(signals.get("notebook_id", 0))
        km = await pool.get(nb_id)
        await km.interrupt()
        w.sync({"executing": False})

    async def kernel_restart(c: Context, w: Writer) -> None:
        signals = await get_signals(c.req)
        nb_id = int(signals.get("notebook_id", 0))
        await pool.restart(nb_id)
        w.sync({"kernel_state": "idle"})
        relay.publish(f"notebook.{nb_id}.kernel_restarted", "kernel")

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

    # ── import / export ──────────────────────────────────────────────────

    async def nb_export(c: Context, w: Writer) -> None:
        """Download notebook as .ipynb file."""
        try:
            nb_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        nb = await db.get_notebook(nb_id)
        if not nb:
            w.text("Not Found", 404)
            return
        cells = await db.get_all_cells(nb_id)
        ipynb_json = export_ipynb(nb, cells)
        safe_name = nb.name.replace('"', "'")
        w.headers.set(b"content-type", b"application/json")
        w.headers.set(b"content-disposition", f'attachment; filename="{safe_name}.ipynb"'.encode())
        w.text(ipynb_json)

    async def nb_import(c: Context, w: Writer) -> None:
        """Import .ipynb from JSON body {name, content}."""
        body = await c.req.json()
        content = body.get("content", "")
        name = body.get("name", "").replace(".ipynb", "") or None
        try:
            nb_id = await import_ipynb(db, content.encode("utf-8"), name=name)
        except (ValueError, Exception) as exc:
            w.json({"error": str(exc)}, 400)
            return
        w.json({"id": nb_id})
        relay.publish(f"notebook.{nb_id}.created", "notebook")

    # ── routes ────────────────────────────────────────────────────────────

    router.get("/", index)
    router.get("/events", events)
    router.get("/nb/*", nb_page)
    router.post("/nb/new", new_notebook)
    router.post("/nb/switch/*", switch_notebook)
    router.post("/nb/menu/*", nb_menu)
    router.post("/nb/menu-close", nb_menu_close)
    router.post("/nb/rename-mode/*", nb_rename_mode)
    router.post("/nb/rename/*", nb_rename)
    router.post("/nb/duplicate/*", nb_duplicate)
    router.post("/nb/delete/*", nb_delete)
    router.get("/nb/export/*", nb_export)
    router.post("/nb/import", nb_import)
    router.post("/cells/run-all", run_all)
    router.post("/cells/new", add_cell)
    router.post("/cells/new-md", add_md_cell)
    router.post("/cells/save-md/*", save_md_cell)
    router.post("/cells/execute/*", execute_cell)
    router.post("/cells/save/*", save_cell)
    router.post("/cells/delete/*", delete_cell)
    router.post("/cells/move-up/*", move_cell_up)
    router.post("/cells/move-down/*", move_cell_down)
    router.post("/cells/duplicate/*", duplicate_cell)
    router.post("/cells/new-above/*", add_cell_above)
    router.post("/cells/new-below/*", add_cell_below)
    router.post("/cells/convert/*", convert_cell)
    router.post("/kernel/interrupt", kernel_interrupt)
    router.post("/kernel/restart", kernel_restart)
    router.post("/kernel/variables", kernel_variables)
    router.post("/complete", complete_handler)
    router.post("/inspect", inspect_handler)

    return router
