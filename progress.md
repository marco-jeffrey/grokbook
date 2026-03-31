# nb-staroid QoL Features Progress

## Feature 1: Rich Output (Images, HTML, Plots)
- [x] kernel.py — capture MIME bundles from display_data/execute_result
- [x] views.py — _render_output() helper for different MIME types
- [x] Commit

## Feature 2: Cell Operations (Delete, Move, Duplicate)
- [ ] db.py — move_cell, duplicate_cell
- [ ] handlers.py — delete/move/duplicate endpoints
- [ ] api.py — REST endpoints
- [ ] mcp_server.py — tools
- [ ] views.py — cell toolbar
- [ ] Commit

## Feature 3: Keyboard Shortcuts (Jupyter-style)
- [ ] db.py — insert_cell_at, update_cell_type
- [ ] handlers.py — new-above/below, convert endpoints
- [ ] views.py — data-cell-container attrs, selected-cell visual
- [ ] app.js — command/edit mode state machine
- [ ] Commit

## Feature 4: Streaming Kernel Output
- [ ] kernel.py — execute_streaming() async generator
- [ ] views.py — output container id
- [ ] handlers.py — streaming execute with w.patch()
- [ ] Commit

## Feature 5: Execution Counter
- [ ] state.py — execution_count field
- [ ] db.py — schema migration, column
- [ ] kernel.py — return execution_count
- [ ] handlers.py + api.py — thread execution_count
- [ ] views.py — In[N]/Out[N] labels
- [ ] Commit

## Feature 6: Cell Output Collapse/Expand
- [ ] views.py — collapse logic for large outputs
- [ ] Commit

## Feature 7: Kernel Variables Inspector
- [ ] kernel.py — get_variables()
- [ ] handlers.py — /kernel/variables endpoint
- [ ] views.py — variables panel
- [ ] Commit

## Feature 8: Auto-reconnect SSE
- [ ] views.py — retry config
- [ ] handlers.py — heartbeat
- [ ] Commit

## Feature 9: OpenAI-Compatible Endpoint
- [ ] app/openai.py — tool definitions, dispatch, /chat/completions
- [ ] main.py — mount /v1
- [ ] Commit

## Feature 10: Dark/Light Theme Toggle
- [ ] views.py — theme signal, dark: classes, toggle button
- [ ] app.js — theme restoration
- [ ] Commit
