"""First-run welcome notebook — created when the database is empty."""

from grokbook.db import Database

_CELLS = [
    (
        "markdown",
        """\
# Welcome to Grokbook

Grokbook is an interactive notebook for learning computer science — like Jupyter, but with a built-in **MCP server** that lets AI assistants create and manage notebooks for you.

**The idea:** connect Claude (or any MCP-compatible LLM) and just say what you want to learn. The AI will generate full notebooks with explanations, runnable code, and exercises — then help you when you get stuck.

- *"Teach me how recursion works"* — get a complete lesson with examples you can run
- *"I don't understand why my sort is wrong"* — the AI reads your notebook and guides you to the fix
- *"Give me 5 practice problems on linked lists"* — runnable exercises with starter code

Notebooks work like Jupyter: **code cells** run Python in a persistent kernel (variables carry over), **markdown cells** render rich text. Each notebook gets its own kernel.\
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
## Connecting an AI Tutor

Paste this into **Claude Desktop** or **Claude Code** settings to connect:

```json
{
  "mcpServers": {
    "grokbook": {
      "command": "grokbook",
      "args": ["mcp", "--allow-code-execution"]
    }
  }
}
```

Once connected, just tell the AI what you want to learn. It will create notebooks, write and run code, and teach you interactively.\
""",
    ),
    (
        "markdown",
        """\
## What Next?

- **Create a notebook** from the sidebar
- **Import** existing `.ipynb` notebooks from Jupyter
- **Inspect variables** with the **Vars** button in the top bar
- **Toggle Vim mode** from the settings panel (gear icon) — `jk` maps to Escape

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
