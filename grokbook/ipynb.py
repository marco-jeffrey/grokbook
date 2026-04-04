"""Import/export Jupyter .ipynb notebooks (nbformat v4)."""

import json
import re

from grokbook.db import Database
from grokbook.state import Cell, Notebook

_ANSI = re.compile(r"\x1b\[[0-9;]*[mK]")


# ── Import ─────────────────────────────────────────────────────────────────


def _flatten_outputs(outputs: list[dict]) -> tuple[str, str]:
    """Flatten ipynb output list → (text, status)."""
    parts: list[str] = []
    status = ""
    for out in outputs:
        otype = out.get("output_type", "")
        if otype == "stream":
            text = out.get("text", [])
            parts.append("".join(text) if isinstance(text, list) else text)
        elif otype in ("execute_result", "display_data"):
            bundle = out.get("data", {})
            for mime in ("text/plain", "text/html"):
                if mime in bundle:
                    val = bundle[mime]
                    parts.append("".join(val) if isinstance(val, list) else val)
                    break
        elif otype == "error":
            status = "error"
            ename = out.get("ename", "Error")
            evalue = out.get("evalue", "")
            parts.append(f"{ename}: {evalue}")
    if not status and parts:
        status = "ok"
    return "\n".join(parts), status


async def import_ipynb(db: Database, file_bytes: bytes, name: str | None = None) -> int:
    """Parse .ipynb bytes, create a notebook + cells in the DB. Returns notebook ID."""
    nb_data = json.loads(file_bytes)
    if nb_data.get("nbformat", 0) < 4:
        raise ValueError("Only nbformat v4+ is supported")

    # Derive name from metadata or use provided name
    if not name:
        meta = nb_data.get("metadata", {})
        name = meta.get("title", "Imported Notebook")

    nb_id = await db.create_notebook(name)

    for i, cell in enumerate(nb_data.get("cells", [])):
        cell_type = cell.get("cell_type", "code")
        if cell_type == "raw":
            cell_type = "code"  # treat raw cells as code

        # source is a list of strings in ipynb
        source = cell.get("source", [])
        source_text = "".join(source) if isinstance(source, list) else source

        outputs = cell.get("outputs", [])
        output_text, status = _flatten_outputs(outputs)

        exec_count = cell.get("execution_count") or 0

        cell_id = await db.insert_cell(nb_id, cell_type=cell_type)
        await db.update_cell(cell_id, source_text, output_text, status, exec_count)

    return nb_id


# ── Export ─────────────────────────────────────────────────────────────────


def _source_to_lines(source: str) -> list[str]:
    """Convert string to nbformat source (list of lines with \\n)."""
    if not source:
        return []
    lines = source.split("\n")
    result = [line + "\n" for line in lines[:-1]]
    if lines[-1]:  # don't add empty trailing string
        result.append(lines[-1])
    return result


def _try_parse_rich_blocks(output: str) -> list[dict] | None:
    """Try to parse the JSON-encoded rich output blocks from the kernel.

    When output contains rich MIME data (images, HTML, etc.), the kernel
    stores it as JSON: [{"mime": "image/png", "data": "base64..."}, ...]
    Returns the parsed list or None if it's plain text.
    """
    if not output.startswith("["):
        return None
    try:
        blocks = json.loads(output)
        if isinstance(blocks, list) and blocks and "mime" in blocks[0]:
            return blocks
    except (json.JSONDecodeError, TypeError, KeyError):
        pass
    return None


def _blocks_to_ipynb_outputs(blocks: list[dict]) -> list[dict]:
    """Convert our internal rich blocks to proper ipynb output dicts."""
    outputs: list[dict] = []
    stream_parts: list[str] = []

    for block in blocks:
        mime = block.get("mime", "text/plain")
        data = block.get("data", "")

        if mime == "text/plain":
            stream_parts.append(data)
        else:
            # Flush any pending stream text first
            if stream_parts:
                outputs.append({
                    "output_type": "stream",
                    "name": "stdout",
                    "text": _source_to_lines("".join(stream_parts)),
                })
                stream_parts = []

            # Build a proper display_data with MIME bundle
            mime_bundle: dict = {}
            if mime in ("image/png", "image/jpeg"):
                # Binary MIME: data is already base64, store as single string
                mime_bundle[mime] = data
            else:
                # Text MIME (text/html, image/svg+xml): store as list of lines
                mime_bundle[mime] = _source_to_lines(data) if isinstance(data, str) else data

            # Always include text/plain fallback if not already the mime
            if mime != "text/plain":
                mime_bundle["text/plain"] = [f"<{mime} output>"]

            outputs.append({
                "output_type": "display_data",
                "metadata": {},
                "data": mime_bundle,
            })

    # Flush remaining stream text
    if stream_parts:
        outputs.append({
            "output_type": "stream",
            "name": "stdout",
            "text": _source_to_lines("".join(stream_parts)),
        })

    return outputs


def _cell_to_ipynb(cell: Cell) -> dict:
    """Convert a Cell dataclass to an ipynb cell dict."""
    base: dict = {
        "cell_type": cell.cell_type,
        "id": f"cell-{cell.id}",
        "metadata": {},
        "source": _source_to_lines(cell.input),
    }
    if cell.cell_type == "code":
        base["execution_count"] = cell.execution_count or None
        outputs: list[dict] = []
        if cell.output:
            if cell.status == "error":
                clean = _ANSI.sub("", cell.output)
                outputs.append({
                    "output_type": "error",
                    "ename": "Error",
                    "evalue": clean,
                    "traceback": [cell.output],
                })
            else:
                # Check if output contains rich MIME blocks (JSON)
                rich_blocks = _try_parse_rich_blocks(cell.output)
                if rich_blocks:
                    outputs = _blocks_to_ipynb_outputs(rich_blocks)
                else:
                    outputs.append({
                        "output_type": "stream",
                        "name": "stdout",
                        "text": _source_to_lines(cell.output),
                    })
        base["outputs"] = outputs
    return base


def export_ipynb(notebook: Notebook, cells: list[Cell]) -> str:
    """Build a valid nbformat v4 JSON string from a notebook + cells."""
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "version": "3.14.0",
            },
        },
        "cells": [_cell_to_ipynb(c) for c in cells],
    }
    return json.dumps(nb, indent=1, ensure_ascii=False)
