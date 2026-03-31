from dataclasses import dataclass


@dataclass
class Notebook:
    id: int
    name: str
    updated_at: str


@dataclass
class Cell:
    id: int
    notebook_id: int
    cell_type: str  # "code" or "markdown"
    input: str
    output: str = ""
    status: str = ""  # "", "ok", "error"
    execution_count: int = 0
