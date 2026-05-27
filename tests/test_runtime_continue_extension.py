import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_runtime_continue_module():
    path = Path(__file__).parents[1] / ".aichs" / "extensions" / "runtime_continue.py"
    spec = importlib.util.spec_from_file_location("aichs_test_runtime_continue", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_auto_continue_marker_requests_resume_once():
    module = _load_runtime_continue_module()
    ctx = SimpleNamespace(history=[{"role": "user", "content": "work [auto-continue]"}])

    directive = module.auto_continue_when_marked(ctx)

    assert directive["action"] == "compact_and_resume"
    assert directive["resume_prompt"] == module._AUTO_RESUME_PROMPT


def test_auto_continue_marker_stops_after_resume_prompt():
    module = _load_runtime_continue_module()
    ctx = SimpleNamespace(history=[
        {"role": "user", "content": "work [auto-continue]"},
        {
            "role": "user",
            "content": module._AUTO_RESUME_PROMPT,
            "synthetic": "extension_resume",
        },
    ])

    assert module.auto_continue_when_marked(ctx) is None
