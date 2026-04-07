"""Mojo LSP client for code completions in %%mojo cells.

Manages a long-lived `mojo-lsp-server` subprocess, sends LSP requests over
stdio, and translates completions into grokbook's {matches, cursor_start,
cursor_end} format.

The server is started lazily on first Mojo completion request and reused
across all notebooks. If `mojo-lsp-server` isn't installed, completions
silently return empty lists.
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


def _find_mojo_lsp(kernel_python: str | None = None) -> str | None:
    """Locate the mojo-lsp-server binary.

    If kernel_python is provided (the notebook's active kernel path),
    check its bin/ directory first — if the kernel is a pixi/conda env
    with Mojo, the LSP server lives right next to Python.
    """
    # 0. Same bin dir as the active kernel (pixi/conda envs bundle mojo here)
    if kernel_python:
        candidate = Path(kernel_python).parent / "mojo-lsp-server"
        if candidate.exists():
            return str(candidate)
    # 1. On PATH
    found = shutil.which("mojo-lsp-server")
    if found:
        return found
    # 2. Common pixi/magic locations relative to cwd
    cwd = Path.cwd()
    for envdir in [cwd / ".pixi" / "envs", cwd / ".magic" / "envs"]:
        if envdir.is_dir():
            for env in envdir.iterdir():
                candidate = env / "bin" / "mojo-lsp-server"
                if candidate.exists():
                    return str(candidate)
    # 3. ~/.modular (legacy installs)
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
    """Build an environment dict for the LSP subprocess.

    The critical variable is MODULAR_HOME — without it the LSP can't find
    the Mojo stdlib ('unable to locate module std'). For pixi/conda envs,
    MODULAR_HOME lives at <prefix>/share/max.
    """
    env = os.environ.copy()
    bin_dir = Path(binary).parent
    prefix = bin_dir.parent  # e.g. .pixi/envs/default

    # Set MODULAR_HOME if not already set
    if "MODULAR_HOME" not in env:
        modular_home = prefix / "share" / "max"
        if modular_home.is_dir():
            env["MODULAR_HOME"] = str(modular_home)

    # Ensure the env's bin is on PATH (for any tools the LSP shells out to)
    env["PATH"] = str(bin_dir) + ":" + env.get("PATH", "")

    # Set CONDA_PREFIX if it looks like a conda/pixi env
    if (prefix / "conda-meta").is_dir():
        env["CONDA_PREFIX"] = str(prefix)

    return env


def _find_project_root(kernel_python: str | None) -> str | None:
    """Try to find the pixi/mojo project root for rootUri.

    Walks up from the kernel's env prefix looking for pixi.toml or mojoproject.toml.
    Falls back to cwd if it has one.
    """
    # Check cwd first
    for name in ("pixi.toml", "mojoproject.toml"):
        if (Path.cwd() / name).exists():
            return str(Path.cwd())
    # Check relative to kernel path
    if kernel_python:
        # .pixi/envs/default/bin/python → walk up to find pixi.toml
        d = Path(kernel_python).parent
        for _ in range(6):
            d = d.parent
            if (d / "pixi.toml").exists() or (d / "mojoproject.toml").exists():
                return str(d)
    return None


class MojoLSP:
    """Async LSP client for mojo-lsp-server over stdio."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._read_task: asyncio.Task | None = None
        self._docs: dict[str, int] = {}  # uri -> version
        self._doc_content: dict[str, str] = {}  # uri -> last sent content
        self._diag_events: dict[str, asyncio.Event] = {}  # uri -> fires on publishDiagnostics
        self._last_completions: dict[str, list] = {}  # uri -> last CompletionItem[]
        self._initialized = False
        self._lock = asyncio.Lock()
        self._tmpdir: str | None = None

    async def start(self, kernel_python: str | None = None) -> bool:
        """Start the LSP server. Returns False if mojo-lsp-server not found."""
        binary = _find_mojo_lsp(kernel_python)
        if not binary:
            log.info("mojo-lsp-server not found — Mojo completions disabled")
            return False

        import tempfile
        self._tmpdir = tempfile.mkdtemp(prefix="grokbook-mojo-")

        # The LSP needs MODULAR_HOME to find the Mojo stdlib. Derive it
        # from the binary's env prefix (pixi/conda envs store it at
        # <prefix>/share/max). Also set PATH and CONDA_PREFIX so the
        # LSP can resolve any additional tooling.
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

        # LSP initialize handshake. Use the pixi/mojo project root as
        # rootUri when available — this lets the LSP resolve project imports.
        project_root = _find_project_root(kernel_python) or self._tmpdir
        resp = await self._request("initialize", {
            "processId": os.getpid(),
            "capabilities": {
                "textDocument": {
                    "completion": {
                        "completionItem": {"snippetSupport": False},
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
        # Clean up temp dir
        if self._tmpdir:
            import shutil as _shutil
            _shutil.rmtree(self._tmpdir, ignore_errors=True)

    @property
    def available(self) -> bool:
        return self._initialized and self._proc is not None and self._proc.returncode is None

    async def complete(self, doc_id: int, code: str, cursor_pos: int) -> dict:
        """Get completions for Mojo code at cursor_pos.

        doc_id is typically the notebook_id — the shadow file is one per
        notebook (all %%mojo cells concatenated). The handler does the
        concatenation and cursor offset translation; this method just sends
        the full code and cursor to the LSP.

        Fast path: if the document content hasn't changed since last call
        (user just moved cursor), skip didChange entirely → ~0ms overhead.

        Returns {matches, cursor_start, cursor_end} matching grokbook's format.
        """
        if not self.available:
            return {"matches": [], "cursor_start": cursor_pos, "cursor_end": cursor_pos}

        async with self._lock:
            fpath = Path(self._tmpdir) / f"nb_{doc_id}.mojo"
            uri = f"file://{fpath}"
            line, col = _offset_to_line_col(code, cursor_pos)
            content_changed = self._doc_content.get(uri) != code

            if content_changed:
                prev_content = self._doc_content.get(uri, "")
                version = self._docs.get(uri, 0) + 1
                self._docs[uri] = version
                self._doc_content[uri] = code

                # Classify the change: small edits (< 10 char delta) get
                # a short wait + cache fallback. Big structural changes
                # (new lines, large rewrites) wait longer for fresh results.
                small_edit = abs(len(code) - len(prev_content)) < 10

                if version == 1:
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
                    await self._notify("textDocument/didChange", {
                        "textDocument": {"uri": uri, "version": version},
                        "contentChanges": [{"text": code}],
                    })
                    if small_edit:
                        # Brief wait — fall back to cached completions if LSP
                        # hasn't finished reparsing. Cache filtering gives
                        # ~150ms response for normal typing.
                        await self._wait_for_diagnostics(uri, timeout=0.15)
                    else:
                        # Structural change — need fresh results (dot completion,
                        # different function, etc.). Wait longer.
                        await self._wait_for_diagnostics(uri, timeout=1.5)
            else:
                small_edit = False

            result = await self._request("textDocument/completion", {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": col},
            }, timeout=3)

            items = _extract_items(result)
            if items:
                self._last_completions[uri] = items
            elif small_edit and uri in self._last_completions:
                # LSP still reparsing — filter cached completions by the
                # current word prefix for instant response.
                items = self._last_completions[uri]
                result = _filter_cached(items, code, cursor_pos)

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
                # Read headers until blank line
                content_length = 0
                while True:
                    line = await reader.readline()
                    if not line:
                        return  # EOF
                    line = line.strip()
                    if not line:
                        break  # end of headers
                    if line.startswith(_CONTENT_LENGTH):
                        content_length = int(line[len(_CONTENT_LENGTH):])

                if content_length == 0:
                    continue
                body = await reader.readexactly(content_length)
                try:
                    msg = json.loads(body)
                except json.JSONDecodeError:
                    continue

                # Route response to waiting future
                if "id" in msg and not isinstance(msg.get("method"), str):
                    req_id = msg["id"]
                    if req_id in self._pending:
                        future = self._pending.pop(req_id)
                        if not future.done():
                            if "error" in msg:
                                future.set_result(None)
                            else:
                                future.set_result(msg.get("result"))
                # publishDiagnostics notification → signal that reparse is done
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
    """Pull the items list out of an LSP CompletionList or CompletionItem[]."""
    if result is None:
        return []
    if isinstance(result, list):
        return result
    return result.get("items", [])


def _filter_cached(items: list, code: str, cursor_pos: int) -> dict:
    """Filter a cached CompletionItem list by the current word prefix."""
    # Find the word being typed
    i = cursor_pos
    while i > 0 and (code[i - 1].isalnum() or code[i - 1] == "_"):
        i -= 1
    prefix = code[i:cursor_pos].lower()
    if not prefix:
        return {"items": items}  # no filtering, return all
    filtered = [it for it in items if it.get("label", "").lower().startswith(prefix)]
    return {"items": filtered}


def _translate_completions(result: dict | list, code: str, cursor_pos: int) -> dict:
    """Translate LSP CompletionList/CompletionItem[] to grokbook format."""
    items = result if isinstance(result, list) else result.get("items", [])

    # Find the word being typed to determine cursor_start
    i = cursor_pos
    while i > 0 and (code[i - 1].isalnum() or code[i - 1] == "_"):
        i -= 1
    cursor_start = i

    matches = []
    for item in items:
        label = item.get("label", "")
        # Use insertText if available, else label
        insert = item.get("insertText") or label
        # Skip snippets with placeholders
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
    """Get or create the singleton MojoLSP. Returns None if unavailable.

    Pass kernel_python (the notebook's active kernel path) so the LSP
    binary can be found relative to a pixi/conda env's bin directory.
    """
    global _instance
    if _instance is not None:
        if _instance.available:
            return _instance
        # Server died — try restarting
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
