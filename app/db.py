import logging

import aiosqlite

from app.state import Cell, Notebook

log = logging.getLogger(__name__)

_SCHEMA_VERSION = 2

_CREATE_NOTEBOOKS = """
CREATE TABLE IF NOT EXISTS notebooks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL DEFAULT 'Untitled',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_CREATE_CELLS = """
CREATE TABLE IF NOT EXISTS cells (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    notebook_id INTEGER NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    order_index INTEGER NOT NULL DEFAULT 0,
    cell_type   TEXT NOT NULL DEFAULT 'code',
    input       TEXT NOT NULL DEFAULT '',
    output      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT ''
)
"""


def _row_to_cell(r) -> Cell:
    return Cell(
        id=r["id"],
        notebook_id=r["notebook_id"],
        cell_type=r["cell_type"] if "cell_type" in r.keys() else "code",
        input=r["input"],
        output=r["output"],
        status=r["status"],
    )


async def _get_version(conn: aiosqlite.Connection) -> int:
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ) as cur:
        if not await cur.fetchone():
            return 0
    async with conn.execute("SELECT version FROM schema_version") as cur:
        row = await cur.fetchone()
    return row["version"] if row else 0


async def _set_version(conn: aiosqlite.Connection, version: int) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    async with conn.execute("SELECT COUNT(*) AS n FROM schema_version") as cur:
        row = await cur.fetchone()
    if row["n"] == 0:
        await conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    else:
        await conn.execute("UPDATE schema_version SET version = ?", (version,))


async def _migrate_v0_to_v1(conn: aiosqlite.Connection) -> None:
    """Initial schema: notebooks + cells tables, migrate orphan cells."""
    log.info("Migration v0→v1: creating base schema")
    await conn.execute(_CREATE_NOTEBOOKS)

    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cells'"
    ) as cur:
        exists = await cur.fetchone()

    if not exists:
        await conn.execute(_CREATE_CELLS)
    else:
        # Add missing columns to legacy cells table
        for col, dflt in [
            ("notebook_id", "0"),
            ("order_index", "0"),
            ("status", "''"),
            ("cell_type", "'code'"),
        ]:
            async with conn.execute(
                "SELECT COUNT(*) AS n FROM pragma_table_info('cells') WHERE name = ?",
                (col,),
            ) as cur:
                row = await cur.fetchone()
            if row["n"] == 0:
                log.info("Migration v0→v1: adding column %s to cells", col)
                await conn.execute(
                    f"ALTER TABLE cells ADD COLUMN {col} DEFAULT {dflt}"
                )

        # Migrate orphan cells to a default notebook
        async with conn.execute(
            "SELECT COUNT(*) AS n FROM cells WHERE notebook_id = 0 OR notebook_id IS NULL"
        ) as cur:
            r = await cur.fetchone()
        if r["n"] > 0:
            log.info("Migration v0→v1: adopting %d orphan cells", r["n"])
            cur = await conn.execute(
                "INSERT INTO notebooks (name) VALUES ('Untitled')"
            )
            nb_id = cur.lastrowid
            await conn.execute(
                "UPDATE cells SET notebook_id = ? WHERE notebook_id = 0 OR notebook_id IS NULL",
                (nb_id,),
            )
            async with conn.execute(
                "SELECT id FROM cells WHERE notebook_id = ? ORDER BY id",
                (nb_id,),
            ) as cur2:
                rows = await cur2.fetchall()
            for i, row in enumerate(rows):
                await conn.execute(
                    "UPDATE cells SET order_index = ? WHERE id = ?", (i, row["id"])
                )


async def _migrate_v1_to_v2(conn: aiosqlite.Connection) -> None:
    """V2: schema_version tracking (no structural changes, just stamps the version)."""
    log.info("Migration v1→v2: stamping schema version")


_MIGRATIONS = [
    (0, 1, _migrate_v0_to_v1),
    (1, 2, _migrate_v1_to_v2),
]


class Database:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    @classmethod
    async def connect(cls, path: str) -> "Database":
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")

        current = await _get_version(conn)
        for from_v, to_v, migrate_fn in _MIGRATIONS:
            if current == from_v:
                await migrate_fn(conn)
                current = to_v
        await _set_version(conn, current)

        await conn.commit()
        return cls(conn)

    # ── notebooks ─────────────────────────────────────────────────────────

    async def create_notebook(self, name: str = "Untitled") -> int:
        cur = await self._conn.execute(
            "INSERT INTO notebooks (name) VALUES (?)", (name,)
        )
        await self._conn.commit()
        return cur.lastrowid

    async def get_all_notebooks(self) -> list[Notebook]:
        async with self._conn.execute(
            "SELECT id, name, updated_at FROM notebooks ORDER BY updated_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [
            Notebook(id=r["id"], name=r["name"], updated_at=r["updated_at"])
            for r in rows
        ]

    async def get_notebook(self, nb_id: int) -> Notebook | None:
        async with self._conn.execute(
            "SELECT id, name, updated_at FROM notebooks WHERE id = ?", (nb_id,)
        ) as cur:
            r = await cur.fetchone()
        if not r:
            return None
        return Notebook(id=r["id"], name=r["name"], updated_at=r["updated_at"])

    async def get_latest_notebook_id(self) -> int | None:
        async with self._conn.execute(
            "SELECT id FROM notebooks ORDER BY updated_at DESC LIMIT 1"
        ) as cur:
            r = await cur.fetchone()
        return r["id"] if r else None

    async def rename_notebook(self, nb_id: int, name: str) -> None:
        await self._conn.execute(
            "UPDATE notebooks SET name = ?, updated_at = datetime('now') WHERE id = ?",
            (name, nb_id),
        )
        await self._conn.commit()

    async def delete_notebook(self, nb_id: int) -> None:
        await self._conn.execute("DELETE FROM cells WHERE notebook_id = ?", (nb_id,))
        await self._conn.execute("DELETE FROM notebooks WHERE id = ?", (nb_id,))
        await self._conn.commit()

    async def duplicate_notebook(self, nb_id: int) -> int:
        nb = await self.get_notebook(nb_id)
        name = f"{nb.name} (copy)" if nb else "Untitled (copy)"
        new_id = await self.create_notebook(name)
        cells = await self.get_all_cells(nb_id)
        for i, cell in enumerate(cells):
            await self._conn.execute(
                "INSERT INTO cells (notebook_id, order_index, cell_type, input, output, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (new_id, i, cell.cell_type, cell.input, cell.output, cell.status),
            )
        await self._conn.commit()
        return new_id

    async def touch_notebook(self, nb_id: int) -> None:
        await self._conn.execute(
            "UPDATE notebooks SET updated_at = datetime('now') WHERE id = ?", (nb_id,)
        )
        await self._conn.commit()

    # ── cells ─────────────────────────────────────────────────────────────

    async def get_all_cells(self, notebook_id: int) -> list[Cell]:
        async with self._conn.execute(
            "SELECT id, notebook_id, cell_type, input, output, status FROM cells "
            "WHERE notebook_id = ? ORDER BY order_index",
            (notebook_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_cell(r) for r in rows]

    async def get_cell(self, cell_id: int) -> Cell | None:
        async with self._conn.execute(
            "SELECT id, notebook_id, cell_type, input, output, status FROM cells WHERE id = ?",
            (cell_id,),
        ) as cur:
            r = await cur.fetchone()
        return _row_to_cell(r) if r else None

    async def insert_cell(self, notebook_id: int, cell_type: str = "code") -> int:
        async with self._conn.execute(
            "SELECT COALESCE(MAX(order_index), -1) + 1 AS next_ord "
            "FROM cells WHERE notebook_id = ?",
            (notebook_id,),
        ) as cur:
            r = await cur.fetchone()
        cur = await self._conn.execute(
            "INSERT INTO cells (notebook_id, order_index, cell_type) VALUES (?, ?, ?)",
            (notebook_id, r["next_ord"], cell_type),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def update_cell(
        self, cell_id: int, input: str, output: str, status: str
    ) -> None:
        await self._conn.execute(
            "UPDATE cells SET input = ?, output = ?, status = ? WHERE id = ?",
            (input, output, status, cell_id),
        )
        await self._conn.commit()

    async def update_input(self, cell_id: int, input: str) -> None:
        await self._conn.execute(
            "UPDATE cells SET input = ? WHERE id = ?", (input, cell_id)
        )
        await self._conn.commit()

    async def delete_cell(self, cell_id: int) -> None:
        await self._conn.execute("DELETE FROM cells WHERE id = ?", (cell_id,))
        await self._conn.commit()

    async def get_cell_notebook_id(self, cell_id: int) -> int | None:
        async with self._conn.execute(
            "SELECT notebook_id FROM cells WHERE id = ?", (cell_id,)
        ) as cur:
            r = await cur.fetchone()
        return r["notebook_id"] if r else None

    async def get_next_cell_id(self, cell_id: int) -> int | None:
        async with self._conn.execute(
            "SELECT c2.id FROM cells c1 "
            "JOIN cells c2 ON c2.notebook_id = c1.notebook_id "
            "  AND c2.order_index > c1.order_index "
            "WHERE c1.id = ? ORDER BY c2.order_index LIMIT 1",
            (cell_id,),
        ) as cur:
            r = await cur.fetchone()
        return r["id"] if r else None

    async def insert_cell_at(
        self, notebook_id: int, reference_cell_id: int, position: str, cell_type: str = "code"
    ) -> int:
        """Insert a cell above or below a reference cell. position: 'above' or 'below'."""
        async with self._conn.execute(
            "SELECT order_index FROM cells WHERE id = ?", (reference_cell_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return await self.insert_cell(notebook_id, cell_type=cell_type)
        ref_order = row["order_index"]
        if position == "above":
            # Shift all cells at or above ref_order up by 1
            await self._conn.execute(
                "UPDATE cells SET order_index = order_index + 1 "
                "WHERE notebook_id = ? AND order_index >= ?",
                (notebook_id, ref_order),
            )
            new_order = ref_order
        else:
            # Shift all cells below ref_order up by 1
            await self._conn.execute(
                "UPDATE cells SET order_index = order_index + 1 "
                "WHERE notebook_id = ? AND order_index > ?",
                (notebook_id, ref_order),
            )
            new_order = ref_order + 1
        cur = await self._conn.execute(
            "INSERT INTO cells (notebook_id, order_index, cell_type) VALUES (?, ?, ?)",
            (notebook_id, new_order, cell_type),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def update_cell_type(self, cell_id: int, cell_type: str) -> None:
        """Change a cell's type (code/markdown)."""
        await self._conn.execute(
            "UPDATE cells SET cell_type = ? WHERE id = ?", (cell_type, cell_id)
        )
        await self._conn.commit()

    async def move_cell(self, cell_id: int, direction: str) -> None:
        """Swap cell with adjacent cell. direction: 'up' or 'down'."""
        async with self._conn.execute(
            "SELECT notebook_id, order_index FROM cells WHERE id = ?", (cell_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return
        nb_id, cur_order = row["notebook_id"], row["order_index"]

        op = "<" if direction == "up" else ">"
        sort = "DESC" if direction == "up" else "ASC"
        async with self._conn.execute(
            f"SELECT id, order_index FROM cells "
            f"WHERE notebook_id = ? AND order_index {op} ? ORDER BY order_index {sort} LIMIT 1",
            (nb_id, cur_order),
        ) as cur:
            adj = await cur.fetchone()
        if not adj:
            return

        await self._conn.execute(
            "UPDATE cells SET order_index = ? WHERE id = ?", (adj["order_index"], cell_id)
        )
        await self._conn.execute(
            "UPDATE cells SET order_index = ? WHERE id = ?", (cur_order, adj["id"])
        )
        await self._conn.commit()

    async def duplicate_cell(self, cell_id: int) -> int | None:
        """Duplicate a cell, inserting the copy right below it. Returns new cell ID."""
        cell = await self.get_cell(cell_id)
        if not cell:
            return None
        async with self._conn.execute(
            "SELECT order_index FROM cells WHERE id = ?", (cell_id,)
        ) as cur:
            row = await cur.fetchone()
        cur_order = row["order_index"]

        # Shift cells below to make room
        await self._conn.execute(
            "UPDATE cells SET order_index = order_index + 1 "
            "WHERE notebook_id = ? AND order_index > ?",
            (cell.notebook_id, cur_order),
        )
        cur = await self._conn.execute(
            "INSERT INTO cells (notebook_id, order_index, cell_type, input, output, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cell.notebook_id, cur_order + 1, cell.cell_type, cell.input, cell.output, cell.status),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def close(self) -> None:
        await self._conn.close()
