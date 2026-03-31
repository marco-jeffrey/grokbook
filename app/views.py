import html as _e
import json

from markdown_it import MarkdownIt
from stario.datastar import DatastarScript, at, data
from stario.html import (
    Body,
    Button,
    Div,
    Head,
    Html,
    Img,
    Input,
    Meta,
    Pre,
    SafeString,
    Script,
    Span,
    Textarea,
    Title,
)

from stario import UrlFor

from app.state import Cell, Notebook

_md = MarkdownIt("gfm-like")

_SPINNER_SVG = SafeString(
    '<svg class="animate-spin h-4 w-4" viewBox="0 0 24 24">'
    '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"></circle>'
    '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>'
    "</svg>"
)


# ── components ────────────────────────────────────────────────────────────────


def header_bar():
    return Div(
        {
            "class": "fixed top-0 left-0 right-0 h-12 bg-zinc-900 border-b border-zinc-800 "
            "flex items-center justify-between px-4 z-40"
        },
        Div(
            {"class": "flex items-center gap-3"},
            Span({"class": "text-sm font-semibold text-zinc-300"}, "nb-staroid"),
            Div(
                {"class": "flex items-center gap-1.5 ml-4"},
                Div(
                    data.show("$kernel_state === 'idle'"),
                    {"class": "w-2 h-2 rounded-full bg-green-400"},
                ),
                Div(
                    data.show("$kernel_state === 'busy'"),
                    {"class": "w-2 h-2 rounded-full bg-yellow-400 animate-pulse"},
                ),
                Div(
                    data.show("$kernel_state === 'dead'"),
                    {"class": "w-2 h-2 rounded-full bg-red-500"},
                ),
                Span(
                    {"class": "text-xs text-zinc-500"},
                    data.text("$kernel_state"),
                ),
            ),
        ),
        Button(
            data.on(
                "click",
                at.post("/kernel/restart", include=["notebook_id"]),
            ),
            {
                "class": "text-xs text-zinc-500 hover:text-zinc-200 transition-colors "
                "cursor-pointer px-3 py-1 rounded border border-zinc-700 hover:border-zinc-500"
            },
            "↺ Restart Kernel",
        ),
    )


def _nb_item(nb: Notebook, active_id: int):
    """Normal sidebar notebook item with ⋯ menu button."""
    is_active = nb.id == active_id
    return Div(
        {"class": "relative group mb-1"},
        Button(
            data.on("click", at.post(f"/nb/switch/{nb.id}")),
            {
                "class": (
                    "block w-full text-left px-3 py-2 pr-8 rounded-md text-sm truncate "
                    "transition-colors cursor-pointer "
                    + (
                        "bg-indigo-600/20 text-indigo-300 border border-indigo-600/30"
                        if is_active
                        else "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
                    )
                ),
            },
            nb.name,
        ),
        Button(
            data.on("click", at.post(f"/nb/menu/{nb.id}")),
            {
                "class": "absolute right-1 top-1/2 -translate-y-1/2 px-1.5 py-0.5 rounded "
                "text-zinc-600 hover:text-zinc-300 hover:bg-zinc-700 opacity-0 "
                "group-hover:opacity-100 transition-opacity cursor-pointer text-xs",
            },
            "⋯",
        ),
    )


def _nb_item_menu(nb: Notebook, active_id: int):
    """Notebook item with dropdown menu open."""
    is_active = nb.id == active_id
    btn = (
        "block w-full text-left px-3 py-1.5 text-sm text-zinc-300 "
        "hover:bg-zinc-700 rounded cursor-pointer"
    )
    return Div(
        {"class": "relative mb-1"},
        Button(
            {
                "class": (
                    "block w-full text-left px-3 py-2 pr-8 rounded-md text-sm truncate "
                    + (
                        "bg-indigo-600/20 text-indigo-300 border border-indigo-600/30"
                        if is_active
                        else "bg-zinc-800 text-zinc-200"
                    )
                ),
            },
            nb.name,
        ),
        # Dropdown
        Div(
            {
                "class": "absolute left-0 top-full mt-1 w-full bg-zinc-800 border border-zinc-600 "
                "rounded-lg shadow-xl z-50 py-1"
            },
            Button(data.on("click", at.post(f"/nb/rename-mode/{nb.id}")), {"class": btn}, "Rename"),
            Button(data.on("click", at.post(f"/nb/duplicate/{nb.id}")), {"class": btn}, "Duplicate"),
            Button(
                data.on("click", at.post(f"/nb/delete/{nb.id}")),
                {
                    "class": "block w-full text-left px-3 py-1.5 text-sm text-red-400 "
                    "hover:bg-zinc-700 rounded cursor-pointer"
                },
                "Delete",
            ),
        ),
        # Click-away overlay
        Div(
            data.on("click", at.post(f"/nb/menu-close")),
            {"class": "fixed inset-0 z-40", "style": "cursor: default"},
        ),
    )


