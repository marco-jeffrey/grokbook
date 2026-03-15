# Stario + Datastar Rules

This project uses **stario** (local editable package at `stario/`) and **Datastar** for reactive hypermedia UIs. These rules capture verified behavior from source code and real usage.

---

## Environment

- **Python 3.14 required** — stario's writer imports `from compression import zstd` which is a Python 3.14 stdlib module. 3.13 and below will fail at import time.
- **Run with**: `uv run python main.py`
- **Tooling**: uv only. No npm, no build step. Tailwind via CDN for dev.

---

## Application Bootstrap

The bootstrap function **must** use `@asynccontextmanager`. A plain `async def` that yields will fail with "async_generator does not support async context manager protocol".

```python
from contextlib import asynccontextmanager
from stario import RichTracer, Stario
from stario.http.server import Server
from stario.http.writer import CompressionConfig
from stario.telemetry.core import Span

@asynccontextmanager                        # REQUIRED — not optional
async def bootstrap(app: Stario, span: Span):
    db = await Database.connect("app.db")
    app.mount("/", my_router(db))
    try:
        yield
    finally:
        await db.close()

if __name__ == "__main__":
    server = Server(
        bootstrap,
        RichTracer(),
        host="0.0.0.0",
        port=8080,                          # avoid low ports — Chrome blocks e.g. 6666
        compression=CompressionConfig(zstd_level=-1),  # disable zstd → brotli preferred
    )
    asyncio.run(server.run())
```

---

## Routing

### Rules
- Only two path forms are supported: **exact** (`/users`) and **wildcard** (`/users/*`)
- **No named path params** — `{id}` style does NOT exist
- Wildcard tail is available as `c.req.tail` (the path segment after the prefix, with leading `/` stripped)
- Trailing slashes are auto-redirected (301) by the router
- Register `router.use(middleware)` **before** any routes — calling it after routes raises immediately

```python
from stario.http import Router
from stario.http.types import Context, Writer

router = Router()

async def users(c: Context, w: Writer) -> None:
    # GET /users/123 → c.req.tail == "123"
    # GET /users/new → c.req.tail == "new"
    user_id = c.req.tail

router.get("/users/*", users)
router.post("/users/*", users)
```

### Mounting sub-routers

```python
app = Stario()
api = Router()
api.get("/health", health_handler)

app.mount("/api", api)   # → GET /api/health
```

---

## Handler Signature

Always `async def handler(c: Context, w: Writer) -> None`. No return value.

```python
from stario.http.types import Context, Writer

async def index(c: Context, w: Writer) -> None:
    w.html(page())          # one-shot HTML response
```

---

## Writer — Response Methods

### One-shot responses (call once per request)

```python
w.html(element)             # HtmlElement — do NOT call render() first, writer does it internally
w.json({"key": "value"})    # dict → JSON
w.text("plain text", 404)   # optional status code (default 200)
w.redirect("/login", 302)   # redirect
w.empty(204)                # no body
```

**Critical**: `w.html()` accepts an `HtmlElement` (Tag object), calls `render()` internally. Passing a string (pre-rendered HTML) causes double-escaping. Pass the Tag object directly.

```python
# CORRECT
w.html(Html(Head(...), Body(...)))

# WRONG — double-rendered
w.html(render(Html(...)))
```

### SSE streaming (Datastar DOM patching)

These methods set SSE headers automatically on first call. No need to call `sse_start()` or set headers manually.

```python
w.patch(element, selector="#target")          # replace element matching selector
w.patch(element, selector="#list", mode="append")  # append inside selector
w.sync({"count": 42, "loading": False})       # update client signals
w.navigate("/success")                        # redirect via SSE
w.execute("alert('done')")                    # run JS on client
w.remove("#old-item")                         # remove element from DOM
```

`patch()` modes: `"outer"` (default), `"inner"`, `"prepend"`, `"append"`, `"before"`, `"after"`

**Do NOT use** `w.sse_start()` + `w.write(sse.patch(...))` for normal SSE patching — use `w.patch()` directly.

### Cookies

```python
w.cookie("session", token, httponly=True, secure=True, max_age=86400)
w.delete_cookie("session")
```

---

## HTML Module

### Imports

```python
from stario.html import (
    Html, Head, Body, Div, Span, P, H1, H2, A,
    Button, Input, Textarea, Form, Pre, Script,
    Meta, Link, Title, SafeString, Tag, render
)
```

### Tag call signature

Arguments to a tag can be mixed in any order: **dicts are attributes**, **everything else is content**. Multiple dicts merge.

```python
Div()                                    # <div></div>
Div("hello")                             # <div>hello</div>
Div({"class": "box"}, "hello")           # <div class="box">hello</div>
Div({"class": "a"}, {"id": "b"}, P("c"))# <div class="a" id="b"><p>c</p></div>
Div(*[P(i) for i in items])              # spread list as children
```

### Attribute value types

