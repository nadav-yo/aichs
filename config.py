import os
from pathlib import Path

AICHS_HOME_ENV  = "AICHS_HOME"


def resolve_aichs_home() -> Path:
    override = os.environ.get(AICHS_HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".aichs"


AICHS_HOME      = resolve_aichs_home()
CONV_DIR        = AICHS_HOME / "conversations"
SETTINGS_PATH   = AICHS_HOME / "settings.json"
AVATARS_DIR     = AICHS_HOME / "avatars"
WORKSPACES_PATH = AICHS_HOME / "workspaces.json"
IGNORED  = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea"}

# Keep the desktop app responsive on large repos and noisy commands.
MAX_FILE_PREVIEW_BYTES = 512 * 1024
MAX_TOOL_READ_BYTES = 64 * 1024
DEFAULT_READ_FILE_LINES = 200
MAX_READ_FILE_LINES = 1000
MAX_TOOL_OUTPUT_CHARS = 64 * 1024
MAX_TOOL_OUTPUT_LINES = 2048
MAX_TERMINAL_BLOCKS = 500
MAX_INLINE_IMAGE_DIMENSION = 1280
MAX_TREE_ENTRIES_PER_DIR = 80

# Left activity panel (rail + stack: chats, files, git).
ACTIVITY_RAIL_WIDTH = 64
MIN_ACTIVITY_WIDTH = 304
DEFAULT_ACTIVITY_WIDTH = 424
MAX_ACTIVITY_WIDTH = 640
ACTIVITY_STACK_MIN_WIDTH = MIN_ACTIVITY_WIDTH - ACTIVITY_RAIL_WIDTH

from services.model_registry import MODELS, MODEL_PROVIDER  # noqa: E402, F401

SYSTEM_PROMPT = """You are a precise senior coding agent. Solve engineering tasks with minimal fluff.
Inspect code before claims. Prefer small, correct changes that follow existing patterns.
Implement when asked, verify when possible, and report only what changed, what was tested, and any blockers.
Call tools only by their exact advertised names. Never wrap tool calls in script runners or provider-specific namespaces.
For broad tasks, map with list_files/search_files, then read targeted slices (read_file offset/limit after a line hit).
Truncated tool output is incomplete; fetch the slice you need or search again—never invent unseen content.
Be terse, technical, and direct. Ask questions only when proceeding would be unsafe or impossible.
Never answer with generic readiness or "awaiting task" messages; answer the user's request or ask one concrete clarifying question."""
