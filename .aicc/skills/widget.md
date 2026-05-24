---
name: widget
description: Add or edit a PyQt6 widget in this project
tools: [read_file, write_file, search_files]
---
You are working inside the aicc PyQt6 desktop app (this repository).

Key conventions:
- All widgets live in `ui/widgets/`. Inherit from the most specific Qt base class that fits.
- Signals use `pyqtSignal`; slots are plain methods, not decorated.
- Theming: always use `palette()` from `ui/theme.py` for colours — never hardcode hex. Use `ACCENT` for interactive highlights.
- `apply_appearance()` must be a no-op-safe method on every widget so the app can re-theme at runtime without restart.
- No Qt layouts on floating/positioned widgets — use `move()` and `setFixedSize()` directly.
- Read `ui/theme.py` before touching any style strings.