def _nb_item_rename(nb: Notebook):
    """Notebook item in rename mode — input field."""
    sig = f"rename_{nb.id}"
    return Div(
        {"class": "mb-1"},
        data.signals({sig: nb.name}),
        Input(
            data.bind(sig),
            data.on(
                "keydown",
                f"if(evt.key==='Enter'){{{at.post(f'/nb/rename/{nb.id}', include=[sig])}}};"
                f"if(evt.key==='Escape'){{{at.post('/nb/menu-close')}}}",
            ),
            {
                "type": "text",
                "autofocus": True,
                "class": "w-full px-3 py-2 rounded-md text-sm bg-zinc-800 border border-indigo-500 "
                "text-zinc-200 outline-none",
            },
        ),
    )


def sidebar_view(
    active_id: int,
    notebooks: list[Notebook],
    menu_id: int | None = None,
    renaming_id: int | None = None,
):
    items = []
    for nb in notebooks:
        if renaming_id == nb.id:
            items.append(_nb_item_rename(nb))
        elif menu_id == nb.id:
            items.append(_nb_item_menu(nb, active_id))
        else:
            items.append(_nb_item(nb, active_id))

    return Div(
        {
            "id": "sidebar",
            "class": "fixed left-0 top-12 w-56 h-[calc(100vh-3rem)] bg-zinc-900 "
            "border-r border-zinc-800 p-4 overflow-y-auto z-30",
        },
        Div(
            {"class": "text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-3"},
            "Notebooks",
        ),
        *items,
        Button(
            data.on("click", at.post("/nb/new")),
            {
                "class": "block w-full text-left px-3 py-2 rounded-md text-sm mt-3 text-zinc-500 "
                "hover:text-indigo-400 hover:bg-zinc-800 transition-colors "
                "border border-dashed border-zinc-700 cursor-pointer",
            },
            "+ New Notebook",
        ),
    )


def execution_indicator():
    return Div(
        Div(
            data.show("$executing"),
            {"class": "fixed bottom-4 left-60 flex items-center gap-2 text-sm text-zinc-400"},
            _SPINNER_SVG,
            "executing…",
        ),
        Div(
            data.show("!$executing && $last_status === 'ok'"),
            {
                "class": "fixed bottom-4 left-60 flex items-center gap-2 text-sm text-green-400"
            },
            "✓ done",
        ),
        Div(
            data.show("!$executing && $last_status === 'error'"),
            {
                "class": "fixed bottom-4 left-60 flex items-center gap-2 text-sm text-red-400"
            },
            "✗ error",
        ),
    )


def _render_output_block(block: dict):
    """Render a single {mime, data} output block."""
    mime = block.get("mime", "text/plain")
    content = block.get("data", "")
    if mime in ("image/png", "image/jpeg"):
        return Img({"src": f"data:{mime};base64,{content}", "class": "max-w-full rounded my-2"})
    if mime == "image/svg+xml":
        return Div(
            {"class": "bg-white rounded p-2 my-2 inline-block"},
            SafeString(content),
        )
    if mime == "text/html":
        return Div({"class": "prose prose-invert prose-sm max-w-none my-2"}, SafeString(content))
    # text/plain fallback
    return Pre(
        {"class": "font-mono text-sm whitespace-pre-wrap break-words"},
        SafeString(_e.escape(content)),
    )


