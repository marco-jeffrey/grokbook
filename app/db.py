import aiosqlite

from app.state import Cell, Notebook

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
    input       TEXT NOT NULL DEFAULT '',
    output      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT ''
)
"""


class Database:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    @classmethod
    async def connect(cls, path: str) -> "Database":
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute(_CREATE_NOTEBOOKS)

        # Check if cells table exists with old schema
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cells'"
        ) as cur:
            exists = await cur.fetchone()

        if not exists:
            await conn.execute(_CREATE_CELLS)
        else:
            # Migrate: add missing columns to existing table
            for col, dflt in [
                ("notebook_id", "0"),
                ("order_index", "0"),
                ("status", "''"),
            ]:
                try:
                    await conn.execute(
                        f"ALTER TABLE cells ADD COLUMN {col} DEFAULT {dflt}"
                    )
                except Exception:
                    pass

            # Migrate orphan cells to a default notebook
            async with conn.execute(
                "SELECT COUNT(*) AS n FROM cells WHERE notebook_id = 0 OR notebook_id IS NULL"
            ) as cur:
                r = await cur.fetchone()
            if r["n"] > 0:
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

    async def touch_notebook(self, nb_id: int) -> None:
        await self._conn.execute(
            "UPDATE notebooks SET updated_at = datetime('now') WHERE id = ?", (nb_id,)
        )
        await self._conn.commit()

    # ── cells ─────────────────────────────────────────────────────────────

    async def get_all_cells(self, notebook_id: int) -> list[Cell]:
        async with self._conn.execute(
            "SELECT id, notebook_id, input, output, status FROM cells "
            "WHERE notebook_id = ? ORDER BY order_index",
            (notebook_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Cell(
                id=r["id"],
                notebook_id=r["notebook_id"],
                input=r["input"],
                output=r["output"],
                status=r["status"],
            )
            for r in rows
        ]

    async def insert_cell(self, notebook_id: int) -> int:
        async with self._conn.execute(
            "SELECT COALESCE(MAX(order_index), -1) + 1 AS next_ord "
            "FROM cells WHERE notebook_id = ?",
            (notebook_id,),
        ) as cur:
            r = await cur.fetchone()
        cur = await self._conn.execute(
            "INSERT INTO cells (notebook_id, order_index) VALUES (?, ?)",
            (notebook_id, r["next_ord"]),
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

    async def close(self) -> None:
        await self._conn.close()
