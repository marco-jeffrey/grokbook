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
    input: str
    output: str = ""
    status: str = ""  # "", "ok", "error"
