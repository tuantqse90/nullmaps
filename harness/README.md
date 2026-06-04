# NullMaps bug-hunting harness

Reusable harness for finding bugs in the **backend** (adapter / geocoder / routing)
and **frontend** (the web app). Run it after any change, or on a schedule, to catch
regressions — it encodes the failure classes we've actually hit.

## Pieces

| File | Layer | What it finds |
|---|---|---|
| `be_fuzz.py` | BE | Robustness — throws malformed/boundary inputs at every endpoint; flags any **5xx**, non-JSON body, or response missing the Google-shaped `status`. Asserts the service never crashes. |
| `be_probe.py` | BE | Correctness — a curated set of **invariants**: routes that must resolve (airports, parks, stations, cross-province) and geocodes that must land in the right area (±tolerance). Catches the "wrong province / no route" class. |
| `fe_smoke.mjs` | FE | Headless Chrome drives every feature (search, directions, multi-stop, nearby, isochrone, share+restore, theme, mobile) and asserts **0 uncaught JS errors** + behavioural invariants. Screenshots per phase. |
| `fe_theme.mjs` | FE | **Playwright** visual + behavioural regression for the theme switcher — reads the control's corner pixel (active fill must not bleed past the rounded pill corner) + asserts light/dark/3D switching. Catches the *visual* class `fe_smoke` misses (it only checks "didn't throw"). `playwright-core` + system Chrome (`channel: chrome`, no download). |
| `run.sh` | both | Orchestrates all three, aggregates, exits non-zero if anything found a bug. |

There is also a deep, on-demand multi-agent version in
`.claude/workflows/bug-hunt.js` (run via the Workflow tool / `ultracode`): it reads
the code across BE+FE, hunts bugs from several angles in parallel, and adversarially
verifies each candidate before reporting. Use the scripts here for fast regression
runs; use the workflow for an exhaustive audit.

## Run

```bash
# everything against production
API_KEY=<key> NM_BASIC=nullshift:<pass> NM_BASE=https://maps.nullshift.sh harness/run.sh

# backend only / frontend only
harness/run.sh be
harness/run.sh fe

# or the Makefile targets
make bug-hunt          # all (reads .env for API_KEY)
make bug-hunt-be
make bug-hunt-fe
```

### Config (env)

| Var | Default | Used by |
|---|---|---|
| `NM_BASE` | `http://localhost:8088` | all — target base URL |
| `API_KEY` | from `.env` | BE — key for the gated `/maps/*`, `/v1/*` |
| `NM_BASIC` | `nullshift:` | FE — `user:pass` for the basic-auth page + `/app` |
| `CHROME` | macOS Chrome path | FE — Chrome executable |
| `NM_PACE` | `0.05`–`0.1`s | BE — sleep between requests (the adapter rate-limits per key) |

## Notes / gotchas encoded here

- BE scripts hit the **key-gated** `/maps`+`/v1` directly with `X-API-Key`; the FE
  smoke uses the **basic-auth** page + keyless `/app` proxy (`page.authenticate`).
- The prod gateway is behind **Cloudflare**, which **403s the default `Python-urllib`
  User-Agent** and throttles request bursts. The BE scripts send a browser UA and
  retry transient 403/timeouts — if you still see flaky `403`/`no result`, raise
  `NM_PACE`, or point `NM_BASE` at the internal adapter (no Cloudflare).
- All `/app` traffic shares one injected key → one rate-limit bucket. Keep paces > 0
  and don't run the harness in a tight loop, or you'll get `OVER_QUERY_LIMIT` (429)
  that looks like a bug but isn't.
- FE smoke: never reads `navigator.clipboard.readText()` (hangs headless), runs the
  terrain/3D theme **last** (WebGL readback can hang), and wraps the whole run in a
  hard timeout. WebGL pixels read black headless → verify with screenshots.
- To probe **without** tripping the public rate limit, point `NM_BASE` at the
  internal adapter on the box (`docker compose exec adapter` / port-forward) — same
  scripts, no gateway.

## Extending

`be_probe.py` is the regression memory: when the map gives a dumb answer, add the
query/route + its expected outcome to `GEOCODE` / `ROUTES`. `be_fuzz.py`: add new
nasty inputs to the vocabularies or a new `(path, params, field, vocab)` case.
`fe_smoke.mjs`: add a `note(...)` invariant for any new feature.