def _render_output(output: str, is_error: bool, cell_id: int | None = None):
    """Render cell output — handles both plain text and rich JSON blocks."""
    id_attr = {"id": f"output-{cell_id}"} if cell_id else {}

    if not output:
        return Div(id_attr) if cell_id else SafeString("")

    base_class = (
        "mt-2 rounded-lg border p-4 "
        + ("border-red-900 bg-red-950 text-red-300" if is_error
           else "border-zinc-700 bg-zinc-900 text-emerald-300")
    )

    # Try parsing as rich output (JSON list of {mime, data} blocks)
    if not is_error:
        try:
            blocks = json.loads(output)
            if isinstance(blocks, list) and blocks and "mime" in blocks[0]:
                return Div(
                    id_attr,
                    {"class": base_class},
                    *[_render_output_block(b) for b in blocks],
                )
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    # Plain text fallback
    return Pre(id_attr, {"class": base_class + " font-mono text-sm whitespace-pre-wrap break-words"}, SafeString(_e.escape(output)))


def _cell_toolbar(cell: Cell):
    """Row of small action buttons that appear on hover."""
    btn = (
        "px-1.5 py-0.5 rounded text-zinc-600 hover:text-zinc-200 "
        "hover:bg-zinc-700 transition-colors cursor-pointer text-xs"
    )
    return Div(
        {"class": "opacity-0 group-hover:opacity-100 transition-opacity flex gap-1 mb-1 justify-end"},
        Button(data.on("click", at.post(f"/cells/move-up/{cell.id}")), {"class": btn, "title": "Move up"}, "▲"),
        Button(data.on("click", at.post(f"/cells/move-down/{cell.id}")), {"class": btn, "title": "Move down"}, "▼"),
        Button(data.on("click", at.post(f"/cells/duplicate/{cell.id}")), {"class": btn, "title": "Duplicate"}, "⊕"),
        Button(data.on("click", at.post(f"/cells/delete/{cell.id}")), {"class": btn + " hover:text-red-400", "title": "Delete"}, "✕"),
    )


def _code_cell_view(cell: Cell):
    is_error = cell.status == "error"
    sig = f"cell_{cell.id}"
    return Div(
        {"class": "mb-6 group", "data-cell-container": str(cell.id)},
        _cell_toolbar(cell),
        data.signals({sig: cell.input}),
        Div(
            {"class": "relative"},
            Textarea(
                cell.input,
                data.bind(sig),
                {
                    "data-cell-id": str(cell.id),
                    "spellcheck": "false",
                    "class": (
                        "w-full min-h-[4.5rem] p-3 bg-zinc-900 border border-zinc-700 rounded-lg "
                        "text-zinc-200 font-mono text-sm leading-relaxed resize-none outline-none "
                        "focus:border-indigo-500 transition-colors overflow-hidden"
                    ),
                },
            ),
            Div(
                {
                    "id": f"completions-{cell.id}",
                    "class": "hidden absolute z-50 min-w-48 max-h-48 overflow-y-auto "
                    "bg-zinc-800 border border-zinc-600 rounded-lg shadow-xl",
                }
            ),
            Div(
                {
                    "id": f"signature-{cell.id}",
                    "class": "hidden absolute z-50 max-w-xl max-h-56 overflow-y-auto "
                    "bg-zinc-900 border border-indigo-800 rounded-lg shadow-xl "
                    "px-3 py-2 font-mono text-xs text-zinc-300 whitespace-pre-wrap",
                }
            ),
        ),
        # Hidden button — Shift+Enter clicks this to trigger Datastar indicator
        Button(
            data.on("click", at.post(f"/cells/execute/{cell.id}", include=[sig])),
            data.indicator("executing"),
            {"id": f"run-btn-{cell.id}", "class": "hidden"},
        ),
        # Status hint
        Div(
            {"class": "mt-1 flex items-center gap-2 text-xs"},
            Span({"class": "text-zinc-600"}, "shift+enter to run"),
            Span({"class": "text-green-400"}, "✓") if cell.status == "ok" else SafeString(""),
            Span({"class": "text-red-400"}, "✗") if cell.status == "error" else SafeString(""),
        ),
        _render_output(cell.output, is_error, cell.id),
    )


