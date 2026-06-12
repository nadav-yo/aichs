from services.file_editor_refs import (
    MAX_EDITOR_REF_TEXT_CHARS,
    editor_ref_paths,
    editor_ref_payload,
    editor_ref_text,
    parse_editor_refs,
)


def test_editor_ref_payload_round_trips_and_cleans():
    payload = editor_ref_payload([
        {
            "path": r"src\main.py",
            "start_line": 3,
            "end_line": 5,
            "text": "  print('hi')  ",
        },
        {"path": "src/main.py", "start_line": 3, "end_line": 5, "text": "dupe"},
    ])

    refs = parse_editor_refs(payload)

    assert refs == [{
        "path": "src/main.py",
        "start_line": 3,
        "end_line": 5,
        "text": "print('hi')",
    }]
    assert editor_ref_text(refs) == "@src/main.py:3-5"
    assert editor_ref_paths(refs) == ["src/main.py"]


def test_editor_ref_payload_ignores_invalid_json():
    assert parse_editor_refs(b"not json") == []


def test_editor_ref_payload_rejects_wrong_shapes():
    assert parse_editor_refs(b'{"kind":"wrong","refs":[]}') == []
    assert parse_editor_refs(b'[]') == []
    assert parse_editor_refs(editor_ref_payload([None, {"path": "   "}])) == []


def test_editor_ref_text_quotes_spaces_and_formats_single_line():
    refs = [
        {"path": "docs/read me.md", "start_line": 2, "end_line": 2},
        {"path": "README.md", "start_line": 0, "end_line": 0},
    ]

    assert editor_ref_text(refs) == '@"docs/read me.md":2 @README.md:1'


def test_editor_refs_normalize_lines_and_truncate_text():
    text = "\u2029" + ("x" * (MAX_EDITOR_REF_TEXT_CHARS + 10))
    payload = editor_ref_payload([
        {"path": "note.md", "start_line": "nope", "end_line": -4, "text": text},
    ])

    parsed = parse_editor_refs(payload)

    assert parsed[0]["start_line"] == 1
    assert parsed[0]["end_line"] == 1
    assert parsed[0]["text"] == "x" * MAX_EDITOR_REF_TEXT_CHARS
