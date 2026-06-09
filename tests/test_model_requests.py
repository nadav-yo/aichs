from services.model_registry import ModelConfig
from services.model_requests import apply_generation_params


def test_apply_generation_params_serializes_openai_extra_body():
    cfg = ModelConfig(
        provider_id="local",
        api="openai-compatible",
        base_url="http://localhost:11434/v1",
        api_key_spec="LOCAL_KEY",
        display_name="Local",
        temperature=0.6,
        top_k=20,
        min_p=0.05,
    )
    request = {"model": "local", "extra_body": {"seed": 1}}

    apply_generation_params(request, cfg)

    assert request["temperature"] == 0.6
    assert request["extra_body"] == {"seed": 1, "top_k": 20, "min_p": 0.05}


def test_apply_generation_params_serializes_top_k_zero():
    cfg = ModelConfig(
        provider_id="local",
        api="openai-compatible",
        base_url="http://localhost:11434/v1",
        api_key_spec="LOCAL_KEY",
        display_name="Local",
        top_k=0,
    )
    request = {"model": "local"}

    apply_generation_params(request, cfg)

    assert request["extra_body"] == {"top_k": 0}


def test_apply_generation_params_serializes_top_k_negative_one():
    cfg = ModelConfig(
        provider_id="local",
        api="openai-compatible",
        base_url="http://localhost:11434/v1",
        api_key_spec="LOCAL_KEY",
        display_name="Local",
        top_k=-1,
    )
    request = {"model": "local"}

    apply_generation_params(request, cfg)

    assert request["extra_body"] == {"top_k": -1}


def test_apply_generation_params_can_skip_extra_body_for_anthropic():
    cfg = ModelConfig(
        provider_id="claude",
        api="anthropic",
        base_url=None,
        api_key_spec="ANTHROPIC_API_KEY",
        display_name="Claude",
        temperature=0.4,
        top_k=20,
        min_p=0.05,
    )
    request = {"model": "claude-sonnet-4-6"}

    apply_generation_params(request, cfg, include_extra_body=False)

    assert request == {"model": "claude-sonnet-4-6", "temperature": 0.4}


def test_apply_generation_params_ignores_unset_or_invalid_values():
    cfg = object()
    request = {"model": "local"}

    apply_generation_params(request, cfg)

    assert request == {"model": "local"}
