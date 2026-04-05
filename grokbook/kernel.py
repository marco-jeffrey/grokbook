import asyncio
import json
import re
from queue import Empty

from jupyter_client import AsyncKernelManager as _KM

_ANSI = re.compile(r"\x1b\[[0-9;]*[mK]")

# MIME types in priority order for rich output
_RICH_MIMES = ("image/png", "image/jpeg", "image/svg+xml", "text/html", "text/plain")


class KernelManager:
    def __init__(self, python_path: str | None = None) -> None:
        self._km = _KM(kernel_name="python3")
        self.python_path = python_path
        if python_path:
            # kernel_cmd is ignored by modern jupyter_client — override the
            # kernel spec's argv directly so the provisioner launches the
            # correct interpreter.
            spec = self._km.kernel_spec
            spec.argv = [
                python_path, "-m", "ipykernel_launcher", "-f", "{connection_file}"
            ]
        self._kc = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        await self._km.start_kernel()
        self._kc = self._km.client()
        self._kc.start_channels()
        await self._kc.wait_for_ready(timeout=30)

    @staticmethod
    def _extract_rich(data: dict) -> dict | None:
        """Pick the best MIME type from a display_data/execute_result bundle."""
        for mime in _RICH_MIMES:
            if mime in data:
                return {"mime": mime, "data": data[mime]}
        return None

    async def execute(self, code: str) -> tuple[str, bool, int]:
        """Execute code in the persistent kernel. Serialised via lock.
        No arbitrary timeout — waits as long as the kernel is alive.
        Returns (output_string, is_error, execution_count)."""
        async with self._lock:
            msg_id = self._kc.execute(code)
            blocks: list[dict] = []
            is_error = False
            has_rich = False
            exec_count = 0

            while True:
                try:
                    msg = await self._kc.get_iopub_msg(timeout=1)
                except (TimeoutError, Empty):
                    if not await self._km.is_alive():
                        blocks.append({"mime": "text/plain", "data": "(kernel died during execution)"})
                        is_error = True
                        break
                    continue

                if msg["parent_header"].get("msg_id") != msg_id:
                    continue
                t = msg["msg_type"]
                if t == "stream":
                    blocks.append({"mime": "text/plain", "data": msg["content"]["text"]})
                elif t in ("execute_result", "display_data"):
                    if t == "execute_result":
                        exec_count = msg["content"].get("execution_count", exec_count)
                    block = self._extract_rich(msg["content"]["data"])
                    if block:
                        if block["mime"] != "text/plain":
                            has_rich = True
                        blocks.append(block)
                elif t == "error":
                    is_error = True
                    tb = msg["content"]["traceback"]
                    blocks.append({"mime": "text/plain", "data": "\n".join(tb)})
                elif t == "execute_input":
                    exec_count = msg["content"].get("execution_count", exec_count)
                elif t == "status" and msg["content"]["execution_state"] == "idle":
                    break

            return self._blocks_to_output(blocks, has_rich), is_error, exec_count

    async def execute_streaming(self, code: str):
        """Async generator that yields (output_str, is_error, is_final, exec_count) tuples.

        Each yield contains the accumulated output so far. The lock is held
        for the entire execution. Callers can patch the UI on each yield."""
        async with self._lock:
            msg_id = self._kc.execute(code)
            blocks: list[dict] = []
            is_error = False
            has_rich = False
            exec_count = 0

            while True:
                try:
                    msg = await self._kc.get_iopub_msg(timeout=1)
                except (TimeoutError, Empty):
                    if not await self._km.is_alive():
                        blocks.append({"mime": "text/plain", "data": "(kernel died during execution)"})
                        is_error = True
                        yield self._blocks_to_output(blocks, has_rich), is_error, True, exec_count
                        return
                    continue

                if msg["parent_header"].get("msg_id") != msg_id:
                    continue
                t = msg["msg_type"]
                if t == "stream":
                    blocks.append({"mime": "text/plain", "data": msg["content"]["text"]})
                    yield self._blocks_to_output(blocks, has_rich), is_error, False, exec_count
                elif t in ("execute_result", "display_data"):
                    if t == "execute_result":
                        exec_count = msg["content"].get("execution_count", exec_count)
                    block = self._extract_rich(msg["content"]["data"])
                    if block:
                        if block["mime"] != "text/plain":
                            has_rich = True
                        blocks.append(block)
                        yield self._blocks_to_output(blocks, has_rich), is_error, False, exec_count
                elif t == "error":
                    is_error = True
                    tb = msg["content"]["traceback"]
                    blocks.append({"mime": "text/plain", "data": "\n".join(tb)})
                    yield self._blocks_to_output(blocks, has_rich), is_error, True, exec_count
                    return
                elif t == "execute_input":
                    exec_count = msg["content"].get("execution_count", exec_count)
                elif t == "status" and msg["content"]["execution_state"] == "idle":
                    yield self._blocks_to_output(blocks, has_rich), is_error, True, exec_count
                    return

    @staticmethod
    def _process_cr(text: str) -> str:
        """Simulate terminal carriage-return behavior.

        When \\r appears mid-line, the text after it overwrites the line
        from the beginning — this is how tqdm progress bars work.
        We process this so HTML <pre> output looks correct.
        """
        lines = text.split("\n")
        result = []
        for line in lines:
            if "\r" in line:
                # Split by \r, each segment overwrites from column 0
                segments = line.split("\r")
                # Start with empty line, apply each segment as an overwrite
                buf = ""
                for seg in segments:
                    if seg:
                        # Overwrite from position 0, keep any trailing chars
                        buf = seg + buf[len(seg):]
                result.append(buf)
            else:
                result.append(line)
        return "\n".join(result)

    @staticmethod
    def _blocks_to_output(blocks: list[dict], has_rich: bool) -> str:
        if not blocks:
            return ""
        if not has_rich:
            raw = "".join(b["data"] for b in blocks)
            return KernelManager._process_cr(raw)
        return json.dumps(blocks)

    _VARS_SNIPPET = (
        "import json as _json\n"
        "_vars = []\n"
        "for _n, _v in list(globals().items()):\n"
        "    if _n.startswith('_') or _n in ('In','Out','get_ipython','exit','quit'): continue\n"
        "    _info = {'name': _n, 'type': type(_v).__name__}\n"
        "    if hasattr(_v, 'shape'): _info['shape'] = str(_v.shape)\n"
        "    if hasattr(_v, 'dtype'): _info['dtype'] = str(_v.dtype)\n"
        "    try:\n"
        "        _info['size'] = str(len(_v))\n"
        "    except TypeError:\n"
        "        pass\n"
        "    _vars.append(_info)\n"
        "print(_json.dumps(_vars))\n"
        "del _json, _vars, _n, _v, _info\n"
    )

    async def get_variables(self) -> list[dict]:
        """Run introspection snippet and return list of variable dicts."""
        async with self._lock:
            msg_id = self._kc.execute(self._VARS_SNIPPET, silent=True)
            output = ""
            while True:
                try:
                    msg = await self._kc.get_iopub_msg(timeout=2)
                except (TimeoutError, Empty):
                    if not await self._km.is_alive():
                        break
                    continue
                if msg["parent_header"].get("msg_id") != msg_id:
                    continue
                t = msg["msg_type"]
                if t == "stream":
                    output += msg["content"]["text"]
                elif t == "status" and msg["content"]["execution_state"] == "idle":
                    break
            try:
                return json.loads(output)
            except (json.JSONDecodeError, TypeError):
                return []

    async def inspect(self, code: str, cursor_pos: int) -> str:
        """Return plain-text docstring/signature for object at cursor."""
        async with self._lock:
            msg_id = self._kc.inspect(code, cursor_pos, detail_level=0)
            for _ in range(20):
                try:
                    msg = await self._kc.get_shell_msg(timeout=5)
                    if msg["parent_header"].get("msg_id") == msg_id:
                        c = msg["content"]
                        if c.get("found"):
                            return _ANSI.sub("", c.get("data", {}).get("text/plain", ""))
                        return ""
                except (TimeoutError, Empty):
                    break
            return ""

    async def complete(self, code: str, cursor_pos: int) -> dict:
        """Send complete_request to kernel, return matches + cursor range."""
        async with self._lock:
            msg_id = self._kc.complete(code, cursor_pos)
            for _ in range(20):
                try:
                    msg = await self._kc.get_shell_msg(timeout=5)
                    if msg["parent_header"].get("msg_id") == msg_id:
                        c = msg["content"]
                        return {
                            "matches": c.get("matches", []),
                            "cursor_start": c.get("cursor_start", cursor_pos),
                            "cursor_end": c.get("cursor_end", cursor_pos),
                        }
                except (TimeoutError, Empty):
                    break
            return {"matches": [], "cursor_start": cursor_pos, "cursor_end": cursor_pos}

    async def get_state(self) -> str:
        try:
            alive = await self._km.is_alive()
            if not alive:
                return "dead"
            return "busy" if self._lock.locked() else "idle"
        except Exception:
            return "dead"

    async def interrupt(self) -> None:
        """Send interrupt signal to the kernel (like Ctrl+C)."""
        await self._km.interrupt_kernel()

    async def restart(self) -> None:
        if self._kc:
            self._kc.stop_channels()
        await self._km.restart_kernel()
        self._kc = self._km.client()
        self._kc.start_channels()
        await self._kc.wait_for_ready(timeout=30)

    async def shutdown(self) -> None:
        if self._kc:
            self._kc.stop_channels()
        await self._km.shutdown_kernel(now=True)


