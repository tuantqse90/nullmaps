"""Offline tests for the normalizer (no LLM call).

The live LLM path needs a configured provider key; here we verify the no-op
behaviour, message construction, and the kwargs builder.
Run: cd services/normalizer && PYTHONPATH=. pytest -q
"""
import importlib
import os


def load(env: dict):
    for k in ("LLM_MODEL", "LLM_API_KEY", "LLM_API_BASE", "DASHSCOPE_API_KEY"):
        os.environ.pop(k, None)
    os.environ.update(env)
    import app.main as m
    return importlib.reload(m)


def test_disabled_when_no_model():
    m = load({})
    assert m.ENABLED is False
    out = m.normalize(q="Q1 P.BenNghe")
    assert out == {"original": "Q1 P.BenNghe", "normalized": "Q1 P.BenNghe", "engine": "noop"}


def test_enabled_with_model_and_key():
    m = load({"LLM_MODEL": "dashscope/qwen-plus", "LLM_API_KEY": "sk-x"})
    assert m.ENABLED is True


def test_enabled_with_native_provider_env():
    m = load({"LLM_MODEL": "dashscope/qwen-plus", "DASHSCOPE_API_KEY": "sk-x"})
    assert m.ENABLED is True


def test_messages_carry_system_rules_and_input():
    m = load({})
    msgs = m.build_messages("Q1 HCM")
    assert msgs[0]["role"] == "system" and "Quận 1" in msgs[0]["content"]
    assert msgs[1] == {"role": "user", "content": "Q1 HCM"}


def test_llm_kwargs_includes_base_and_key():
    m = load({"LLM_MODEL": "openai/qwen", "LLM_API_KEY": "k", "LLM_API_BASE": "http://x/v1"})
    kw = m._llm_kwargs()
    assert kw["model"] == "openai/qwen" and kw["api_key"] == "k"
    assert kw["api_base"] == "http://x/v1" and kw["temperature"] == 0


def test_empty_input_is_noop_even_if_enabled():
    m = load({"LLM_MODEL": "dashscope/qwen-plus", "LLM_API_KEY": "sk-x"})
    assert m.normalize(q="   ")["engine"] == "noop"
