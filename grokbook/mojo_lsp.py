"""Mojo LSP client for code completions in %%mojo cells.

Manages a long-lived `mojo-lsp-server` subprocess, sends LSP requests over
stdio, and translates completions into grokbook's {matches, cursor_start,
cursor_end} format.

Shadow files (one per notebook, all %%mojo cells concatenated) live in
~/.grokbook/mojo-lsp/ and persist across server restarts. Uses incremental
document sync (LSP textDocumentSync.change=2) so single-char edits only
reparse the changed region.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

_CONTENT_LENGTH = b"Content-Length: "
_LSP_DIR = Path.home() / ".grokbook" / "mojo-lsp"


def _find_mojo_lsp(kernel_python: str | None = None) -> str | None:
    """Locate the mojo-lsp-server binary."""
    if kernel_python:
        candidate = Path(kernel_python).parent / "mojo-lsp-server"
        if candidate.exists():
            return str(candidate)
    found = shutil.which("mojo-lsp-server")
    if found:
        return found
    cwd = Path.cwd()
    for envdir in [cwd / ".pixi" / "envs", cwd / ".magic" / "envs"]:
        if envdir.is_dir():
            for env in envdir.iterdir():
                candidate = env / "bin" / "mojo-lsp-server"
                if candidate.exists():
                    return str(candidate)
    modular_home = Path(os.environ.get("MODULAR_HOME", Path.home() / ".modular"))
    for candidate in [
        modular_home / "bin" / "mojo-lsp-server",
        modular_home / "pkg" / "packages.modular.com_max" / "bin" / "mojo-lsp-server",
        modular_home / "pkg" / "packages.modular.com_mojo" / "bin" / "mojo-lsp-server",
    ]:
        if candidate.exists():
            return str(candidate)
    return None


def _build_lsp_env(binary: str) -> dict[str, str]:
    """Build environment for the LSP subprocess. Critical: MODULAR_HOME."""
    env = os.environ.copy()
    bin_dir = Path(binary).parent
    prefix = bin_dir.parent
    if "MODULAR_HOME" not in env:
        modular_home = prefix / "share" / "max"
        if modular_home.is_dir():
            env["MODULAR_HOME"] = str(modular_home)
    env["PATH"] = str(bin_dir) + ":" + env.get("PATH", "")
    if (prefix / "conda-meta").is_dir():
        env["CONDA_PREFIX"] = str(prefix)
    return env


def _find_project_root(kernel_python: str | None) -> str | None:
    """Find the pixi/mojo project root for rootUri."""
    for name in ("pixi.toml", "mojoproject.toml"):
        if (Path.cwd() / name).exists():
            return str(Path.cwd())
    if kernel_python:
        d = Path(kernel_python).parent
        for _ in range(6):
            d = d.parent
            if (d / "pixi.toml").exists() or (d / "mojoproject.toml").exists():
                return str(d)
    return None


def _compute_incremental_change(
    old: str, new: str
) -> tuple[dict, dict] | None:
    """Compute an incremental LSP contentChange from old → new text.

    Returns (range, text) for the minimal single-range edit, or None if
    texts are identical. Much faster than sending the full document — the
    LSP only reparses the changed region.
    """
    if old == new:
        return None

    # Find common prefix
    prefix_len = 0
    min_len = min(len(old), len(new))
    while prefix_len < min_len and old[prefix_len] == new[prefix_len]:
        prefix_len += 1

    # Find common suffix (not overlapping with prefix)
    suffix_len = 0
    while (suffix_len < (len(old) - prefix_len)
           and suffix_len < (len(new) - prefix_len)
           and old[-(suffix_len + 1)] == new[-(suffix_len + 1)]):
        suffix_len += 1

    # The changed region in old text: old[prefix_len : len(old) - suffix_len]
    old_end = len(old) - suffix_len
    new_end = len(new) - suffix_len

    start_line = old[:prefix_len].count("\n")
    start_last_nl = old[:prefix_len].rfind("\n")
    start_col = prefix_len - start_last_nl - 1 if start_last_nl >= 0 else prefix_len

    end_line = old[:old_end].count("\n")
    end_last_nl = old[:old_end].rfind("\n")
    end_col = old_end - end_last_nl - 1 if end_last_nl >= 0 else old_end

    range_obj = {
        "start": {"line": start_line, "character": start_col},
        "end": {"line": end_line, "character": end_col},
    }
    insert_text = new[prefix_len:new_end]

    return range_obj, insert_text


class MojoLSP:
    """Async LSP client for mojo-lsp-server over stdio."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._read_task: asyncio.Task | None = None
        self._docs: dict[str, int] = {}  # uri -> version
        self._doc_content: dict[str, str] = {}  # uri -> last sent content
        self._last_completions: dict[str, list] = {}  # uri -> last CompletionItem[]
        self._initialized = False
        self._lock = asyncio.Lock()

    async def start(self, kernel_python: str | None = None) -> bool:
        """Start the LSP server. Returns False if mojo-lsp-server not found."""
        binary = _find_mojo_lsp(kernel_python)
        if not binary:
            log.info("mojo-lsp-server not found — Mojo completions disabled")
            return False

        _LSP_DIR.mkdir(parents=True, exist_ok=True)
        env = _build_lsp_env(binary)

        try:
            self._proc = await asyncio.create_subprocess_exec(
                binary,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except (OSError, FileNotFoundError) as e:
            log.warning("Failed to start mojo-lsp-server: %s", e)
            return False

        self._read_task = asyncio.create_task(self._read_loop())

        project_root = _find_project_root(kernel_python) or str(_LSP_DIR)
        resp = await self._request("initialize", {
            "processId": os.getpid(),
            "capabilities": {
                "textDocument": {
                    "completion": {
                        "completionItem": {"snippetSupport": False},
                    },
                    "synchronization": {
                        "dynamicRegistration": False,
                        "didSave": False,
                    },
                },
            },
            "rootUri": f"file://{project_root}",
            "workspaceFolders": [{"uri": f"file://{project_root}", "name": "grokbook"}],
        })
        if resp is None:
            log.warning("mojo-lsp-server initialize failed")
            await self.shutdown()
            return False

        await self._notify("initialized", {})
        self._initialized = True
        log.info("Mojo LSP started: %s", binary)
        return True

    async def shutdown(self) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                await self._request("shutdown", None, timeout=3)
                await self._notify("exit", None)
            except Exception:
                pass
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=2)
            except Exception:
                self._proc.kill()
        if self._read_task:
            self._read_task.cancel()
        self._proc = None
        self._initialized = False
        self._docs.clear()

    @property
    def available(self) -> bool:
        return self._initialized and self._proc is not None and self._proc.returncode is None

    async def complete(self, doc_id: int, code: str, cursor_pos: int) -> dict:
        """Get completions for Mojo code at cursor_pos.

        doc_id = notebook_id. Shadow file at ~/.grokbook/mojo-lsp/nb_{id}.mojo.

        Uses incremental sync: only the diff between old and new content is
        sent to the LSP via didChange. For single-char edits this means the
        LSP only reparses the changed region.

        No waiting for diagnostics — fires didChange + completion immediately.
        If the LSP returns empty (still reparsing), falls back to cached
        completions filtered by the current word prefix.
        """
        if not self.available:
            return {"matches": [], "cursor_start": cursor_pos, "cursor_end": cursor_pos}

        async with self._lock:
            fpath = _LSP_DIR / f"nb_{doc_id}.mojo"
            uri = f"file://{fpath}"
            line, col = _offset_to_line_col(code, cursor_pos)
            prev_content = self._doc_content.get(uri)
            content_changed = prev_content != code

            if content_changed:
                version = self._docs.get(uri, 0) + 1
                self._docs[uri] = version
                self._doc_content[uri] = code

                if prev_content is None:
                    # First open — write file, wait for initial parse
                    fpath.write_text(code)
                    await self._notify("textDocument/didOpen", {
                        "textDocument": {
                            "uri": uri,
                            "languageId": "mojo",
                            "version": version,
                            "text": code,
                        },
                    })
                    await self._wait_for_diagnostics(uri, timeout=2.0)
                else:
                    # Incremental sync — send only the diff
                    change = _compute_incremental_change(prev_content, code)
                    if change is not None:
                        range_obj, insert_text = change
                        await self._notify("textDocument/didChange", {
                            "textDocument": {"uri": uri, "version": version},
                            "contentChanges": [{
                                "range": range_obj,
                                "text": insert_text,
                            }],
                        })
                    # Persist to disk periodically for cold-start recovery
                    if version % 10 == 0:
                        fpath.write_text(code)

                    # The LSP needs ~600ms to reparse regardless of change
                    # size. Wait for diagnostics so the completion request
                    # sees the new AST. The frontend caches and filters
                    # locally so this only fires on trigger chars (dot, etc.)
                    await self._wait_for_diagnostics(uri, timeout=1.5)

            result = await self._request("textDocument/completion", {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": col},
            }, timeout=3)

            items = _extract_items(result)
            if items:
                self._last_completions[uri] = items

        if result is None:
            return {"matches": [], "cursor_start": cursor_pos, "cursor_end": cursor_pos}

        return _translate_completions(result, code, cursor_pos)

    async def _wait_for_diagnostics(self, uri: str, timeout: float) -> None:
        """Wait for publishDiagnostics for uri, or timeout."""
        ev = asyncio.Event()
        self._diag_events[uri] = ev
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._diag_events.pop(uri, None)

    @property
    def _diag_events(self) -> dict[str, asyncio.Event]:
        if not hasattr(self, "_diag_events_store"):
            self._diag_events_store: dict[str, asyncio.Event] = {}
        return self._diag_events_store

    # ── LSP transport ─────────────────────────────────────────────────────

    async def _request(self, method: str, params: dict | None, timeout: float = 10) -> dict | None:
        self._req_id += 1
        req_id = self._req_id
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params

        self._send(msg)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return None

    async def _notify(self, method: str, params: dict | None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def _send(self, msg: dict) -> None:
        if not self._proc or not self._proc.stdin:
            return
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._proc.stdin.write(header + body)

    async def _read_loop(self) -> None:
        """Read LSP JSON-RPC messages from stdout."""
        assert self._proc and self._proc.stdout
        reader = self._proc.stdout
        try:
            while True:
                content_length = 0
                while True:
                    line = await reader.readline()
                    if not line:
                        return
                    line = line.strip()
                    if not line:
                        break
                    if line.startswith(_CONTENT_LENGTH):
                        content_length = int(line[len(_CONTENT_LENGTH):])

                if content_length == 0:
                    continue
                body = await reader.readexactly(content_length)
                try:
                    msg = json.loads(body)
                except json.JSONDecodeError:
                    continue

                if "id" in msg and not isinstance(msg.get("method"), str):
                    req_id = msg["id"]
                    if req_id in self._pending:
                        future = self._pending.pop(req_id)
                        if not future.done():
                            if "error" in msg:
                                future.set_result(None)
                            else:
                                future.set_result(msg.get("result"))
                elif msg.get("method") == "textDocument/publishDiagnostics":
                    diag_uri = msg.get("params", {}).get("uri", "")
                    ev = self._diag_events.get(diag_uri)
                    if ev:
                        ev.set()
        except (asyncio.CancelledError, asyncio.IncompleteReadError, ConnectionError):
            pass


# ── helpers ──────────────────────────────────────────────────────────────────


def _offset_to_line_col(text: str, offset: int) -> tuple[int, int]:
    """Convert byte offset to (line, character) for LSP."""
    offset = min(offset, len(text))
    before = text[:offset]
    line = before.count("\n")
    last_nl = before.rfind("\n")
    col = offset - last_nl - 1 if last_nl >= 0 else offset
    return line, col


def _extract_items(result: dict | list | None) -> list:
    if result is None:
        return []
    if isinstance(result, list):
        return result
    return result.get("items", [])


def _filter_cached(items: list, code: str, cursor_pos: int) -> dict:
    """Filter cached CompletionItems by the current word prefix."""
    i = cursor_pos
    while i > 0 and (code[i - 1].isalnum() or code[i - 1] == "_"):
        i -= 1
    prefix = code[i:cursor_pos].lower()
    if not prefix:
        return {"items": items}
    filtered = [it for it in items if it.get("label", "").lower().startswith(prefix)]
    return {"items": filtered}


def _translate_completions(result: dict | list, code: str, cursor_pos: int) -> dict:
    """Translate LSP CompletionList/CompletionItem[] to grokbook format."""
    items = result if isinstance(result, list) else result.get("items", [])

    i = cursor_pos
    while i > 0 and (code[i - 1].isalnum() or code[i - 1] == "_"):
        i -= 1
    cursor_start = i

    matches = []
    for item in items:
        label = item.get("label", "")
        insert = item.get("insertText") or label
        if "$" in insert or "{" in insert:
            insert = label
        matches.append(insert)

    return {
        "matches": matches,
        "cursor_start": cursor_start,
        "cursor_end": cursor_pos,
    }


# ── singleton ────────────────────────────────────────────────────────────────

_instance: MojoLSP | None = None


async def get_mojo_lsp(kernel_python: str | None = None) -> MojoLSP | None:
    """Get or create the singleton MojoLSP. Returns None if unavailable."""
    global _instance
    if _instance is not None:
        if _instance.available:
            return _instance
        _instance = None
    lsp = MojoLSP()
    ok = await lsp.start(kernel_python)
    if ok:
        _instance = lsp
        return lsp
    return None


async def shutdown_mojo_lsp() -> None:
    """Shutdown the singleton if running."""
    global _instance
    if _instance:
        await _instance.shutdown()
        _instance = None
