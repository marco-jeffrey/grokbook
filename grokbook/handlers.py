import asyncio
import time

from stario import Relay
from stario.html import Div, Pre, SafeString
from stario.http import Router
from stario.http.types import Context, Writer

from grokbook import envs
from grokbook.db import Database
from grokbook.ipynb import export_ipynb, import_ipynb
from grokbook.kernel import KernelPool
from grokbook.views import _path_suggestions_view, _render_output, kernel_selector, notebook, page, sidebar_view


def app_router(db: Database, pool: KernelPool, relay: Relay[str]) -> Router:
    router = Router()

    async def _get_kernel(nb_id: int):
        """Get or create the kernel for a notebook, using its stored kernel_env."""
        nb = await db.get_notebook(nb_id)
        env = nb.kernel_env if nb else None
        return await pool.get(nb_id, env)

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
        projects, notebooks_by_project = await _get_sidebar_data()
        cells = await db.get_all_cells(nb_id)
        if not envs.get_all():
            await envs.refresh()
        w.html(page(nb, projects, notebooks_by_project, cells, c.url_for, envs=envs.get_all()))

    # ── SSE live-push ───────────────────────────────────────────────────

    async def events(c: Context, w: Writer) -> None:
        """SSE endpoint — browser connects on page load for live updates."""
        signals = await c.signals()
        nb_id = int(signals.get("notebook_id", 0))
        if not nb_id:
            w.empty(204)
            return

        # Send initial state
        projects, notebooks_by_project = await _get_sidebar_data()
        cells = await db.get_all_cells(nb_id)
        nb = await db.get_notebook(nb_id)
        w.patch(element=notebook(cells, nb_id), selector="#notebook")
        w.patch(element=sidebar_view(nb_id, projects, notebooks_by_project), selector="#sidebar")
        # Sync kernel_env signal to match the (possibly different) notebook we're now viewing
        w.sync({"kernel_env": (nb.kernel_env if nb else "") or ""})

        # Loop: wait for relay events, re-patch. Heartbeat every 15s
        # to prevent proxies/browsers from closing idle connections.
        # IMPORTANT: We use asyncio.wait (not wait_for) so the pending
        # __anext__ task is NOT cancelled on timeout — cancelling it would
        # kill the async generator and destroy the relay subscription.
        # Subscribe to all events (notebook.* and project.*) so sidebar
        # stays in sync across tabs.
        sub = relay.subscribe("*")
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

                    projects, notebooks_by_project = await _get_sidebar_data()
                    w.patch(element=sidebar_view(nb_id, projects, notebooks_by_project), selector="#sidebar")

                    if event_nb_id == nb_id:
                        cells = await db.get_all_cells(nb_id)
                        w.patch(element=notebook(cells, nb_id), selector="#notebook")
                        # If this was a kernel env change in another tab, sync the signal
                        if subject.endswith(".kernel_env_changed"):
                            nb2 = await db.get_notebook(nb_id)
                            w.sync({"kernel_env": (nb2.kernel_env if nb2 else "") or ""})
            finally:
                if pending_next is not None:
                    pending_next.cancel()
                try:
                    await sub_iter.aclose()
                except RuntimeError:
                    pass  # generator already running during cancellation

    # ── notebook switching ────────────────────────────────────────────────

    async def _patch_all(w: Writer, nb_id: int) -> None:
        """Switch active notebook: sync signal + update URL.

        The client has a data-effect watching $notebook_id that reconnects
        the /events SSE stream with the new id, which then pushes fresh
        #notebook + #sidebar patches. So all we do here is flip the signal
        and pushState the URL — no patching needed.
        """
        w.sync({"notebook_id": nb_id, "last_status": "", "focus_cell": ""})
        w.execute(f"window.history.pushState(null,'','/nb/{nb_id}')")

    async def new_notebook(c: Context, w: Writer) -> None:
        signals = await c.signals()
        project_id = int(signals.get("project_id", 1))
        nb_id = await db.create_notebook(project_id=project_id)
        await _patch_all(w, nb_id)
        relay.publish(f"notebook.{nb_id}.created", "notebook")

    async def new_notebook_in_project(c: Context, w: Writer) -> None:
        try:
            project_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        nb_id = await db.create_notebook(project_id=project_id)
        await _patch_all(w, nb_id)
        relay.publish(f"notebook.{nb_id}.created", "notebook")

    async def _get_sidebar_data():
        projects = await db.get_all_projects()
        notebooks_by_project = {}
        for p in projects:
            notebooks_by_project[p.id] = await db.get_notebooks_by_project(p.id)
        return projects, notebooks_by_project

    async def _patch_sidebar(w: Writer, active_id: int, **kwargs) -> None:
        projects, notebooks_by_project = await _get_sidebar_data()
        w.patch(
            element=sidebar_view(active_id, projects, notebooks_by_project, **kwargs),
            selector="#sidebar",
        )

    async def _get_active_id(c: Context) -> int:
        signals = await c.signals()
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
        signals = await c.signals()
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

    # ── projects ──────────────────────────────────────────────────────────

    async def new_project(c: Context, w: Writer) -> None:
        project_id = await db.create_project()
        active_id = await _get_active_id(c)
        await _patch_sidebar(w, active_id)
        relay.publish(f"project.{project_id}.created", "project")

    async def project_menu(c: Context, w: Writer) -> None:
        project_id = int(c.req.tail)
        active_id = await _get_active_id(c)
        await _patch_sidebar(w, active_id, project_menu_id=project_id)

    async def project_menu_close(c: Context, w: Writer) -> None:
        active_id = await _get_active_id(c)
        await _patch_sidebar(w, active_id)

    async def project_rename_mode(c: Context, w: Writer) -> None:
        project_id = int(c.req.tail)
        active_id = await _get_active_id(c)
        await _patch_sidebar(w, active_id, project_renaming_id=project_id)

    async def project_rename(c: Context, w: Writer) -> None:
        project_id = int(c.req.tail)
        signals = await c.signals()
        name = signals.get(f"proj_rename_{project_id}", "").strip()
        if name:
            await db.rename_project(project_id, name)
        active_id = int(signals.get("notebook_id", 0))
        await _patch_sidebar(w, active_id)
        relay.publish(f"project.{project_id}.updated", "project")

    async def project_delete(c: Context, w: Writer) -> None:
        project_id = int(c.req.tail)
        active_id = await _get_active_id(c)
        await db.delete_project(project_id)
        await _patch_sidebar(w, active_id)
        relay.publish(f"project.{project_id}.deleted", "project")

    # ── cells ─────────────────────────────────────────────────────────────

    async def _patch_notebook(w: Writer, nb_id: int) -> None:
        cells = await db.get_all_cells(nb_id)
        w.patch(element=notebook(cells, nb_id), selector="#notebook")

    async def add_cell(c: Context, w: Writer) -> None:
        signals = await c.signals()
        nb_id = int(signals.get("notebook_id", 0))
        new_id = await db.insert_cell(nb_id, cell_type="code")
        await _patch_notebook(w, nb_id)
        w.sync({"focus_cell": str(new_id)})
        relay.publish(f"notebook.{nb_id}.cell_created", "cell")

    async def add_md_cell(c: Context, w: Writer) -> None:
        signals = await c.signals()
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
        signals = await c.signals()
        code = signals.get(f"cell_{cell_id}", "")
        await db.update_input(cell_id, code)
        nb_id = await db.get_cell_notebook_id(cell_id)
        if nb_id:
            await _patch_notebook(w, nb_id)
            relay.publish(f"notebook.{nb_id}.cell_updated", "cell")

    async def _stream_execute(cell_id: int, nb_id: int, code: str, w: Writer) -> str:
        """Stream execution output to the browser, then save to DB."""
        km = await _get_kernel(nb_id)
        output = ""
        is_error = False
        exec_count = 0

        t0 = time.monotonic()
        async for output, is_error, is_final, exec_count in km.execute_streaming(code):
            w.patch(
                element=_render_output(output, is_error, cell_id),
                selector=f"#output-{cell_id}",
            )
            if is_final:
                break
        elapsed = time.monotonic() - t0

        status = "error" if is_error else "ok"
        await db.update_cell(cell_id, input=code, output=output, status=status, execution_count=exec_count, execution_time=elapsed)
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

        signals = await c.signals()
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

    # ── clear outputs ──────────────────────────────────────────────────

    async def clear_output(c: Context, w: Writer) -> None:
        try:
            cell_id = int(c.req.tail)
        except ValueError:
            w.text("Not Found", 404)
            return
        nb_id = await db.get_cell_notebook_id(cell_id)
        if not nb_id:
            w.text("Not Found", 404)
            return
        await db.clear_cell_output(cell_id)
        w.json({"ok": True})
        relay.publish(f"notebook.{nb_id}.cell_updated", "cell")

    async def clear_all_outputs(c: Context, w: Writer) -> None:
        signals = await c.signals()
        nb_id = int(signals.get("notebook_id", 0))
        if not nb_id:
            w.empty(204)
            return
        await db.clear_all_outputs(nb_id)
        await _patch_notebook(w, nb_id)
        relay.publish(f"notebook.{nb_id}.cell_updated", "cell")

    # ── run all ─────────────────────────────────────────────────────────

    async def run_all(c: Context, w: Writer) -> None:
        signals = await c.signals()
        nb_id = int(signals.get("notebook_id", 0))
        if not nb_id:
            w.empty(204)
            return
        cells = await db.get_all_cells(nb_id)
        km = await _get_kernel(nb_id)

        async def _run_all():
            for cell in cells:
                if cell.cell_type != "code" or not cell.input.strip():
                    continue
                t0 = time.monotonic()
                output, is_error, exec_count = await km.execute(cell.input)
                elapsed = time.monotonic() - t0
                status = "error" if is_error else "ok"
                await db.update_cell(cell.id, input=cell.input, output=output, status=status, execution_count=exec_count, execution_time=elapsed)
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
        km = await _get_kernel(nb_id)
        variables = await km.get_variables()
        w.json({"variables": variables})

    async def kernel_interrupt(c: Context, w: Writer) -> None:
        signals = await c.signals()
        nb_id = int(signals.get("notebook_id", 0))
        km = await _get_kernel(nb_id)
        await km.interrupt()
        w.sync({"executing": False})

    async def kernel_restart(c: Context, w: Writer) -> None:
        signals = await c.signals()
        nb_id = int(signals.get("notebook_id", 0))
        await pool.restart(nb_id)
        w.sync({"kernel_state": "idle"})
        relay.publish(f"notebook.{nb_id}.kernel_restarted", "kernel")

    # ── kernel env selection ──────────────────────────────────────────────

    async def kernel_env_set(c: Context, w: Writer) -> None:
        """POST /kernel/env/set — switch a notebook's python interpreter."""
        signals = await c.signals()
        nb_id = int(signals.get("notebook_id", 0))
        env_path = signals.get("pending_env_path", "") or ""
        if not nb_id:
            w.json({"error": "missing notebook_id"}, 400)
            return
        env = envs.find_by_path(env_path) if env_path else None
        if env_path and not env:
            w.json({"error": f"unknown env: {env_path}"}, 400)
            return
        if env and not env.has_ipykernel:
            w.json({"error": "ipykernel not installed in this env"}, 400)
            return
        # Persist and swap the kernel. The next cell execute will spawn a
        # fresh kernel with the new interpreter.
        await db.set_notebook_kernel_env(nb_id, env_path or None)
        await pool.set_env(nb_id, env_path or None)
        label = env.name if env else "default"
        w.sync({"kernel_env": env_path, "kernel_state": "idle", "last_status": f"kernel → {label}"})
        relay.publish(f"notebook.{nb_id}.kernel_env_changed", "kernel")

    async def kernel_envs_refresh(c: Context, w: Writer) -> None:
        """POST /kernel/envs/refresh — rescan envs and re-patch the selector."""
        await envs.refresh()
        w.patch(element=kernel_selector(envs.get_all()), selector="#kernel-selector")

    async def kernel_envs_complete(c: Context, w: Writer) -> None:
        """POST /kernel/envs/complete — path autocomplete suggestions."""
        signals = await c.signals()
        prefix = (signals.get("new_env_path", "") or "").strip()
        suggestions = envs.complete_path(prefix)
        w.patch(element=_path_suggestions_view(suggestions), selector="#path-suggestions")

    async def kernel_envs_add_custom(c: Context, w: Writer) -> None:
        """POST /kernel/envs/add — add a user-supplied python path."""
        signals = await c.signals()
        path = (signals.get("new_env_path", "") or "").strip()
        if not path:
            w.sync({"new_env_error": "path is required"})
            return
        env = await envs.add_custom_env(path)
        if env is None:
            w.sync({"new_env_error": f"not a valid Python interpreter: {path}"})
            return
        w.sync({"new_env_path": "", "new_env_error": ""})
        w.patch(element=kernel_selector(envs.get_all()), selector="#kernel-selector")

    async def kernel_envs_remove_custom(c: Context, w: Writer) -> None:
        """POST /kernel/envs/remove — remove a custom env by path (tail)."""
        signals = await c.signals()
        path = (signals.get("remove_env_path", "") or "").strip()
        if not path:
            w.empty(204)
            return
        await envs.remove_custom_env(path)
        w.patch(element=kernel_selector(envs.get_all()), selector="#kernel-selector")

    async def kernel_env_install(c: Context, w: Writer) -> None:
        """POST /kernel/env/install — install ipykernel into an env, stream log."""
        signals = await c.signals()
        env_path = signals.get("install_env_path", "") or ""
        if not env_path:
            w.json({"error": "missing install_env_path"}, 400)
            return
        w.sync({"install_running": True, "install_done": False, "install_ok": False})
        # Reset log to empty
        w.patch(
            element=Pre(
                {
                    "id": "install-log",
                    "class": "bg-black p-2 font-mono text-[11px] max-h-64 overflow-auto text-zinc-300 whitespace-pre-wrap mb-3 rounded",
                },
                "",
            ),
            selector="#install-log",
        )
        ok = False
        async for kind, line in envs.install_ipykernel(env_path):
            if kind == "done":
                ok = line == "ok"
                if not ok:
                    w.patch(
                        element=Div({"class": "text-red-400"}, f"\n✗ {line}\n"),
                        selector="#install-log",
                        mode="append",
                    )
                else:
                    w.patch(
                        element=Div({"class": "text-green-400"}, "\n✓ installed\n"),
                        selector="#install-log",
                        mode="append",
                    )
            else:
                w.patch(
                    element=Div(line + "\n"),
                    selector="#install-log",
                    mode="append",
                )
        if ok:
            # Re-probe + update cache + re-render selector
            await envs.refresh()
            w.patch(element=kernel_selector(envs.get_all()), selector="#kernel-selector")
        w.sync({"install_running": False, "install_done": True, "install_ok": ok})

    # ── autocomplete / inspect ────────────────────────────────────────────

    async def complete_handler(c: Context, w: Writer) -> None:
        body = await c.req.json()
        nb_id = body.get("notebook_id", 0)
        if not nb_id:
            w.json({"matches": [], "cursor_start": 0, "cursor_end": 0})
            return
        code = body.get("code", "")
        cursor_pos = body.get("cursor_pos", 0)
        cell_id = body.get("cell_id", 0)

        # Route %%mojo cells to the Mojo LSP server instead of the IPython kernel
        if code.lstrip().startswith("%%mojo"):
            from grokbook.mojo_lsp import get_mojo_lsp
            nb = await db.get_notebook(nb_id)
            kernel_python = (nb.kernel_env if nb else None) or pool.default_python_path
            lsp = await get_mojo_lsp(kernel_python)
            if lsp:
                # Strip the %%mojo magic line from the CURRENT cell
                first_nl = code.index("\n") if "\n" in code else len(code)
                this_mojo = code[first_nl + 1:]
                this_cursor = cursor_pos - first_nl - 1
                if this_cursor < 0:
                    # Cursor is on the %%mojo line itself
                    w.json({"matches": [], "cursor_start": cursor_pos, "cursor_end": cursor_pos})
                    return

                # Build shadow file: concatenate ALL %%mojo cells in the
                # notebook so the LSP sees cross-cell definitions (imports,
                # functions, structs declared in earlier cells).
                all_cells = await db.get_all_cells(nb_id)
                shadow_parts: list[str] = []
                cell_shadow_start = 0
                found = False
                for c in all_cells:
                    if c.cell_type != "code":
                        continue
                    # For the ACTIVE cell, use the live code from the POST
                    # body (may have unsaved edits), not the stale DB version
                    inp = code if c.id == cell_id else c.input
                    if not inp.lstrip().startswith("%%mojo"):
                        continue
                    nl = inp.index("\n") if "\n" in inp else len(inp)
                    mojo_src = inp[nl + 1:]
                    if c.id == cell_id:
                        cell_shadow_start = sum(len(p) + 1 for p in shadow_parts)
                        found = True
                    shadow_parts.append(mojo_src)

                if not found:
                    # Cell not in DB yet (unsaved new cell) — append it
                    cell_shadow_start = sum(len(p) + 1 for p in shadow_parts)
                    shadow_parts.append(this_mojo)

                shadow_code = "\n".join(shadow_parts)
                shadow_cursor = cell_shadow_start + this_cursor

                result = await lsp.complete(nb_id, shadow_code, shadow_cursor)

                # Translate cursor positions back to cell-local coords
                result["cursor_start"] = result["cursor_start"] - cell_shadow_start + first_nl + 1
                result["cursor_end"] = result["cursor_end"] - cell_shadow_start + first_nl + 1
                w.json(result)
                return
            w.json({"matches": [], "cursor_start": cursor_pos, "cursor_end": cursor_pos})
            return

        km = await _get_kernel(nb_id)
        result = await km.complete(code, cursor_pos)
        w.json(result)

    async def inspect_handler(c: Context, w: Writer) -> None:
        body = await c.req.json()
        nb_id = body.get("notebook_id", 0)
        if not nb_id:
            w.json({"text": ""})
            return
        km = await _get_kernel(nb_id)
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
    router.post("/nb/menu/*", nb_menu)
    router.post("/nb/menu-close", nb_menu_close)
    router.post("/nb/rename-mode/*", nb_rename_mode)
    router.post("/nb/rename/*", nb_rename)
    router.post("/nb/duplicate/*", nb_duplicate)
    router.post("/nb/delete/*", nb_delete)
    router.post("/nb/new-in/*", new_notebook_in_project)
    router.get("/nb/export/*", nb_export)
    router.post("/nb/import", nb_import)
    router.post("/project/new", new_project)
    router.post("/project/menu/*", project_menu)
    router.post("/project/menu-close", project_menu_close)
    router.post("/project/rename-mode/*", project_rename_mode)
    router.post("/project/rename/*", project_rename)
    router.post("/project/delete/*", project_delete)
    router.post("/cells/run-all", run_all)
    router.post("/cells/clear-output/*", clear_output)
    router.post("/cells/clear-all-outputs", clear_all_outputs)
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
    router.post("/kernel/env/set", kernel_env_set)
    router.post("/kernel/env/install", kernel_env_install)
    router.post("/kernel/envs/refresh", kernel_envs_refresh)
    router.post("/kernel/envs/add", kernel_envs_add_custom)
    router.post("/kernel/envs/remove", kernel_envs_remove_custom)
    router.post("/kernel/envs/complete", kernel_envs_complete)
    router.post("/complete", complete_handler)
    router.post("/inspect", inspect_handler)

    return router