def _markdown_cell_view(cell: Cell):
    sig = f"cell_{cell.id}"
    rendered = _md.render(cell.input) if cell.input else "<p class='text-zinc-600 italic'>empty markdown cell</p>"
    return Div(
        {"class": "mb-6 group", "data-cell-container": str(cell.id)},
        _cell_toolbar(cell),
        data.signals({sig: cell.input}),
        # Rendered markdown (click to edit)
        Div(
            {
                "id": f"md-display-{cell.id}",
                "class": "prose prose-invert prose-sm max-w-none p-4 rounded-lg "
                "border border-zinc-800 hover:border-zinc-600 transition-colors cursor-text",
            },
            data.on(
                "dblclick",
                f"document.getElementById('md-edit-{cell.id}').classList.remove('hidden');"
                f"document.getElementById('md-display-{cell.id}').classList.add('hidden');"
                f"document.querySelector('#md-edit-{cell.id} textarea').focus()",
            ),
            SafeString(rendered),
        ),
        # Edit mode (hidden by default)
        Div(
            {
                "id": f"md-edit-{cell.id}",
                "class": "hidden",
            },
            Textarea(
                cell.input,
                data.bind(sig),
                {
                    "data-cell-id": str(cell.id),
                    "spellcheck": "false",
                    "class": (
                        "w-full min-h-[4.5rem] p-3 bg-zinc-900 border border-zinc-700 rounded-lg "
                        "text-zinc-200 font-mono text-sm leading-relaxed resize-none outline-none "
                        "focus:border-indigo-500 transition-colors overflow-hidden"
                    ),
                },
            ),
            Div(
                {"class": "mt-1 text-xs text-zinc-600"},
                "shift+enter to save · double-click rendered text to edit",
            ),
        ),
        # Hidden button — Shift+Enter saves markdown and re-renders
        Button(
            data.on("click", at.post(f"/cells/save-md/{cell.id}", include=[sig])),
            {"id": f"run-btn-{cell.id}", "class": "hidden"},
        ),
    )


def cell_view(cell: Cell):
    if cell.cell_type == "markdown":
        return _markdown_cell_view(cell)
    return _code_cell_view(cell)


def notebook(cells: list[Cell], nb_id: int):
    """Full notebook div — SSE-patched on every change."""
    btn_class = (
        "px-4 py-1.5 border border-zinc-700 text-zinc-400 rounded-md "
        "text-sm hover:border-indigo-500 hover:text-indigo-400 transition-colors cursor-pointer"
    )
    return Div(
        {"id": "notebook", "class": "w-full max-w-3xl"},
        *[cell_view(c) for c in cells],
        Div(
            {"class": "mt-2 flex gap-2"},
            Button(
                data.on("click", at.post("/cells/new", include=["notebook_id"])),
                {"class": btn_class},
                "+ Code",
            ),
            Button(
                data.on("click", at.post("/cells/new-md", include=["notebook_id"])),
                {"class": btn_class},
                "+ Markdown",
            ),
        ),
    )


def page(
    nb: Notebook,
    notebooks: list[Notebook],
    cells: list[Cell],
    url_for: UrlFor,
):
    """Full HTML shell — only used for the initial GET /nb/{id}."""
    return Html(
        Head(
            Meta({"charset": "utf-8"}),
            Meta({"name": "viewport", "content": "width=device-width, initial-scale=1"}),
            Title(f"{nb.name} — nb-staroid"),
            Script({"src": "https://cdn.tailwindcss.com?plugins=typography"}),
            Script({"src": "https://cdn.jsdelivr.net/npm/textarea-caret@3.1.0/index.js"}),
            DatastarScript(),
        ),
        Body(
            {
                "class": "min-h-screen bg-zinc-950 text-zinc-200",
                "data-notebook-id": str(nb.id),
            },
            data.signals(
                {
                    "notebook_id": nb.id,
                    "executing": False,
                    "last_status": "",
                    "focus_cell": "",
                    "kernel_state": "idle",
                }
            ),
            data.init(at.get("/events")),
            data.effect("document.body.dataset.notebookId=String($notebook_id)"),
            data.effect(
                "if($focus_cell){"
                "var el=document.querySelector('textarea[data-cell-id=\"'+$focus_cell+'\"]');"
                "if(el){el.focus();}$focus_cell='';}"
            ),
            header_bar(),
            Div(
                {"class": "flex pt-12"},
                sidebar_view(nb.id, notebooks),
                Div(
                    {"class": "flex-1 flex justify-center py-10 px-4 ml-56"},
                    notebook(cells, nb.id),
                ),
            ),
            execution_indicator(),
            Script({"src": url_for("static", "js/app.js")}),
        ),
    )
