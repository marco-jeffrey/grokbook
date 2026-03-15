from dataclasses import dataclass


@dataclass
class Cell:
    id: int
    input: str
    output: str = ""
