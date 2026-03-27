import os
import sys
from unittest.mock import patch, MagicMock
import rlm_tools_bsl.llm_bridge as _llm_bridge_module
from rlm_tools_bsl.llm_bridge import (
    make_llm_query,
    make_llm_query_batched,
    _make_openai_query,
    get_llm_query_fn,
    warmup_openai_import,
)


# ── Existing Anthropic tests (unchanged) ──────────────


def test_llm_query_calls_anthropic():
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(content=[MagicMock(text="YES - handles errors properly")])

    query_fn = make_llm_query(client=mock_client, model="claude-haiku-4-5-20251001")
    result = query_fn("Does this handle errors?", context="some code here")

    assert "YES" in result
    mock_client.messages.create.assert_called_once()


def test_llm_query_without_context():
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(content=[MagicMock(text="42")])

    query_fn = make_llm_query(client=mock_client, model="claude-haiku-4-5-20251001")
    result = query_fn("What is the answer?")

    assert "42" in result
    call_args = mock_client.messages.create.call_args
    messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
    assert len(messages) == 1
    assert "Context:" not in messages[0]["content"]


def test_llm_query_batched():
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(content=[MagicMock(text="answer")])

    query_fn = make_llm_query(client=mock_client, model="claude-haiku-4-5-20251001")
    batch_fn = make_llm_query_batched(query_fn)

    results = batch_fn(["q1", "q2", "q3"])
    assert len(results) == 3
    assert all(r == "answer" for r in results)


# ── OpenAI-compatible provider tests ──────────────────


def _mock_openai_module():
    """Inject a mock 'openai' module into sys.modules for lazy import."""
    mock_module = MagicMock()
    mock_client = MagicMock()
    mock_module.OpenAI.return_value = mock_client
    return mock_module, mock_client


def test_openai_query_calls_openai():
    mock_module, mock_client = _mock_openai_module()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="YES - works"))]
    )

    with patch.dict(sys.modules, {"openai": mock_module}):
        query_fn = _make_openai_query("http://localhost:11434/v1", "test-key", "qwen2.5:7b")
        result = query_fn("Does this work?", context="some code")

    assert "YES" in result
    mock_client.chat.completions.create.assert_called_once()
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs.get("messages")
    assert "Context:" in messages[0]["content"]


def test_openai_query_without_context():
    mock_module, mock_client = _mock_openai_module()
    mock_client.chat.completions.create.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content="42"))])

    with patch.dict(sys.modules, {"openai": mock_module}):
        query_fn = _make_openai_query("http://x", "key", "model")
        result = query_fn("What is the answer?")

    assert "42" in result
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs.get("messages")
    assert len(messages) == 1
    assert "Context:" not in messages[0]["content"]


def test_openai_empty_response():
    mock_module, mock_client = _mock_openai_module()
    mock_client.chat.completions.create.return_value = MagicMock(choices=[])

    with patch.dict(sys.modules, {"openai": mock_module}):
        query_fn = _make_openai_query("http://x", "key", "model")
        result = query_fn("test")

    assert result == ""


def test_openai_batched():
    mock_module, mock_client = _mock_openai_module()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="answer"))]
    )

    with patch.dict(sys.modules, {"openai": mock_module}):
        query_fn = _make_openai_query("http://x", "key", "model")
        batch_fn = make_llm_query_batched(query_fn)

    results = batch_fn(["q1", "q2", "q3"])
    assert len(results) == 3
    assert all(r == "answer" for r in results)


# ── Provider priority / factory tests ─────────────────


def _clean_llm_env(env_dict):
    """Helper: remove all LLM-related env vars, then set given ones."""
    keys_to_clear = [
        "RLM_LLM_BASE_URL",
        "RLM_LLM_API_KEY",
        "RLM_LLM_MODEL",
        "ANTHROPIC_API_KEY",
        "RLM_SUB_MODEL",
    ]
    cleaned = {k: v for k, v in os.environ.items() if k not in keys_to_clear}
    cleaned.update(env_dict)
    return cleaned


