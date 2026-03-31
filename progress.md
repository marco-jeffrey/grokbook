# nb-staroid QoL Features Progress

## Feature 1: Rich Output (Images, HTML, Plots)
- [x] kernel.py — capture MIME bundles from display_data/execute_result
- [x] views.py — _render_output() helper for different MIME types
- [x] Commit

## Feature 2: Cell Operations (Delete, Move, Duplicate)
- [x] db.py — move_cell, duplicate_cell
- [x] handlers.py — delete/move/duplicate endpoints
- [x] api.py — REST endpoints
- [x] mcp_server.py — tools
- [x] views.py — cell toolbar
- [x] Commit

## Feature 3: Keyboard Shortcuts (Jupyter-style)
- [x] db.py — insert_cell_at, update_cell_type
- [x] handlers.py — new-above/below, convert endpoints
- [x] views.py — data-cell-container attrs, selected-cell visual
- [x] app.js — command/edit mode state machine
- [x] Commit

## Feature 4: Streaming Kernel Output
- [x] kernel.py — execute_streaming() async generator
- [x] views.py — output container id
- [x] handlers.py — streaming execute with w.patch()
- [x] Commit

## Feature 5: Execution Counter
- [x] state.py — execution_count field
- [x] db.py — schema migration, column
- [x] kernel.py — return execution_count
- [x] handlers.py + api.py — thread execution_count
- [x] views.py — In[N]/Out[N] labels
- [x] Commit

## Feature 6: Cell Output Collapse/Expand
- [x] views.py — collapse logic for large outputs
- [x] Commit

## Feature 7: Kernel Variables Inspector
- [x] kernel.py — get_variables()
- [x] handlers.py — /kernel/variables endpoint
- [x] views.py — variables panel + JS polling
- [x] Commit

## Feature 8: Auto-reconnect SSE
- [x] views.py — retry config (unlimited retries, 2s interval)
- [x] handlers.py — heartbeat every 15s
- [x] Commit

## Feature 9: OpenAI-Compatible Endpoint
- [x] app/openai.py — tool definitions, dispatch, /chat/completions
- [x] main.py — mount /v1
- [x] Commit

## Feature 10: Dark/Light Theme Toggle
- [ ] views.py — theme signal, dark: classes, toggle button
- [ ] app.js — theme restoration
- [ ] Commit
