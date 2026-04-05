from dataclasses import dataclass


@dataclass
class Project:
    id: int
    name: str
    order_index: int = 0


@dataclass
class Notebook:
    id: int
    name: str
    project_id: int = 1
    order_index: int = 0
    updated_at: str = ""
    kernel_env: str | None = None


@dataclass
class Cell:
    id: int
    notebook_id: int
    cell_type: str  # "code" or "markdown"
    input: str
    output: str = ""
    status: str = ""  # "", "ok", "error"
    execution_count: int = 0
    execution_time: float = 0.0
