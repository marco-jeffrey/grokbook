import html as _e

from stario.datastar import DatastarScript, at, data
from stario.html import (
    A,
    Body,
    Button,
    Div,
    Head,
    Html,
    Meta,
    Pre,
    SafeString,
    Script,
    Span,
    Textarea,
    Title,
)

from app.state import Cell, Notebook

# ── JS ────────────────────────────────────────────────────────────────────────

_APP_JS = SafeString("""
(function () {
  var _activeCell = null;
  var _cursorStart = 0;
  var _cursorEnd = 0;
  var _selectedIdx = -1;
  var _saveTimers = {};

  function nbId() { return document.body.dataset.notebookId; }

  // ── input handler ──────────────────────────────────────────────────────
  document.addEventListener('input', function (e) {
    var ta = e.target;
    if (!ta.dataset.cellId) return;

    // autosave debounce
    clearTimeout(_saveTimers[ta.dataset.cellId]);
    _saveTimers[ta.dataset.cellId] = setTimeout(function () {
      var body = {};
      body['cell_' + ta.dataset.cellId] = ta.value;
      fetch('/cells/save/' + ta.dataset.cellId, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
    }, 1500);

    var ch = ta.value[ta.selectionStart - 1];
    if (ch === '(') {
      hideCompletions();
      fetchInspect(ta);
    } else if (ch === ')') {
      hideSignature(ta.dataset.cellId);
      fetchComplete(ta);
    } else {
      fetchComplete(ta);
    }
  });

  // ── completions ────────────────────────────────────────────────────────
  async function fetchComplete(ta) {
    var code = ta.value;
    var cursor_pos = ta.selectionStart;
    if (!code.trim()) { hideCompletions(); return; }
    try {
      var res = await fetch('/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code, cursor_pos: cursor_pos, notebook_id: parseInt(nbId()) })
      });
      var d = await res.json();
      _activeCell = ta.dataset.cellId;
      _cursorStart = d.cursor_start;
      _cursorEnd = d.cursor_end;
      _selectedIdx = -1;
      showCompletions(ta, d.matches);
    } catch (_) {}
  }

  function showCompletions(ta, matches) {
    var drop = document.getElementById('completions-' + ta.dataset.cellId);
    if (!drop) return;
    if (!matches || !matches.length) { drop.classList.add('hidden'); return; }
    var caret = getCaretCoordinates(ta, ta.selectionStart);
    drop.style.left = (ta.offsetLeft + caret.left) + 'px';
    drop.style.top  = (ta.offsetTop  + caret.top + caret.height) + 'px';
    drop.innerHTML = matches.slice(0, 30).map(function (m, i) {
      return '<div class="completion-item px-3 py-1 cursor-pointer font-mono text-sm text-zinc-200" data-match="' + m + '" data-idx="' + i + '">' + m + '</div>';
    }).join('');
    drop.classList.remove('hidden');
  }

  function setSelected(drop, idx) {
    _selectedIdx = idx;
    drop.querySelectorAll('.completion-item').forEach(function (el, i) {
      el.classList.toggle('bg-indigo-600', i === idx);
    });
    var active = drop.querySelector('[data-idx="' + idx + '"]');
    if (active) active.scrollIntoView({ block: 'nearest' });
  }

  function hideCompletions() {
    _selectedIdx = -1;
    document.querySelectorAll('[id^="completions-"]').forEach(function (d) {
      d.classList.add('hidden');
    });
  }

  function insertMatch(match) {
    var ta = document.querySelector('textarea[data-cell-id="' + _activeCell + '"]');
    if (ta) {
      ta.value = ta.value.slice(0, _cursorStart) + match + ta.value.slice(_cursorEnd);
      ta.selectionStart = ta.selectionEnd = _cursorStart + match.length;
      ta.dispatchEvent(new Event('input'));
    }
    hideCompletions();
  }

  // ── signature / inspect ────────────────────────────────────────────────
  async function fetchInspect(ta) {
    var code = ta.value;
    var cursor_pos = ta.selectionStart - 1;
    if (!code.trim()) return;
    try {
      var res = await fetch('/inspect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code, cursor_pos: cursor_pos, notebook_id: parseInt(nbId()) })
      });
      var d = await res.json();
      if (d.text) showSignature(ta, d.text);
    } catch (_) {}
  }

  function showSignature(ta, text) {
    var box = document.getElementById('signature-' + ta.dataset.cellId);
    if (!box) return;
    var caret = getCaretCoordinates(ta, ta.selectionStart);
    box.style.left = (ta.offsetLeft + caret.left) + 'px';
    box.style.top  = (ta.offsetTop + caret.top) + 'px';
    box.style.transform = 'translateY(-100%) translateY(-4px)';
    box.textContent = text;
    box.classList.remove('hidden');
  }

  function hideSignature(cellId) {
    var box = document.getElementById('signature-' + cellId);
    if (box) box.classList.add('hidden');
  }

  // ── events ─────────────────────────────────────────────────────────────
  document.addEventListener('click', function (e) {
    var item = e.target.closest('.completion-item');
    if (item) { insertMatch(item.dataset.match); return; }
    if (!e.target.closest('[id^="completions-"]')) hideCompletions();
    if (!e.target.closest('[id^="signature-"]') && !e.target.closest('textarea')) {
      document.querySelectorAll('[id^="signature-"]').forEach(function (d) {
        d.classList.add('hidden');
      });
    }
  });

  document.addEventListener('keydown', function (e) {
    // Shift+Enter: execute cell (always takes priority)
    if (e.key === 'Enter' && e.shiftKey) {
      var ta = e.target;
      if (!ta.dataset.cellId) return;
      e.preventDefault();
      hideCompletions();
      hideSignature(ta.dataset.cellId);
      var btn = document.getElementById('run-btn-' + ta.dataset.cellId);
      if (btn) btn.click();
      return;
    }

    if (e.key === 'Escape') {
      hideCompletions();
      document.querySelectorAll('[id^="signature-"]').forEach(function (d) {
        d.classList.add('hidden');
      });
      return;
    }

    var drop = _activeCell ? document.getElementById('completions-' + _activeCell) : null;
    if (!drop || drop.classList.contains('hidden')) return;
    var items = drop.querySelectorAll('.completion-item');
    if (!items.length) return;

    if (e.key === 'Tab') {
      e.preventDefault();
      var next = e.shiftKey
        ? (_selectedIdx <= 0 ? items.length - 1 : _selectedIdx - 1)
        : (_selectedIdx + 1) % items.length;
      setSelected(drop, next);
    } else if (e.key === 'Enter' && _selectedIdx >= 0) {
      e.preventDefault();
      insertMatch(items[_selectedIdx].dataset.match);
    }
  });
})();
""")

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


