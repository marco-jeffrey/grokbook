"""First-run welcome notebook — created when the database is empty."""

from grokbook.db import Database

_CELLS = [
    (
        "markdown",
        """\
# Welcome to Grokbook

Grokbook is an interactive notebook for learning computer science. It works just like Jupyter — write code and markdown in cells, run them, and see results instantly.

**How it works:**
- **Code cells** run Python in a persistent kernel (variables carry over between cells)
- **Markdown cells** render rich text for explanations and notes
- Each notebook has its own kernel — restart it anytime for a clean slate\
""",
    ),
    (
        "markdown",
        """\
## Running Cells

Click on a code cell and press **Shift+Enter** to execute it. The output appears below.

Try it with the cell below!\
""",
    ),
    (
        "code",
        """\
message = "Hello from Grokbook!"
print(message)
print(f"2 + 2 = {2 + 2}")\
""",
    ),
    (
        "markdown",
        """\
## Keyboard Shortcuts

Grokbook has two modes, like Vim:

**Command mode** (press `Escape` to enter):

| Key | Action |
|-----|--------|
| `j` / `k` | Navigate between cells |
| `a` / `b` | Insert cell above / below |
| `Enter` | Edit selected cell |
| `m` / `y` | Convert to markdown / code |
| `dd` | Delete cell |

**Edit mode** (press `Enter` or click a cell):

| Key | Action |
|-----|--------|
| `Shift+Enter` | Run cell, move to next |
| `Ctrl+Enter` | Run cell, stay in place |
| `Escape` | Back to command mode |
| `Tab` | Indent |
| `Shift+Tab` | Dedent |\
""",
    ),
    (
        "markdown",
        """\
## Variables Persist Between Cells

Just like Jupyter, when you define a variable in one cell, it's available in all cells below. Run these two cells in order:\
""",
    ),
    (
        "code",
        """\
# Run this first
import math

radius = 5
area = math.pi * radius ** 2\
""",
    ),
    (
        "code",
        """\
# Then run this — it uses `radius` and `area` from above
print(f"A circle with radius {radius} has area {area:.2f}")
print(f"Its circumference is {2 * math.pi * radius:.2f}")\
""",
    ),
    (
        "markdown",
        """\
## What Next?

- Create a new notebook from the sidebar
- Use the **MCP integration** to have an AI tutor help you learn
- Import existing `.ipynb` notebooks from Jupyter
- Click **Vars** in the top bar to inspect kernel variables

Happy learning!\
""",
    ),
]


async def ensure_welcome_notebook(db: Database) -> None:
    """Create a welcome notebook if the database has no notebooks."""
    notebooks = await db.get_all_notebooks()
    if notebooks:
        return

    nb_id = await db.create_notebook(name="Welcome to Grokbook")

    for cell_type, content in _CELLS:
        cell_id = await db.insert_cell(nb_id, cell_type=cell_type)
        await db.update_input(cell_id, content)