```python
{"class": "btn primary"}                 # str → normal attribute
{"tabindex": 0}                          # int → stringified
{"disabled": True}                       # True → boolean attribute (name only)
{"disabled": False}                      # False → attribute omitted entirely
{"class": ["btn", "primary", "lg"]}      # list → joined with spaces
{"data": {"user-id": "42"}}              # nested dict → data-user-id="42"
{"aria": {"label": "Close"}}             # nested dict → aria-label="Close"
{"style": {"color": "red"}}              # style dict → inline CSS string
```

### SafeString

All text content is HTML-escaped by default. Use `SafeString` only for trusted markup you control — never for user input.

```python
Div("<b>bold</b>")                       # <div>&lt;b&gt;...    ← escaped
Div(SafeString("<b>bold</b>"))           # <div><b>bold</b>     ← raw
```

### render()

`render()` converts a Tag to an HTML string. Only call it when you need a string — not before passing to `w.html()`.

```python
from stario.html import render
html_str = render(Div({"class": "box"}, "hello"))
```

---

## Datastar Integration

### Imports

```python
from stario.datastar import DatastarScript, data, at
from stario.datastar.signals import get_signals   # for reading POST signal body
```

### Including Datastar in page

```python
Head(
    DatastarScript(),   # <script type="module" src="...cdn...">
)
```

### Signals — reactive state (`data.signals`)

Declare signals on a container element. All child elements can reference them.

```python
# Multiple signals — values are Python values, JSON-encoded
Div(data.signals({"count": 0, "name": "", "loading": False}))
# → <div data-signals='{"count":0,"name":"","loading":false}'></div>

# Single signal — expression form (value is a JS/Datastar expression)
Div(data.signals("count", "0"))
# → <div data-signals:count="0"></div>

# Only set if signal doesn't already exist (SSR hydration)
Div(data.signals({"count": 0}, ifmissing=True))
# → <div data-signals__ifmissing='{"count":0}'></div>

# Accepts dataclass instances directly
Div(data.signals(AppState()))
```

### Two-way binding (`data.bind`)

Binds an input's value to a signal (two-way sync).

```python
Input({"type": "text"}, data.bind("name"))
# → <input type="text" data-bind="name"/>

Textarea(data.bind("code"))
# → <textarea data-bind="code"></textarea>
```

### Display signal value (`data.text`)

Renders signal value as text content (reactive, updates on signal change).

```python
Span(data.text("count"))
# → <span data-text="count"></span>
```

### Conditional visibility (`data.show`)

```python
Div(data.show("count > 0"), "You have items")
# → <div data-show="count > 0">You have items</div>
```

### Event handlers (`data.on`)

`data.on(event, expression)` produces `data-on:{event}` attributes. The `expression` is either a JS expression or an `at.*` fetch action.

```python
# Inline JS expression
Button(data.on("click", "count++"), "+")
# → <button data-on:click="count++">+</button>

# POST to server on click (sends all signals as JSON body)
Button(data.on("click", at.post("/increment")), "Increment")
# → <button data-on:click="@post('/increment')">Increment</button>

# POST with only specific signals (use include= to filter)
Button(data.on("click", at.post("/save", include=["name", "email"])), "Save")
# → <button data-on:click="@post('/save', {filterSignals: {include: '/^(name|email)$/'}})">Save</button>

# With modifiers
Button(data.on("click", at.post("/save"), once=True, prevent=True), "Save once")
# → <button data-on:click__once__prevent="@post('/save')">Save once</button>

# Debounced keydown
Input(data.on("keydown", at.post("/search"), debounce={"ms": 300}))

# Window-level event
Div(data.on("resize", "updateLayout()", window=True))
```

Available modifiers for `data.on`: `once`, `passive`, `capture`, `window`, `outside`, `prevent`, `stop`, `delay`, `debounce`, `throttle`, `viewtransition`

### Action builders (`at`)

`at.get()`, `at.post()`, etc. return Datastar action strings (not HTML attributes). Always use inside `data.on()` or as a raw attribute value.

```python
at.get("/api/data")                          # "@get('/api/data')"
at.post("/api/submit")                       # "@post('/api/submit')"
at.post("/save", include=["name", "email"])  # filter which signals to send
at.post("/save", exclude=["password"])
at.get("/search", {"q": "hello"})            # with query params
at.post("/upload", content_type="form")      # form encoding instead of JSON
```

### Loading indicator (`data.indicator`)

Links a signal name to track in-flight requests. Signal is `true` while request is pending.

```python
Button(
    data.on("click", at.post("/run")),
    data.indicator("loading"),
    "Run"
)
Div(data.show("loading"), "Running...")
```

### Computed signals & effects

```python
Div(data.computed("double", "count * 2"))    # derived reactive value
Div(data.effect("document.title = name"))    # side effect on signal change
```

### Dynamic class toggling

```python
Button(data.class_("active", "isActive"), "Toggle")
# → <button data-class:active="isActive"></button>
```

