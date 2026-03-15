import aiosqlite

from app.state import Cell

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cells (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    input TEXT NOT NULL DEFAULT '',
    output TEXT NOT NULL DEFAULT ''
)
"""


class Database:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    @classmethod
    async def connect(cls, path: str) -> "Database":
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute(_CREATE_TABLE)
        await conn.commit()
        return cls(conn)

    async def get_all_cells(self) -> list[Cell]:
        async with self._conn.execute(
            "SELECT id, input, output FROM cells ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
        return [Cell(id=r["id"], input=r["input"], output=r["output"]) for r in rows]

    async def insert_cell(self) -> None:
        await self._conn.execute("INSERT INTO cells DEFAULT VALUES")
        await self._conn.commit()

    async def update_cell(self, cell_id: int, input: str, output: str) -> None:
        await self._conn.execute(
            "UPDATE cells SET input = ?, output = ? WHERE id = ?",
            (input, output, cell_id),
        )
        await self._conn.commit()

    async def close(self) -> None:
        await self._conn.close()
