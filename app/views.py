import html as _e

from stario.datastar import DatastarScript, at, data
from stario.html import (
    Body,
    Button,
    Div,
    Head,
    Html,
    Meta,
    Pre,
    SafeString,
    Script,
    Textarea,
    Title,
)

_COMPLETE_JS = SafeString("""
(function () {
  var _activeCell = null;
  var _cursorStart = 0;
  var _cursorEnd = 0;
  var _selectedIdx = -1;

  // ── input handler ────────────────────────────────────────────────────────
  document.addEventListener('input', function (e) {
    var ta = e.target;
    if (!ta.dataset.cellId) return;
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

  // ── completions ───────────────────────────────────────────────────────────
  async function fetchComplete(ta) {
    var code = ta.value;
    var cursor_pos = ta.selectionStart;
    if (!code.trim()) { hideCompletions(); return; }
    try {
      var res = await fetch('/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code, cursor_pos: cursor_pos })
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

  // ── signature / inspect ───────────────────────────────────────────────────
  async function fetchInspect(ta) {
    var code = ta.value;
    var cursor_pos = ta.selectionStart - 1;  // position before the '('
    if (!code.trim()) return;
    try {
      var res = await fetch('/inspect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code, cursor_pos: cursor_pos })
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
    // position above current line using CSS transform
    box.style.top  = (ta.offsetTop + caret.top) + 'px';
    box.style.transform = 'translateY(-100%) translateY(-4px)';
    box.textContent = text;
    box.classList.remove('hidden');
  }

  function hideSignature(cellId) {
    var box = document.getElementById('signature-' + cellId);
    if (box) box.classList.add('hidden');
  }

  // ── events ────────────────────────────────────────────────────────────────
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

from app.state import Cell


def cell_view(cell: Cell):
    is_error = "Error" in cell.output or "Traceback" in cell.output
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
            Div({
                "id": f"completions-{cell.id}",
                "class": "hidden absolute z-50 min-w-48 max-h-48 overflow-y-auto "
                         "bg-zinc-800 border border-zinc-600 rounded-lg shadow-xl",
            }),
            Div({
                "id": f"signature-{cell.id}",
                "class": "hidden absolute z-50 max-w-xl max-h-56 overflow-y-auto "
                         "bg-zinc-900 border border-indigo-800 rounded-lg shadow-xl "
                         "px-3 py-2 font-mono text-xs text-zinc-300 whitespace-pre-wrap",
            }),
        ),
        Div(
            {"class": "mt-2"},
            Button(
                data.on("click", at.post(f"/cells/execute/{cell.id}", include=[sig])),
                {
                    "class": "px-4 py-1.5 bg-indigo-600 text-white rounded-md text-sm font-medium hover:bg-indigo-500 transition-colors cursor-pointer"
                },
                "▶  Run",
            ),
        ),
        Pre({"class": out_class}, SafeString(_e.escape(cell.output)))
        if cell.output
        else SafeString(""),
    )


def notebook(cells: list[Cell]):
    """Full notebook div — SSE-patched on every change."""
    return Div(
        {"id": "notebook", "class": "w-full max-w-3xl"},
        *[cell_view(c) for c in cells],
        Button(
            data.on("click", at.post("/cells/new")),
            {
                "class": "mt-2 px-4 py-1.5 border border-zinc-700 text-zinc-400 rounded-md text-sm hover:border-indigo-500 hover:text-indigo-400 transition-colors cursor-pointer",
            },
            "+ New Cell",
        ),
    )


def page(cells: list[Cell]):
    """Full HTML shell — only used for the initial GET /."""
    return Html(
        Head(
            Meta({"charset": "utf-8"}),
            Meta(
                {"name": "viewport", "content": "width=device-width, initial-scale=1"}
            ),
            Title("nb-staroid"),
            Script({"src": "https://cdn.tailwindcss.com"}),
            Script({"src": "https://cdn.jsdelivr.net/npm/textarea-caret@3.1.0/index.js"}),
            DatastarScript(),
        ),
        Body(
            {
                "class": "min-h-screen bg-zinc-950 text-zinc-200 flex justify-center py-10 px-4"
            },
            notebook(cells),
            SafeString(
                '<p class="fixed bottom-4 right-4 text-xs text-zinc-700">persistent kernel</p>'
            ),
            Script(_COMPLETE_JS),
        ),
    )