def sidebar_view(active_id: int, notebooks: list[Notebook]):
    return Div(
        {
            "class": "fixed left-0 top-12 w-56 h-[calc(100vh-3rem)] bg-zinc-900 "
            "border-r border-zinc-800 p-4 overflow-y-auto z-30"
        },
        Div(
            {"class": "text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-3"},
            "Notebooks",
        ),
        *[
            A(
                {
                    "href": f"/nb/{nb.id}",
                    "class": (
                        "block px-3 py-2 rounded-md text-sm mb-1 truncate transition-colors "
                        + (
                            "bg-indigo-600/20 text-indigo-300 border border-indigo-600/30"
                            if nb.id == active_id
                            else "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
                        )
                    ),
                },
                nb.name,
            )
            for nb in notebooks
        ],
        A(
            {
                "href": "/nb/new",
                "class": "block px-3 py-2 rounded-md text-sm mt-3 text-zinc-500 "
                "hover:text-indigo-400 hover:bg-zinc-800 transition-colors "
                "border border-dashed border-zinc-700",
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


def cell_view(cell: Cell):
    is_error = cell.status == "error"
    out_class = (
        "mt-2 rounded-lg border border-red-900 bg-red-950 p-4 font-mono "
        "text-sm text-red-300 whitespace-pre-wrap break-words"
        if is_error
        else "mt-2 rounded-lg border border-zinc-700 bg-zinc-900 p-4 font-mono "
        "text-sm text-emerald-300 whitespace-pre-wrap break-words"
    )
    sig = f"cell_{cell.id}"
    return Div(
        {"class": "mb-6"},
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
                        "w-full min-h-32 p-3 bg-zinc-900 border border-zinc-700 rounded-lg "
                        "text-zinc-200 font-mono text-sm leading-relaxed resize-y outline-none "
                        "focus:border-indigo-500 transition-colors"
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
        Pre({"class": out_class}, SafeString(_e.escape(cell.output)))
        if cell.output
        else SafeString(""),
    )


def notebook(cells: list[Cell], nb_id: int):
    """Full notebook div — SSE-patched on every change."""
    return Div(
        {"id": "notebook", "class": "w-full max-w-3xl"},
        *[cell_view(c) for c in cells],
        Button(
            data.on("click", at.post("/cells/new", include=["notebook_id"])),
            {
                "class": "mt-2 px-4 py-1.5 border border-zinc-700 text-zinc-400 rounded-md "
                "text-sm hover:border-indigo-500 hover:text-indigo-400 transition-colors cursor-pointer",
            },
            "+ New Cell",
        ),
    )


def page(nb: Notebook, notebooks: list[Notebook], cells: list[Cell]):
    """Full HTML shell — only used for the initial GET /nb/{id}."""
    return Html(
        Head(
            Meta({"charset": "utf-8"}),
            Meta({"name": "viewport", "content": "width=device-width, initial-scale=1"}),
            Title(f"{nb.name} — nb-staroid"),
            Script({"src": "https://cdn.tailwindcss.com"}),
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
            Script(_APP_JS),
        ),
    )