def test_provider_priority_openai_over_anthropic():
    mock_module, mock_client = _mock_openai_module()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="openai-response"))]
    )
    env = _clean_llm_env(
        {
            "RLM_LLM_BASE_URL": "http://localhost:11434/v1",
            "RLM_LLM_API_KEY": "test",
            "RLM_LLM_MODEL": "qwen2.5:7b",
            "ANTHROPIC_API_KEY": "sk-ant-test",
        }
    )
    with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, {"openai": mock_module}):
        fn = get_llm_query_fn()
        assert fn is not None
        result = fn("test")
        assert result == "openai-response"
        mock_module.OpenAI.assert_called_once()


def test_provider_fallback_to_anthropic():
    env = _clean_llm_env({"ANTHROPIC_API_KEY": "sk-ant-test"})
    with patch.dict(os.environ, env, clear=True), patch("rlm_tools_bsl.llm_bridge.Anthropic") as MockAnthropic:
        mock_client = MagicMock()
        MockAnthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(content=[MagicMock(text="anthropic-response")])

        fn = get_llm_query_fn()
        assert fn is not None
        result = fn("test")
        assert result == "anthropic-response"


def test_provider_none_when_no_keys():
    env = _clean_llm_env({})
    with patch.dict(os.environ, env, clear=True):
        fn = get_llm_query_fn()
        assert fn is None


def test_openai_missing_model():
    env = _clean_llm_env({"RLM_LLM_BASE_URL": "http://localhost:11434/v1"})
    with patch.dict(os.environ, env, clear=True):
        fn = get_llm_query_fn()
        assert fn is None


# ── Edge case tests ───────────────────────────────────


def test_anthropic_empty_response():
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(content=[])

    query_fn = make_llm_query(client=mock_client, model="claude-haiku-4-5-20251001")
    result = query_fn("test")

    assert result == ""


def test_openai_none_content():
    mock_module, mock_client = _mock_openai_module()
    mock_client.chat.completions.create.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content=None))])

    with patch.dict(sys.modules, {"openai": mock_module}):
        query_fn = _make_openai_query("http://x", "key", "model")
        result = query_fn("test")

    assert result == ""


def test_empty_prompt_raises_anthropic():
    mock_client = MagicMock()
    query_fn = make_llm_query(client=mock_client, model="test")

    try:
        query_fn("")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_empty_prompt_raises_openai():
    mock_module, mock_client = _mock_openai_module()

    with patch.dict(sys.modules, {"openai": mock_module}):
        query_fn = _make_openai_query("http://x", "key", "model")

    try:
        query_fn("")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_batched_empty_list():
    mock_client = MagicMock()
    query_fn = make_llm_query(client=mock_client, model="test")
    batch_fn = make_llm_query_batched(query_fn)

    results = batch_fn([])
    assert results == []
    mock_client.messages.create.assert_not_called()


def test_batched_handles_error():
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API down")

    query_fn = make_llm_query(client=mock_client, model="test")
    batch_fn = make_llm_query_batched(query_fn)

    results = batch_fn(["q1"])
    assert len(results) == 1
    assert "[ERROR]" in results[0]
    assert "API down" in results[0]


# ── Warmup tests ───────────────────────────────────────


def test_warmup_no_crash_without_openai():
    """warmup_openai_import should not raise even when openai is missing."""
    # Reset flag to test fresh
    _llm_bridge_module._openai_warmup_done = False
    try:
        with patch.dict(sys.modules, {"openai": None}):
            # openai mapped to None simulates ImportError
            _llm_bridge_module._openai_warmup_done = False
            warmup_openai_import()
        assert _llm_bridge_module._openai_warmup_done is True
    finally:
        _llm_bridge_module._openai_warmup_done = False


def test_warmup_idempotent():
    """Second call should be a no-op (flag already True)."""
    _llm_bridge_module._openai_warmup_done = False
    try:
        warmup_openai_import()
        assert _llm_bridge_module._openai_warmup_done is True
        # Second call — should not fail
        warmup_openai_import()
        assert _llm_bridge_module._openai_warmup_done is True
    finally:
        _llm_bridge_module._openai_warmup_done = False


def test_warmup_does_not_break_get_llm_query_fn():
    """Warmup should not interfere with get_llm_query_fn without RLM_LLM_MODEL."""
    _llm_bridge_module._openai_warmup_done = False
    try:
        warmup_openai_import()
        env = _clean_llm_env({})
        with patch.dict(os.environ, env, clear=True):
            fn = get_llm_query_fn()
            assert fn is None
    finally:
        _llm_bridge_module._openai_warmup_done = False
