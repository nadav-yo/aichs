from types import SimpleNamespace
from unittest.mock import patch

import services.commit_message as cm


def test_staged_commit_context_includes_names_stats_and_diff(monkeypatch):
    monkeypatch.setattr(
        cm,
        "staged_commit_parts",
        lambda repo_path: (
            "M\tsrc/main.py",
            " src/main.py | 2 +-",
            "@@\n-print('hi')\n+print('message')\n",
        ),
    )

    context = cm.staged_commit_context("repo")

    assert "STAGED FILES:" in context
    assert "M\tsrc/main.py" in context
    assert "STAGED STATS:" in context
    assert "src/main.py" in context
    assert "STAGED DIFF:" in context
    assert "+print('message')" in context


def test_staged_commit_context_truncates_large_diff(monkeypatch):
    monkeypatch.setattr(
        cm,
        "staged_commit_parts",
        lambda repo_path: (
            "M\tsrc/main.py",
            " src/main.py | 100 +++++",
            "\n".join(f"+print({i})" for i in range(100)),
        ),
    )

    context = cm.staged_commit_context("repo", max_diff_chars=80)

    assert "[diff truncated]" in context


def test_staged_commit_context_returns_empty_for_no_staged_parts(monkeypatch):
    monkeypatch.setattr(cm, "staged_commit_parts", lambda repo_path: ("", "", ""))

    assert cm.staged_commit_context("repo") == ""


def test_build_commit_message_prompt_includes_optional_guidance():
    prompt = cm.build_commit_message_prompt("STAGED DIFF", "Mention ticket IDs.")

    assert "STAGED DIFF" in prompt
    assert "Mention ticket IDs." in prompt


def test_generate_commit_message_validates_model_and_staged_context(monkeypatch):
    monkeypatch.setattr(cm, "staged_commit_parts", lambda repo_path: ("", "", ""))

    for model, expected in [("", "No model"), ("local-model", "No staged changes")]:
        try:
            cm.generate_commit_message(model, "repo")
        except ValueError as exc:
            error = str(exc)
        else:
            error = ""
        assert expected in error


def test_clean_and_split_commit_message():
    raw = "```text\nCommit message: Update git workflow\n\nKeep staging clear.\n```"

    summary, body = cm.split_commit_message(raw)

    assert summary == "Update git workflow"
    assert body == "Keep staging clear."


def test_clean_commit_message_removes_chatml_control_tokens():
    raw = (
        "<|im_start|>assistant\n"
        "Update git workflow\n\n"
        "Add staged commit support.<|im_end|>\n"
        "<|im_end|>"
    )

    summary, body = cm.split_commit_message(raw)

    assert summary == "Update git workflow"
    assert body == "Add staged commit support."


def test_clean_commit_message_removes_think_blocks():
    raw = "<think>compare options first</think>\nUpdate git commit generation"

    assert cm.clean_commit_message(raw) == "Update git commit generation"


def test_commit_message_response_helpers_cover_unusual_shapes():
    dict_content = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=[
            {"text": "Update "},
            {"type": "output_text", "text": "settings"},
            {"content": " ignored"},
        ]))]
    )
    plain_content = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=123))]
    )

    assert cm._openai_message_text(dict_content) == "Update settings ignored"
    assert cm._openai_message_text(plain_content) == "123"
    assert cm._openai_message_text(SimpleNamespace(choices=[])) == ""
    assert cm._finish_reason(SimpleNamespace(stop_reason="max_tokens")) == "max_tokens"
    assert cm._finish_reason(SimpleNamespace()) == ""
    assert cm._empty_summary_note(SimpleNamespace(stop_reason="length")) == (
        "[chunk summary ran out of output tokens]"
    )
    assert cm._empty_summary_note(SimpleNamespace()) == "[chunk summary returned no visible text]"


def test_commit_message_small_helpers():
    cfg = SimpleNamespace(api="anthropic", base_url="http://api", api_key_spec="")

    assert cm._client_kwargs(cfg) == {"api_key": "", "base_url": "http://api"}
    assert cm._final_max_tokens(cfg) == cm.COMMIT_MESSAGE_MAX_TOKENS
    assert cm._chunk_text("", 10) == [""]
    assert cm._chunk_text("abcdef", 2) == ["ab", "cd", "ef"]
    assert cm.split_commit_message("   ") == ("", "")


def test_generate_commit_message_uses_anthropic_current_model(monkeypatch):
    cfg = SimpleNamespace(api="anthropic", api_key_spec="ANTHROPIC_API_KEY", base_url="")
    mock_client = patch("services.commit_message._anthropic_client").start()
    mock_client.return_value.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(text="Update staged files")]
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setattr(cm, "get_model_config", lambda _model: cfg)
    monkeypatch.setattr(
        cm,
        "staged_commit_parts",
        lambda _repo: ("M\tsrc/main.py", "src/main.py | 1 +", "+change"),
    )

    try:
        message = cm.generate_commit_message("claude-custom", "repo", "Use Jira keys")
    finally:
        patch.stopall()

    assert message == "Update staged files"
    kwargs = mock_client.return_value.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-custom"
    assert kwargs["max_tokens"] == cm.COMMIT_MESSAGE_MAX_TOKENS
    assert "Use Jira keys" in kwargs["messages"][0]["content"]


