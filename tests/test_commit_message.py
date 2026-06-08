from types import SimpleNamespace
from unittest.mock import patch

import services.commit_message as cm


def test_staged_commit_context_includes_names_stats_and_diff(git_repo):
    main = git_repo / "src" / "main.py"
    main.write_text("print('message')\n", encoding="utf-8")
    from services.git_status import stage_files

    assert stage_files(str(git_repo), ["src/main.py"]).ok

    context = cm.staged_commit_context(str(git_repo))

    assert "STAGED FILES:" in context
    assert "M\tsrc/main.py" in context
    assert "STAGED STATS:" in context
    assert "src/main.py" in context
    assert "STAGED DIFF:" in context
    assert "+print('message')" in context


def test_staged_commit_context_truncates_large_diff(git_repo):
    main = git_repo / "src" / "main.py"
    main.write_text("\n".join(f"print({i})" for i in range(100)) + "\n", encoding="utf-8")
    from services.git_status import stage_files

    assert stage_files(str(git_repo), ["src/main.py"]).ok

    context = cm.staged_commit_context(str(git_repo), max_diff_chars=80)

    assert "[diff truncated]" in context


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


def test_generate_commit_message_uses_anthropic_current_model(monkeypatch):
    cfg = SimpleNamespace(api="anthropic", api_key_spec="ANTHROPIC_API_KEY", base_url="")
    mock_client = patch("services.commit_message.anthropic.Anthropic").start()
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
    cfg = SimpleNamespace(api="openai-compatible", api_key_spec="OPENAI_API_KEY", base_url="http://local")
    mock_client = patch("services.commit_message.OpenAI").start()
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


def test_generate_commit_message_reports_length_empty_response(monkeypatch):
    cfg = SimpleNamespace(api="openai-compatible", api_key_spec="OPENAI_API_KEY", base_url="")
    mock_client = patch("services.commit_message.OpenAI").start()
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
    mock_client = patch("services.commit_message.OpenAI").start()
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
    mock_client = patch("services.commit_message.OpenAI").start()
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
