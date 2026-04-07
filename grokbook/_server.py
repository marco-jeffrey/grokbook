"""Server bootstrap and runner — shared by main.py (dev) and cli.py (production)."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from stario import Relay, RichTracer, Stario
from stario.http.server import Server
from stario.http.writer import CompressionConfig
from stario.telemetry.core import Span

from grokbook.api import api_router
from grokbook.db import Database
from grokbook import envs
from grokbook.handlers import app_router
from grokbook.kernel import KernelPool


def make_bootstrap(db_path: Path, python_path: str | None = None):
    @asynccontextmanager
    async def bootstrap(app: Stario, span: Span):
        nonlocal python_path
        db = await Database.connect(str(db_path))

        # Create welcome notebook on first run
        from grokbook.welcome import ensure_welcome_notebook

        await ensure_welcome_notebook(db)

        # Discover available Python environments (uv + kernelspecs + cwd .venv)
        await envs.refresh()

        # Auto-pick a default interpreter if --python wasn't supplied
        if python_path is None:
            default_env = await envs.pick_default(cwd=Path.cwd())
            if default_env is not None:
                python_path = default_env.path
                if not default_env.has_ipykernel:
                    print(f"Installing ipykernel into {default_env.name}…")
                    ok = False
                    async for kind, line in envs.install_ipykernel(default_env.path):
                        if kind == "done":
                            ok = line == "ok"
                            if not ok:
                                print(f"  WARNING: {line}")
                        else:
                            print(f"  {line}")
                    if ok:
                        await envs.refresh()
                    else:
                        print("  Kernel will start, but ipykernel import may fail.")
                print(f"Using kernel: {default_env.name} ({default_env.path})")

        pool = KernelPool(default_python_path=python_path)
        relay: Relay[str] = Relay()

        static_dir = Path(__file__).parent / "static"
        app.assets("/static", static_dir, name="static")

        app.mount("/api", api_router(db, pool, relay))
        app.mount("/", app_router(db, pool, relay))

        try:
            yield
        finally:
            await pool.shutdown_all()
            from grokbook.mojo_lsp import shutdown_mojo_lsp
            await shutdown_mojo_lsp()
            await db.close()

    return bootstrap


def run_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    db_path: Path = Path("nb.db"),
    python_path: str | None = None,
    mcp_host: str | None = None,
    mcp_port: int = 8081,
) -> None:
    """Start the grokbook server, optionally with MCP server. Blocks until Ctrl+C."""

    async def _run() -> None:
        server = Server(
            make_bootstrap(db_path, python_path),
            RichTracer(),
            host=host,
            port=port,
            compression=CompressionConfig(zstd_level=-1),
        )

        if mcp_host is not None:
            # Run both notebook server and MCP HTTP server concurrently
            os.environ["GROKBOOK_API_URL"] = f"http://127.0.0.1:{port}/api"
            from grokbook.mcp_server import mcp

            await asyncio.gather(
                server.run(),
                mcp.run_async(
                    "streamable-http",
                    host=mcp_host,
                    port=mcp_port,
                    show_banner=False,
                ),
            )
        else:
            await server.run()

    asyncio.run(_run())
