"""AI address normalizer (Phase 5, optional polish).

Cleans messy Vietnamese address input — expands abbreviations (Q1 -> Quận 1,
P. -> Phường, Đ. -> Đường, TP.HCM -> Thành phố Hồ Chí Minh), restores diacritics,
fixes obvious typos — BEFORE the text hits the geocoder. Provider-agnostic via
LiteLLM (the brief's local Qwen, or DashScope / any OpenAI-compatible endpoint).

Fail-open and optional:
  - No LLM configured  -> /normalize is a no-op (returns input unchanged).
  - LLM call errors     -> returns input unchanged (never blocks geocoding).

Config (env):
  LLM_MODEL     e.g. "dashscope/qwen-plus", "openai/qwen2.5-7b-instruct", "ollama/qwen2.5"
  LLM_API_KEY   provider key (or use the provider's native env, e.g. DASHSCOPE_API_KEY)
  LLM_API_BASE  optional, for self-hosted / OpenAI-compatible / Ollama endpoints
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Query

LLM_MODEL = os.environ.get("LLM_MODEL", "").strip()
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_API_BASE = os.environ.get("LLM_API_BASE", "").strip()
# Native provider keys also count as "configured" (LiteLLM reads them itself).
_PROVIDER_ENV = ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "QWEN_API_KEY")
ENABLED = bool(LLM_MODEL) and (bool(LLM_API_KEY) or bool(LLM_API_BASE)
                               or any(os.environ.get(k) for k in _PROVIDER_ENV))

SYSTEM_PROMPT = (
    "You normalize messy Vietnamese postal addresses. Given one address, return a "
    "single cleaned address line and NOTHING else. Rules: expand abbreviations "
    "(Q1->Quận 1, P.->Phường, Đ./D.->Đường, TP.HCM/HCM->Thành phố Hồ Chí Minh, "
    "TX->Thị xã, TT->Thị trấn, KP->Khu phố); restore correct Vietnamese diacritics; "
    "fix obvious typos; keep the original meaning and house numbers; do not invent "
    "details or add commentary. Output only the cleaned address."
)

app = FastAPI(title="NullMaps Normalizer", version="0.5.0")


def build_messages(q: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": q},
    ]


def _llm_kwargs() -> dict:
    kw: dict = {"model": LLM_MODEL, "temperature": 0, "timeout": 12, "max_tokens": 200}
    if LLM_API_KEY:
        kw["api_key"] = LLM_API_KEY
    if LLM_API_BASE:
        kw["api_base"] = LLM_API_BASE
    return kw


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "nullmaps-normalizer",
            "enabled": ENABLED, "model": LLM_MODEL or None}


@app.get("/normalize")
def normalize(q: str = Query(...)) -> dict:
    q = q.strip()
    if not ENABLED or not q:
        return {"original": q, "normalized": q, "engine": "noop"}
    try:
        import litellm
        resp = litellm.completion(messages=build_messages(q), **_llm_kwargs())
        text = (resp.choices[0].message.content or "").strip()
        return {"original": q, "normalized": text or q, "engine": LLM_MODEL}
    except Exception as e:  # fail-open: never block geocoding on the LLM
        return {"original": q, "normalized": q, "engine": "error", "detail": str(e)}
