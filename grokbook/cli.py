"""Grokbook CLI — interactive notebook server for learning CS."""

import json
import os
import sys
from pathlib import Path

import typer

app = typer.Typer(
    name="grokbook",
    help="Interactive notebook server for learning computer science.",
    no_args_is_help=False,
    invoke_without_command=True,
)

BANNER = r"""
   __ _ _ __ ___  | | __ | |__   ___   ___  | | __
  / _` | '__/ _ \ | |/ / | '_ \ / _ \ / _ \ | |/ /
 | (_| | | | (_) ||   <  | |_) | (_) | (_) ||   <
  \__, |_|  \___/ |_|\_\ |_.__/ \___/ \___/ |_|\_\
   __/ |
  |___/
"""


def _default_db_path() -> Path:
    return Path.home() / ".grokbook" / "grokbook.db"


def _print_banner(
    host: str, port: int, mcp_host: str, mcp_port: int, python_path: str | None, db_path: Path
) -> None:
    typer.secho(BANNER, fg=typer.colors.CYAN, bold=True)

    display_host = "localhost" if host == "0.0.0.0" else host
    mcp_display_host = "localhost" if mcp_host == "127.0.0.1" else mcp_host
    url = f"http://{display_host}:{port}"
    mcp_url = f"http://{mcp_display_host}:{mcp_port}"
    python_display = python_path or sys.executable

    typer.echo(f"  Server:   {url}")
    typer.echo(f"  MCP:      {mcp_url}")
    typer.echo(f"  Python:   {python_display}")
    typer.echo(f"  Database: {db_path}")
    typer.echo()

    # MCP config block — ready to copy-paste
    mcp_config = {
        "mcpServers": {
            "grokbook": {
                "command": "grokbook",
                "args": ["mcp", "--allow-code-execution"],
                "env": {"GROKBOOK_API_URL": f"http://localhost:{port}/api"},
            }
        }
    }
    typer.secho("  MCP config for stdio (Claude Desktop / Claude Code):", dim=True)
    for line in json.dumps(mcp_config, indent=2).splitlines():
        typer.secho(f"  {line}", dim=True)
    typer.echo()

    if host == "0.0.0.0":
        typer.secho(
            "  ⚠ Binding to 0.0.0.0 — the server is accessible to anyone on your network.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        typer.secho(
            "    Use --host 127.0.0.1 to restrict to localhost only.\n",
            fg=typer.colors.YELLOW,
            err=True,
        )

    typer.secho("  Press Ctrl+C to stop\n", dim=True)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address for both servers"),
    port: int = typer.Option(8080, "--port", "-p", help="Notebook server port"),
    mcp_port: int = typer.Option(8081, "--mcp-port", help="MCP server port"),
    python: str | None = typer.Option(None, "--python", help="Python interpreter for kernels"),
    db: Path | None = typer.Option(None, "--db", help="Database file path"),
    allow_code_execution: bool = typer.Option(
        False, "--allow-code-execution",
        help="Enable code execution tools in the MCP server",
    ),
) -> None:
    """Start the grokbook notebook server with MCP."""
    if allow_code_execution:
        os.environ["GROKBOOK_ALLOW_CODE_EXECUTION"] = "1"

    db_path = db if db else _default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    mcp_host = host

    _print_banner(host, port, mcp_host, mcp_port, python, db_path)

    from grokbook._server import run_server

    run_server(
        host=host,
        port=port,
        db_path=db_path,
        python_path=python,
        mcp_host=mcp_host,
        mcp_port=mcp_port,
    )


@app.command()
def mcp(
    host: str | None = typer.Option(None, "--host", help="HTTP transport host (omit for stdio)"),
    port: int = typer.Option(8081, "--port", "-p", help="HTTP transport port"),
    api_url: str = typer.Option(
        "http://localhost:8080/api",
        "--api-url",
        envvar="GROKBOOK_API_URL",
        help="URL of the grokbook API server",
    ),
    allow_code_execution: bool = typer.Option(
        False, "--allow-code-execution",
        help="Enable code execution tools (execute_cell, run_all_cells, etc.)",
    ),
) -> None:
    """Run the MCP server standalone (stdio mode for Claude Desktop)."""
    os.environ["GROKBOOK_API_URL"] = api_url
    if allow_code_execution:
        os.environ["GROKBOOK_ALLOW_CODE_EXECUTION"] = "1"

    if host and host == "0.0.0.0":
        typer.secho(
            "⚠ Binding MCP to 0.0.0.0 — anyone on your network can execute arbitrary Python code.",
            fg=typer.colors.YELLOW,
            err=True,
        )

    from grokbook.mcp_server import run_mcp

    run_mcp(host=host, port=port)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Interactive notebook server for learning computer science."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)
