import asyncio
import re
from queue import Empty

from jupyter_client import AsyncKernelManager as _KM

_ANSI = re.compile(r"\x1b\[[0-9;]*[mK]")


class KernelManager:
    def __init__(self) -> None:
        self._km = _KM(kernel_name="python3")
        self._kc = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        await self._km.start_kernel()
        self._kc = self._km.client()
        self._kc.start_channels()
        await self._kc.wait_for_ready(timeout=30)

    async def execute(self, code: str) -> tuple[str, bool]:
        """Execute code in the persistent kernel. Serialised via lock.
        No arbitrary timeout — waits as long as the kernel is alive."""
        async with self._lock:
            msg_id = self._kc.execute(code)
            outputs: list[str] = []
            is_error = False

            while True:
                try:
                    msg = await self._kc.get_iopub_msg(timeout=1)
                except (TimeoutError, Empty):
                    # No message yet — check if kernel is still alive
                    if not await self._km.is_alive():
                        outputs.append("(kernel died during execution)")
                        is_error = True
                        break
                    continue

                # Skip messages from other requests (e.g. kernel startup)
                if msg["parent_header"].get("msg_id") != msg_id:
                    continue
                t = msg["msg_type"]
                if t == "stream":
                    outputs.append(msg["content"]["text"])
                elif t == "execute_result":
                    outputs.append(msg["content"]["data"].get("text/plain", ""))
                elif t == "display_data":
                    if "text/plain" in msg["content"]["data"]:
                        outputs.append(msg["content"]["data"]["text/plain"])
                elif t == "error":
                    is_error = True
                    tb = msg["content"]["traceback"]
                    outputs.append("\n".join(_ANSI.sub("", line) for line in tb))
                elif t == "status" and msg["content"]["execution_state"] == "idle":
                    break

            return "".join(outputs) or "(no output)", is_error

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
    """Lazily starts and caches one KernelManager per notebook_id."""

    def __init__(self) -> None:
        self._kernels: dict[int, KernelManager] = {}

    async def get(self, notebook_id: int) -> KernelManager:
        if notebook_id not in self._kernels:
            km = KernelManager()
            await km.start()
            self._kernels[notebook_id] = km
        return self._kernels[notebook_id]

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