### Dynamic attributes

```python
Img(data.attr("src", "imageUrl"))
# → <img data-attr:src="imageUrl"/>
```

### Reading signals on the server (POST handlers)

Datastar sends signals as a flat JSON object in the POST body (not form-encoded).

```python
from stario.datastar.signals import get_signals

async def handler(c: Context, w: Writer) -> None:
    signals = await get_signals(c.req)
    # signals is a plain dict: {"cell_1": "print('hello')", "name": "foo", ...}
    code = signals.get("cell_1", "")
```

For GET requests, signals are in the `datastar` query param as JSON.

---

## Complete Reactive Pattern

This is the canonical pattern for a Datastar-powered interactive component.

### View (Python)

```python
from stario.datastar import DatastarScript, data, at
from stario.html import Html, Head, Body, Div, Button, Textarea, Pre, SafeString, Script
import html as _e

def cell_view(cell_id: int, code: str, output: str):
    sig = f"cell_{cell_id}"
    return Div(
        {"class": "mb-6"},
        data.signals({sig: code}),          # declare signal with server value
        Textarea(
            data.bind(sig),                  # two-way bind textarea to signal
            {"class": "w-full font-mono"},
        ),
        Button(
            data.on("click", at.post(f"/cells/{cell_id}", include=[sig])),
            {"class": "px-4 py-2 bg-indigo-600 text-white rounded"},
            "▶ Run",
        ),
        Pre(SafeString(_e.escape(output))) if output else SafeString(""),
    )

def page(cells):
    return Html(
        Head(
            Script({"src": "https://cdn.tailwindcss.com"}),
            DatastarScript(),
        ),
        Body(
            Div({"id": "notebook"}, *[cell_view(c.id, c.code, c.output) for c in cells])
        ),
    )
```

### Handler (Python)

```python
from stario.datastar.signals import get_signals
from stario.http import Router
from stario.http.types import Context, Writer

def app_router(db) -> Router:
    router = Router()

    async def _patch_notebook(w: Writer) -> None:
        cells = await db.get_all_cells()
        # patch() sets SSE headers automatically — no sse_start() needed
        w.patch(element=notebook(cells), selector="#notebook")

    async def index(c: Context, w: Writer) -> None:
        cells = await db.get_all_cells()
        w.html(page(cells))               # pass Tag object, NOT render(page(cells))

    async def cell_handler(c: Context, w: Writer) -> None:
        tail = c.req.tail                  # e.g. "42" from POST /cells/42

        if tail == "new":
            await db.insert_cell()
            await _patch_notebook(w)
            return

        try:
            cell_id = int(tail)
        except ValueError:
            w.text("Not Found", 404)
            return

        signals = await get_signals(c.req)
        code = signals.get(f"cell_{cell_id}", "")
        output = await run_code(code)
        await db.save(cell_id, code, output)
        await _patch_notebook(w)

    router.get("/", index)
    router.post("/cells/*", cell_handler)  # wildcard → c.req.tail has the rest
    return router
```

---

## SSE Internals (advanced)

Use `w.patch()` / `w.sync()` for all normal Datastar streaming — they handle headers, encoding, and chunking internally.

Only use raw `sse.*` formatters if you need direct control:

```python
from stario.datastar import sse

w.write(sse.patch(element, selector="#foo", mode="append"))
w.write(sse.signals({"count": 5}))
w.write(sse.remove("#old"))
w.write(sse.script("alert('hi')"))
w.write(sse.redirect("/done"))
```

---

## Compression

```python
from stario.http.writer import CompressionConfig

# Default: zstd > brotli > gzip priority
CompressionConfig()

# Disable zstd (required for clients without Python 3.14 zstd support isn't the issue,
# but useful when zstd causes chunked SSE issues)
CompressionConfig(zstd_level=-1)           # brotli preferred, gzip fallback

# Only gzip
CompressionConfig(zstd_level=-1, brotli_level=-1)

# Custom levels
CompressionConfig(zstd_level=3, brotli_level=4, gzip_level=6, min_size=512)
```

---

## Common Mistakes

| Mistake | Correct |
|---|---|
| `w.html(render(page()))` | `w.html(page())` |
| `await w.sse_start()` + `w.write(sse.patch(...))` | `w.patch(element, selector="#id")` |
| `async def bootstrap(app, span): yield` | `@asynccontextmanager async def bootstrap(...)` |
| `router.get("/cells/{id}", ...)` | `router.get("/cells/*", ...)` + `c.req.tail` |
| `"data-on-click": at.post(...)` | `data.on("click", at.post(...))` |
| Reading form body with `parse_qs` | `await get_signals(c.req)` |
| `data-on-click` (hyphen) | `data-on:click` (colon) — generated by `data.on()` |
| `data.show("count > 0")` | `data.show("$count > 0")` — signals need `$` prefix in ALL expressions |
| `data.text("name")` | `data.text("$name")` — same for text, effect, computed |