def test_generate_commit_message_uses_openai_compatible_current_model(monkeypatch):
    cfg = SimpleNamespace(
        api="openai-compatible",
        api_key_spec="OPENAI_API_KEY",
        base_url="http://local",
        temperature=0.6,
        top_k=20,
        min_p=0.05,
    )
    mock_client = patch("services.commit_message._openai_client").start()
    mock_client.return_value.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Update sidebar commits"),
                finish_reason="stop",
            )
        ]
    )
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setattr(cm, "get_model_config", lambda _model: cfg)
    monkeypatch.setattr(
        cm,
        "staged_commit_parts",
        lambda _repo: ("M\tsrc/main.py", "src/main.py | 1 +", "+change"),
    )

    try:
        message = cm.generate_commit_message("local-model", "repo")
    finally:
        patch.stopall()

    assert message == "Update sidebar commits"
    assert mock_client.call_args.kwargs["base_url"] == "http://local"
    kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "local-model"
    assert "max_tokens" not in kwargs
    assert kwargs["temperature"] == 0.6
    assert kwargs["extra_body"] == {"top_k": 20, "min_p": 0.05}


def test_generate_commit_message_reports_length_empty_response(monkeypatch):
    cfg = SimpleNamespace(api="openai-compatible", api_key_spec="OPENAI_API_KEY", base_url="")
    mock_client = patch("services.commit_message._openai_client").start()
    mock_client.return_value.chat.completions.create.side_effect = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=""),
                    finish_reason="length",
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=""),
                    finish_reason="length",
                )
            ]
        ),
    ]
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setattr(cm, "get_model_config", lambda _model: cfg)
    monkeypatch.setattr(
        cm,
        "staged_commit_parts",
        lambda _repo: ("M\tsrc/main.py", "src/main.py | 1 +", "+change"),
    )

    try:
        try:
            cm.generate_commit_message("local-model", "repo")
        except ValueError as exc:
            error = str(exc)
        else:
            error = ""
    finally:
        patch.stopall()

    assert "ran out of output tokens" in error


def test_generate_commit_message_retries_empty_length_response(monkeypatch):
    cfg = SimpleNamespace(api="openai-compatible", api_key_spec="OPENAI_API_KEY", base_url="")
    mock_client = patch("services.commit_message._openai_client").start()
    mock_client.return_value.chat.completions.create.side_effect = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=""),
                    finish_reason="length",
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Update staged git workflow"),
                    finish_reason="stop",
                )
            ]
        ),
    ]
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setattr(cm, "get_model_config", lambda _model: cfg)
    monkeypatch.setattr(
        cm,
        "staged_commit_parts",
        lambda _repo: ("M\tsrc/main.py", "src/main.py | 1 +", "+change"),
    )

    try:
        message = cm.generate_commit_message("local-model", "repo")
    finally:
        patch.stopall()

    assert message == "Update staged git workflow"
    calls = mock_client.return_value.chat.completions.create.call_args_list
    assert "max_tokens" not in calls[0].kwargs
    assert calls[1].kwargs["max_tokens"] == cm.COMMIT_MESSAGE_RETRY_MAX_TOKENS
    assert "Reply with only one concise commit summary line" in calls[1].kwargs["messages"][0]["content"]


def test_generate_commit_message_compacts_large_diff_iteratively(monkeypatch):
    cfg = SimpleNamespace(api="openai-compatible", api_key_spec="OPENAI_API_KEY", base_url="")
    mock_client = patch("services.commit_message._openai_client").start()
    mock_client.return_value.chat.completions.create.side_effect = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="first chunk summary"),
                    finish_reason="stop",
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="second chunk summary"),
                    finish_reason="stop",
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Update generated commit messages"),
                    finish_reason="stop",
                )
            ]
        ),
    ]
    huge_diff = "x" * (cm.MAX_STAGED_DIFF_CHARS + 100)
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setattr(cm, "get_model_config", lambda _model: cfg)
    monkeypatch.setattr(
        cm,
        "staged_commit_parts",
        lambda _repo: ("M\tsrc/main.py", "src/main.py | 1 +", huge_diff),
    )

    try:
        message = cm.generate_commit_message("local-model", "repo")
    finally:
        patch.stopall()

    assert message == "Update generated commit messages"
    calls = mock_client.return_value.chat.completions.create.call_args_list
    assert len(calls) == 3
    assert calls[0].kwargs["max_tokens"] == cm.COMMIT_MESSAGE_SUMMARY_MAX_TOKENS
    final_prompt = calls[-1].kwargs["messages"][0]["content"]
    assert "COMPACTED STAGED DIFF SUMMARY" in final_prompt
    assert "first chunk summary" in final_prompt
    assert "second chunk summary" in final_prompt
