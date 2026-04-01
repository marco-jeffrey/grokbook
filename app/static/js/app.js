(function () {
  // ── state ──────────────────────────────────────────────────────────────
  var _saveTimers = {};

  // command mode state
  var _mode = 'command';       // 'command' or 'edit'
  var _selectedCellId = null;  // currently selected cell in command mode
  var _lastDTime = 0;          // for dd (double-d) delete

  // CM6 editor registry: cellId → {view, getDoc, setDoc, focus, destroy}
  window._cmEditors = new Map();

  // Editor settings (read from localStorage, updated by Datastar effect)
  var _editorSettings = {
    autocomplete: localStorage.getItem('nb-autocomplete') !== 'false',
    linewrap: localStorage.getItem('nb-linewrap') === 'true',
    vim: localStorage.getItem('nb-vim') === 'true',
  };

  window._applyEditorSettings = function (ac, lw, vm) {
    _editorSettings.autocomplete = ac;
    _editorSettings.linewrap = lw;
    _editorSettings.vim = vm;
    window._cmEditors.forEach(function (editor, cellId) {
      CM.reconfigure(editor.view, 'autocomplete', ac, {
        completionSource: makeCompletionSource(cellId)
      });
      CM.reconfigure(editor.view, 'lineWrap', lw);
      CM.reconfigure(editor.view, 'vim', vm);
    });
  };

  function nbId() { return document.body.dataset.notebookId; }

  // ── cell selection helpers ─────────────────────────────────────────────
  function getAllCellIds() {
    return Array.from(document.querySelectorAll('[data-cell-container]'))
      .map(function (el) { return el.dataset.cellContainer; });
  }

  function selectCell(cellId) {
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
    var cm = window._cmEditors.get(cellId);
    if (cm) {
      cm.focus();
    } else {
      // Markdown cell — find textarea
      var ta = document.querySelector('textarea[data-cell-id="' + cellId + '"]');
      if (ta) ta.focus();
    }
  }

  function enterCommandMode() {
    _mode = 'command';
    if (document.activeElement) document.activeElement.blur();
    // Also blur CM6 editors
    window._cmEditors.forEach(function (ed) {
      if (ed.view.hasFocus) ed.view.contentDOM.blur();
    });
    if (!_selectedCellId) {
      var ids = getAllCellIds();
      if (ids.length) selectCell(ids[0]);
    } else {
      selectCell(_selectedCellId);
    }
  }

  function focusNextCell(currentCellId) {
    var ids = getAllCellIds();
    var idx = ids.indexOf(currentCellId);
    if (idx === -1 || idx >= ids.length - 1) return;
    var nextId = ids[idx + 1];
    _selectedCellId = nextId;
    // Small delay to let any DOM updates settle
    setTimeout(function () {
      var cm = window._cmEditors.get(nextId);
      if (cm) {
        cm.focus();
      } else {
        var ta = document.querySelector('textarea[data-cell-id="' + nextId + '"]');
        if (ta) ta.focus();
      }
    }, 50);
  }

  // fire-and-forget POST
  function firePost(path) {
    fetch(path, { method: 'POST' });
  }

  // ── auto-resize markdown textareas ─────────────────────────────────────
  function autoResize(ta) {
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
  }
  function resizeMarkdownTextareas() {
    // Only resize markdown textareas (not hidden sync textareas)
    document.querySelectorAll('textarea[data-cell-id]').forEach(autoResize);
  }

  // ── CM6 integration ────────────────────────────────────────────────────

  function syncToSignal(cellId, doc) {
    // Write to hidden textarea that has data-bind for Datastar signal sync
    var syncTa = document.querySelector('textarea[data-cell-sync="' + cellId + '"]');
    if (syncTa && syncTa.value !== doc) {
      syncTa.value = doc;
      syncTa.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  function autosave(cellId, doc) {
    clearTimeout(_saveTimers[cellId]);
    _saveTimers[cellId] = setTimeout(function () {
      var body = {};
      body['cell_' + cellId] = doc;
      fetch('/cells/save/' + cellId, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
    }, 1500);
  }

  function makeCompletionSource(cellId) {
    return async function (context) {
      var doc = context.state.doc.toString();
      var pos = context.pos;
      if (!doc.trim()) return null;
      try {
        var res = await fetch('/complete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code: doc,
            cursor_pos: pos,
            notebook_id: parseInt(nbId())
          })
        });
        var d = await res.json();
        if (!d.matches || !d.matches.length) return null;
        return {
          from: d.cursor_start,
          options: d.matches.map(function (m) {
            return { label: m, type: 'variable' };
          })
        };
      } catch (_) {
        return null;
      }
    };
  }

  function showSignature(cellId, view) {
    var doc = view.state.doc.toString();
    var pos = view.state.selection.main.head;
    if (!doc.trim()) return;
    fetch('/inspect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code: doc,
        cursor_pos: pos > 0 ? pos - 1 : 0,
        notebook_id: parseInt(nbId())
      })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.text) return;
      var box = document.getElementById('signature-' + cellId);
      if (!box) return;
      var coords = view.coordsAtPos(pos);
      if (!coords) return;
      var parent = view.dom.closest('.relative');
      if (!parent) return;
      var parentRect = parent.getBoundingClientRect();
      box.style.left = (coords.left - parentRect.left) + 'px';
      box.style.top = (coords.top - parentRect.top) + 'px';
      box.style.transform = 'translateY(-100%) translateY(-4px)';
      box.textContent = d.text;
      box.classList.remove('hidden');
    }).catch(function () {});
  }

  function hideSignature(cellId) {
    var box = document.getElementById('signature-' + cellId);
    if (box) box.classList.add('hidden');
  }

  function createCellEditor(container) {
    var cellId = container.dataset.cellId;
    if (window._cmEditors.has(cellId)) return; // Already initialized

    // Read initial content from <script data-cell-content>
    var contentScript = document.querySelector('script[data-cell-content="' + cellId + '"]');
    var initialDoc = '';
    if (contentScript) {
      try { initialDoc = JSON.parse(contentScript.textContent); } catch (_) {
        initialDoc = contentScript.textContent;
      }
    }

    var editor = window.CM.createEditor(container, {
      doc: initialDoc,
      autocompleteEnabled: _editorSettings.autocomplete,
      lineWrapEnabled: _editorSettings.linewrap,
      vimEnabled: _editorSettings.vim,
      onDocChanged: function (newDoc) {
        syncToSignal(cellId, newDoc);
        autosave(cellId, newDoc);
      },
      onFocus: function () {
        _mode = 'edit';
        _selectedCellId = cellId;
      },
      onBlur: function () {
        // Don't switch mode here — let Escape handle it
      },
      completionSource: makeCompletionSource(cellId),
      extraKeymap: [
        {
          key: 'Shift-Enter',
          run: function () {
            hideSignature(cellId);
            var btn = document.getElementById('run-btn-' + cellId);
            if (btn) btn.click();
            // Focus next cell immediately
            focusNextCell(cellId);
            return true;
          }
        },
        {
          key: 'Mod-Enter',
          run: function (view) {
            hideSignature(cellId);
            var btn = document.getElementById('run-btn-' + cellId);
            if (btn) btn.click();
            setTimeout(function () { view.focus(); }, 100);
            return true;
          }
        },
        {
          key: 'Escape',
          run: function () {
            hideSignature(cellId);
            _selectedCellId = cellId;
            enterCommandMode();
            return true;
          }
        }
      ]
    });

    // Detect ( for signature tooltips
    editor.view.dom.addEventListener('keyup', function (e) {
      if (e.key === '(' || (e.key === '9' && e.shiftKey)) {
        showSignature(cellId, editor.view);
      }
      if (e.key === ')' || e.key === 'Escape') {
        hideSignature(cellId);
      }
    });

    window._cmEditors.set(cellId, editor);
  }

  function initCodeEditors() {
    document.querySelectorAll('[data-cell-type="code"]').forEach(createCellEditor);
  }

  function cleanupOrphanedEditors() {
    window._cmEditors.forEach(function (editor, cellId) {
      var container = document.querySelector('[data-cell-type="code"][data-cell-id="' + cellId + '"]');
      if (!container || !container.contains(editor.view.dom)) {
        editor.destroy();
        window._cmEditors.delete(cellId);
      }
    });
  }

  // ── initialize ─────────────────────────────────────────────────────────
  initCodeEditors();
  resizeMarkdownTextareas();

  // Watch for DOM changes (SSE patches recreate cells)
  new MutationObserver(function () {
    cleanupOrphanedEditors();
    initCodeEditors();
    resizeMarkdownTextareas();
  }).observe(document.body, { childList: true, subtree: true });

  // Track focus for mode switching (markdown textareas)
  document.addEventListener('focusin', function (e) {
    if (e.target.tagName === 'TEXTAREA' && e.target.dataset.cellId) {
      _mode = 'edit';
      _selectedCellId = e.target.dataset.cellId;
    }
  });

  // ── markdown textarea input handler ────────────────────────────────────
  document.addEventListener('input', function (e) {
    var ta = e.target;
    // Skip hidden sync textareas and non-cell textareas
    if (ta.dataset.cellSync) return;
    if (!ta.dataset.cellId) return;
    autoResize(ta);

    // Autosave for markdown cells
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
  });

  // ── click handler ──────────────────────────────────────────────────────
  document.addEventListener('click', function (e) {
    if (!e.target.closest('[id^="signature-"]') && !e.target.closest('.cm-editor') && !e.target.closest('textarea')) {
      document.querySelectorAll('[id^="signature-"]').forEach(function (d) {
        d.classList.add('hidden');
      });
    }
  });

  // ── indent / dedent for markdown textareas ─────────────────────────────
  function indentSelection(ta) {
    var start = ta.selectionStart;
    var end = ta.selectionEnd;
    var val = ta.value;
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

  // ── keydown handler ────────────────────────────────────────────────────
  document.addEventListener('keydown', function (e) {
    var ta = e.target;
    var isTextarea = ta.tagName === 'TEXTAREA' && ta.dataset.cellId;

    // ── Edit mode for markdown textareas ──────────────────────────────
    if (_mode === 'edit' && isTextarea) {
      if (e.key === 'Escape') {
        e.preventDefault();
        hideSignature(ta.dataset.cellId);
        _selectedCellId = ta.dataset.cellId;
        enterCommandMode();
        return;
      }
      if (e.key === 'Enter' && e.shiftKey) {
        e.preventDefault();
        var btn = document.getElementById('run-btn-' + ta.dataset.cellId);
        if (btn) btn.click();
        focusNextCell(ta.dataset.cellId);
        return;
      }
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        var btn = document.getElementById('run-btn-' + ta.dataset.cellId);
        if (btn) btn.click();
        var cid = ta.dataset.cellId;
        setTimeout(function () {
          var el = document.querySelector('textarea[data-cell-id="' + cid + '"]');
          if (el) el.focus();
        }, 100);
        return;
      }
      if (e.key === 'Tab' && !e.shiftKey) {
        e.preventDefault();
        indentSelection(ta);
        return;
      }
      if (e.key === 'Tab' && e.shiftKey) {
        e.preventDefault();
        dedentSelection(ta);
        return;
      }
      return;
    }

    // ── Command mode keybindings ─────────────────────────────────────
    if (_mode === 'command') {
      // Ignore if typing in non-cell inputs (rename, etc.)
      if (ta.tagName === 'INPUT' || ta.tagName === 'TEXTAREA') return;
      // Ignore if focus is inside a CM editor
      if (ta.closest && ta.closest('.cm-editor')) return;

      var key = e.key;

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
      if (key === 'Enter' && _selectedCellId) {
        e.preventDefault();
        enterEditMode(_selectedCellId);
        return;
      }
      if (key === 'a' && _selectedCellId) {
        e.preventDefault();
        firePost('/cells/new-above/' + _selectedCellId);
        return;
      }
      if (key === 'b' && _selectedCellId) {
        e.preventDefault();
        firePost('/cells/new-below/' + _selectedCellId);
        return;
      }
      if (key === 'd' && _selectedCellId) {
        e.preventDefault();
        var now = Date.now();
        if (now - _lastDTime < 500) {
          firePost('/cells/delete/' + _selectedCellId);
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
      if (key === 'm' && _selectedCellId) {
        e.preventDefault();
        firePost('/cells/convert/' + _selectedCellId);
        return;
      }
      if (key === 'y' && _selectedCellId) {
        e.preventDefault();
        firePost('/cells/convert/' + _selectedCellId);
        return;
      }
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
    return getComputedStyle(panel).display !== 'none';
  }

  var _varsWasVisible = false;
  new MutationObserver(function () {
    var visible = isVarsPanelVisible();
    if (visible && !_varsWasVisible) fetchVariables();
    _varsWasVisible = visible;
  }).observe(document.body, { attributes: true, subtree: true });

  setInterval(function () {
    if (!isVarsPanelVisible()) return;
    fetchVariables();
  }, 5000);
})();
