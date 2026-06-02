# services/normalizer — AI address normalizer (Phase 5, optional)

> **Status: built, ships as a no-op.** Cleans messy Vietnamese address input before geocoding.
> Polish, not a moat. Off by default; enable by configuring an LLM.

**What:** Expands abbreviations (`Q1`→Quận 1, `P.`→Phường, `Đ.`→Đường, `TP.HCM`→Thành phố Hồ Chí Minh),
restores diacritics, and fixes typos so a messy query geocodes better.

**Why:** Real app input is messy ("q1 p.ben nghe hcm"). A light LLM pass lifts geocoder hit-rate
without changing the geocoder.

## Provider-agnostic (LiteLLM)

Works with the brief's **local Qwen**, **DashScope Qwen**, or any OpenAI-compatible / self-hosted
endpoint — just change env. Nothing is hardcoded.

```env
# no-op (default): leave LLM_MODEL empty
LLM_MODEL=dashscope/qwen-plus
DASHSCOPE_API_KEY=sk-...
# or self-hosted / OpenAI-compatible / Ollama:
LLM_MODEL=ollama/qwen2.5            # LLM_API_BASE=http://host:11434
LLM_MODEL=openai/qwen2.5-7b-instruct  # LLM_API_KEY=... LLM_API_BASE=http://host/v1
```

## Endpoints (`:8100`)

| Endpoint | Behaviour |
|---|---|
| `GET /healthz` | `{enabled, model}` |
| `GET /normalize?q=...` | cleaned address, or input unchanged when disabled/on error |

## Fail-open by design

- **Not configured** → `/normalize` returns the input unchanged (`engine: "noop"`).
- **LLM errors / times out** → returns the input unchanged (`engine: "error"`). It must never block
  geocoding.

## How the adapter uses it

The adapter calls the normalizer **only on opt-in** `?normalize=1`:

```
GET /maps/api/geocode/json?address=q1+p.ben+nghe&normalize=1&key=...
GET /maps/api/place/autocomplete/json?input=...&normalize=1&key=...
```

Without `normalize=1` (or with the normalizer disabled) geocoding behaves exactly as before.

## Verify

```bash
make norm-test     # shows enabled:false + no-op passthrough until you set LLM_MODEL
```

Offline unit tests (no LLM call) in `tests/` cover the no-op path, enable detection, message
construction, and kwargs. The live LLM path needs a configured provider key.
