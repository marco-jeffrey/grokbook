(function () {
  // ── state ──────────────────────────────────────────────────────────────
  var _activeCell = null;   // cell id with active completions
  var _cursorStart = 0;
  var _cursorEnd = 0;
  var _selectedIdx = -1;
  var _saveTimers = {};

  // command mode state
  var _mode = 'command';       // 'command' or 'edit'
  var _selectedCellId = null;  // currently selected cell in command mode
  var _lastDTime = 0;          // for dd (double-d) delete

  function nbId() { return document.body.dataset.notebookId; }

  // ── cell selection helpers ─────────────────────────────────────────────
  function getAllCellIds() {
    return Array.from(document.querySelectorAll('[data-cell-container]'))
      .map(function (el) { return el.dataset.cellContainer; });
  }

  function selectCell(cellId) {
    // Remove previous selection
    document.querySelectorAll('[data-cell-container]').forEach(function (el) {
      el.classList.remove('ring-2', 'ring-indigo-500/50', 'rounded-lg');
    });
    _selectedCellId = cellId;
    if (cellId) {
      var el = document.querySelector('[data-cell-container="' + cellId + '"]');
      if (el) {
        el.classList.add('ring-2', 'ring-indigo-500/50', 'rounded-lg');
        el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
    }
  }

  function selectAdjacentCell(delta) {
    var ids = getAllCellIds();
    if (!ids.length) return;
    var idx = ids.indexOf(_selectedCellId);
    if (idx === -1) {
      selectCell(ids[0]);
    } else {
      var next = Math.max(0, Math.min(ids.length - 1, idx + delta));
      selectCell(ids[next]);
    }
  }

  function enterEditMode(cellId) {
    _mode = 'edit';
    var ta = document.querySelector('textarea[data-cell-id="' + cellId + '"]');
    if (ta) ta.focus();
  }

  function enterCommandMode() {
    _mode = 'command';
    if (document.activeElement && document.activeElement.tagName === 'TEXTAREA') {
      document.activeElement.blur();
    }
    // Select the cell that was being edited
    if (!_selectedCellId) {
      var ids = getAllCellIds();
      if (ids.length) selectCell(ids[0]);
    } else {
      selectCell(_selectedCellId);
    }
  }

  // fire-and-forget POST
  function firePost(path) {
    fetch(path, { method: 'POST' });
  }

  // ── auto-resize textareas ──────────────────────────────────────────────
  function autoResize(ta) {
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
  }
  function resizeAll() {
    document.querySelectorAll('textarea[data-cell-id]').forEach(autoResize);
  }
  resizeAll();
  new MutationObserver(resizeAll).observe(
    document.body, { childList: true, subtree: true }
  );

  // Track focus/blur for mode switching
  document.addEventListener('focusin', function (e) {
    if (e.target.tagName === 'TEXTAREA' && e.target.dataset.cellId) {
      _mode = 'edit';
      _selectedCellId = e.target.dataset.cellId;
    }
  });

  // ── input handler ──────────────────────────────────────────────────────
  document.addEventListener('input', function (e) {
    var ta = e.target;
    if (!ta.dataset.cellId) return;
    autoResize(ta);

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

  function completionsVisible() {
    var drop = _activeCell ? document.getElementById('completions-' + _activeCell) : null;
    return drop && !drop.classList.contains('hidden');
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

  // ── indent / dedent helpers ────────────────────────────────────────────
  function indentSelection(ta) {
    var start = ta.selectionStart;
    var end = ta.selectionEnd;
    var val = ta.value;
    // Find start of current line
    var lineStart = val.lastIndexOf('\n', start - 1) + 1;
    ta.value = val.slice(0, lineStart) + '    ' + val.slice(lineStart);
    ta.selectionStart = start + 4;
    ta.selectionEnd = end + 4;
    ta.dispatchEvent(new Event('input'));
  }

  function dedentSelection(ta) {
    var start = ta.selectionStart;
    var end = ta.selectionEnd;
    var val = ta.value;
    var lineStart = val.lastIndexOf('\n', start - 1) + 1;
    var lineContent = val.slice(lineStart);
    var removed = 0;
    if (lineContent.startsWith('    ')) removed = 4;
    else if (lineContent.startsWith('\t')) removed = 1;
    else {
      var m = lineContent.match(/^( {1,3})/);
      if (m) removed = m[1].length;
    }
    if (removed > 0) {
      ta.value = val.slice(0, lineStart) + val.slice(lineStart + removed);
      ta.selectionStart = Math.max(lineStart, start - removed);
      ta.selectionEnd = Math.max(lineStart, end - removed);
      ta.dispatchEvent(new Event('input'));
    }
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
    var ta = e.target;
    var isTextarea = ta.tagName === 'TEXTAREA' && ta.dataset.cellId;

    // ── Completions navigation (always takes priority when visible) ────
    if (completionsVisible()) {
      var drop = document.getElementById('completions-' + _activeCell);
      var items = drop.querySelectorAll('.completion-item');
      if (e.key === 'Tab') {
        e.preventDefault();
        var next = e.shiftKey
          ? (_selectedIdx <= 0 ? items.length - 1 : _selectedIdx - 1)
          : (_selectedIdx + 1) % items.length;
        setSelected(drop, next);
        return;
      }
      if (e.key === 'Enter' && _selectedIdx >= 0) {
        e.preventDefault();
        insertMatch(items[_selectedIdx].dataset.match);
        return;
      }
      if (e.key === 'Escape') {
        hideCompletions();
        return;
      }
    }

    // ── Edit mode keybindings ──────────────────────────────────────────
    if (_mode === 'edit' && isTextarea) {
      if (e.key === 'Escape') {
        e.preventDefault();
        hideCompletions();
        hideSignature(ta.dataset.cellId);
        _selectedCellId = ta.dataset.cellId;
        enterCommandMode();
        return;
      }
      // Shift+Enter: execute/save cell
      if (e.key === 'Enter' && e.shiftKey) {
        e.preventDefault();
        hideCompletions();
        hideSignature(ta.dataset.cellId);
        var btn = document.getElementById('run-btn-' + ta.dataset.cellId);
        if (btn) btn.click();
        return;
      }
      // Ctrl+Enter: run cell, stay in cell
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        hideCompletions();
        hideSignature(ta.dataset.cellId);
        var btn = document.getElementById('run-btn-' + ta.dataset.cellId);
        if (btn) btn.click();
        // Re-focus same cell after a tick
        var cid = ta.dataset.cellId;
        setTimeout(function () {
          var el = document.querySelector('textarea[data-cell-id="' + cid + '"]');
          if (el) el.focus();
        }, 100);
        return;
      }
      // Tab: indent (when no completions visible)
      if (e.key === 'Tab' && !e.shiftKey) {
        e.preventDefault();
        indentSelection(ta);
        return;
      }
      // Shift+Tab: dedent
      if (e.key === 'Tab' && e.shiftKey) {
        e.preventDefault();
        dedentSelection(ta);
        return;
      }
      return;
    }

    // ── Command mode keybindings ─────────────────────────────────────────
    if (_mode === 'command') {
      // Ignore if typing in non-cell inputs (rename, etc.)
      if (ta.tagName === 'INPUT' || ta.tagName === 'TEXTAREA') return;

      var key = e.key;

      // Navigation
      if (key === 'j' || key === 'ArrowDown') {
        e.preventDefault();
        selectAdjacentCell(1);
        return;
      }
      if (key === 'k' || key === 'ArrowUp') {
        e.preventDefault();
        selectAdjacentCell(-1);
        return;
      }

      // Enter edit mode
      if (key === 'Enter' && _selectedCellId) {
        e.preventDefault();
        enterEditMode(_selectedCellId);
        return;
      }

      // Insert cell above
      if (key === 'a' && _selectedCellId) {
        e.preventDefault();
        firePost('/cells/new-above/' + _selectedCellId);
        return;
      }

      // Insert cell below
      if (key === 'b' && _selectedCellId) {
        e.preventDefault();
        firePost('/cells/new-below/' + _selectedCellId);
        return;
      }

      // dd — delete cell (double-tap d within 500ms)
      if (key === 'd' && _selectedCellId) {
        e.preventDefault();
        var now = Date.now();
        if (now - _lastDTime < 500) {
          firePost('/cells/delete/' + _selectedCellId);
          // Select next cell
          var ids = getAllCellIds();
          var idx = ids.indexOf(_selectedCellId);
          var nextId = ids[idx + 1] || ids[idx - 1] || null;
          _selectedCellId = nextId;
          _lastDTime = 0;
        } else {
          _lastDTime = now;
        }
        return;
      }

      // m — convert to markdown
      if (key === 'm' && _selectedCellId) {
        e.preventDefault();
        firePost('/cells/convert/' + _selectedCellId);
        return;
      }

      // y — convert to code
      if (key === 'y' && _selectedCellId) {
        e.preventDefault();
        firePost('/cells/convert/' + _selectedCellId);
        return;
      }

      // Ctrl+Shift+Up/Down — move cell
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && key === 'ArrowUp' && _selectedCellId) {
        e.preventDefault();
        firePost('/cells/move-up/' + _selectedCellId);
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && key === 'ArrowDown' && _selectedCellId) {
        e.preventDefault();
        firePost('/cells/move-down/' + _selectedCellId);
        return;
      }
    }
  });

  // ── variables panel ─────────────────────────────────────────────────────
  async function fetchVariables() {
    var id = nbId();
    if (!id) return;
    try {
      var res = await fetch('/kernel/variables', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notebook_id: parseInt(id) })
      });
      var d = await res.json();
      var content = document.getElementById('vars-content');
      if (!content) return;
      var vars = d.variables || [];
      if (!vars.length) {
        content.innerHTML = '<span class="text-zinc-600 italic">No variables</span>';
        return;
      }
      var html = '<table class="w-full"><thead><tr class="text-zinc-500 border-b border-zinc-800">' +
        '<th class="text-left py-1 pr-2">Name</th><th class="text-left py-1 pr-2">Type</th>' +
        '<th class="text-left py-1">Shape/Size</th></tr></thead><tbody>';
      vars.forEach(function (v) {
        var extra = v.shape || v.size || '';
        if (v.dtype) extra += (extra ? ' ' : '') + v.dtype;
        html += '<tr class="border-b border-zinc-800/50"><td class="py-1 pr-2 text-indigo-300">' +
          v.name + '</td><td class="py-1 pr-2 text-zinc-500">' + v.type +
          '</td><td class="py-1 text-zinc-600">' + extra + '</td></tr>';
      });
      html += '</tbody></table>';
      content.innerHTML = html;
    } catch (_) {}
  }

  function isVarsPanelVisible() {
    var panel = document.getElementById('vars-panel');
    if (!panel) return false;
    // offsetParent is null for position:fixed elements, so use computed display
    return getComputedStyle(panel).display !== 'none';
  }

  // Immediate fetch when panel becomes visible
  var _varsWasVisible = false;
  new MutationObserver(function () {
    var visible = isVarsPanelVisible();
    if (visible && !_varsWasVisible) fetchVariables();
    _varsWasVisible = visible;
  }).observe(document.body, { attributes: true, subtree: true });

  // Continue polling every 5s while visible
  setInterval(function () {
    if (!isVarsPanelVisible()) return;
    fetchVariables();
  }, 5000);
})();