class KernelPool:
    """Lazily starts and caches one KernelManager per notebook_id.

    Evicts the least-recently-used kernel when max_kernels is reached.
    """

    def __init__(self, max_kernels: int = 5, default_python_path: str | None = None) -> None:
        self._kernels: dict[int, KernelManager] = {}
        self._max_kernels = max_kernels
        self._default_python_path = default_python_path

    @property
    def default_python_path(self) -> str | None:
        return self._default_python_path

    async def _evict_lru(self) -> None:
        """Shut down the least-recently-used idle kernel to make room."""
        for nb_id in list(self._kernels):
            km = self._kernels[nb_id]
            if not km._lock.locked():
                await km.shutdown()
                del self._kernels[nb_id]
                return
        # All kernels busy — evict the oldest anyway
        nb_id = next(iter(self._kernels))
        await self._kernels[nb_id].shutdown()
        del self._kernels[nb_id]

    async def get(
        self, notebook_id: int, python_path: str | None = None
    ) -> KernelManager:
        """Get or create a kernel for a notebook.

        If a kernel exists but with a different python_path than requested,
        the existing one is shut down and replaced. This is how the UI
        switches kernels mid-session.
        """
        target = python_path or self._default_python_path
        if notebook_id in self._kernels:
            existing = self._kernels[notebook_id]
            if existing.python_path == target:
                # Move to end (most recently used)
                self._kernels[notebook_id] = self._kernels.pop(notebook_id)
                return existing
            # Env changed — replace the kernel
            await existing.shutdown()
            del self._kernels[notebook_id]
        if len(self._kernels) >= self._max_kernels:
            await self._evict_lru()
        km = KernelManager(python_path=target)
        await km.start()
        self._kernels[notebook_id] = km
        return km

    async def set_env(self, notebook_id: int, python_path: str | None) -> None:
        """Force the kernel for a notebook to use python_path, restarting if needed."""
        if notebook_id in self._kernels:
            existing = self._kernels[notebook_id]
            if existing.python_path == python_path:
                return
            await existing.shutdown()
            del self._kernels[notebook_id]
        # Next .get() will create a fresh one with the new path

    async def restart(self, notebook_id: int) -> None:
        if notebook_id in self._kernels:
            await self._kernels[notebook_id].restart()

    async def get_state(self, notebook_id: int) -> str:
        if notebook_id not in self._kernels:
            return "idle"
        return await self._kernels[notebook_id].get_state()

    async def shutdown_all(self) -> None:
        for km in self._kernels.values():
            await km.shutdown()
        self._kernels.clear()
