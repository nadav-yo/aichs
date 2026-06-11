from services.file_refs import files_for_refs, message_file_refs


def test_message_file_refs_collects_visible_and_hidden_refs():
    refs = message_file_refs(
        'read @"src/main file.py" and @tests/test_app.py.',
        ["hidden.py"],
    )

    assert refs == ["src/main file.py", "tests/test_app.py", "hidden.py"]


def test_files_for_refs_reads_workspace_files_once(workspace):
    target = workspace / "src" / "main.py"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"print('ok')\n")

    files = files_for_refs(
        str(workspace),
        ["src/main.py", "src\\main.py", "../outside.py", "missing.py"],
    )

    assert files == [{
        "path": "src/main.py",
        "content": "print('ok')\n",
        "truncated": False,
        "size": len(b"print('ok')\n"),
    }]
