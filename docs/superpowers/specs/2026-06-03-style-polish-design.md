# Style Polish (POI declutter + lint) — Design Spec

**Date:** 2026-06-03
**Sub-project:** ④a of the NullMaps upgrade program (①✅ ②✅ ③a✅ ③b✅ → **④a Style polish** → ④b 3D terrain)
**Branch:** `feat/style-polish`
**Status:** Approved design → ready for implementation plan

## Context

The last visible-polish work on the tiles/style subsystem. Two items: declutter the POI symbol layer with
rank-based zoom gating, and add cheap lint insurance for the hand-authored styles.

**Confirmed from code (read, not assumed):**

- `services/tiles/style/style.json` `poi-icons` layer (lines 623-733): `minzoom: 14`; a `filter` that
  already whitelists 21 OMT `poi` classes (lines 628-660); an `icon-image` `match` (lines 662-711) with an
  explicit arm per whitelisted class **and a trailing `"attraction"` default (line 710) that is
  unreachable** — the filter guarantees `class` ∈ the whitelist, every member of which has an arm. So the
  survey's "unmatched classes render as attraction" concern does not actually occur; the default is dead.
- The real clutter cause: **no rank/zoom gating** — all 21 classes render from z14, so dense HCMC areas
  pile up. The OMT `poi` source-layer carries a numeric `rank` attribute (1 = most prominent in the tile).
- `style-dark.json` is a near-copy of `style.json` and has the same `poi-icons` layer.
- Sprites are 20 raw SVGs in `services/tiles/sprites/`; Martin (`docker-compose.yml:15` `--sprite /sprites`)
  serves the atlas **including `@2x` retina** at runtime, so pre-baking with spreet adds no retina value —
  dropped. Only the icon-name coverage check is kept.
- `style.json:768` `"sprite": "/tiles/sprite/sprites"`; `:7` `"glyphs": "/tiles/font/..."`.
- `.github/workflows/ci.yml` has `compose`, `tests`, and `shell` jobs; the runners are `ubuntu-latest`
  (node + npx available).

**Design decisions (locked with operator):**

- Declutter via **rank-based zoom gating** + `symbol-sort-key`, not per-class hardcoding.
- Drop the spreet sprite pre-bake (Martin already serves `@2x`); keep only the CI icon-name coverage check.
- Apply identical changes to `style.json` **and** `style-dark.json`.

**Explicitly out of scope:** 3D terrain (④b, separate spec), spreet sprite atlas, new POI classes/sprites,
glyph/CJK coverage.

## Goals / Success Criteria

1. At z14 only top-rank POIs render; lower-rank POIs appear progressively by z15/z16 — HCMC is less
   cluttered while important POIs stay visible.
2. When icons collide, the more prominent (lower `rank`) POI wins (`symbol-sort-key`).
3. The dead trailing `attraction` default is removed from the `icon-image` match (cleanup; no behavior
   change since it was unreachable).
4. `make style-lint` validates both styles against the MapLibre style spec and confirms every
   `icon-image` name has a matching `sprites/<name>.svg`; CI runs it.
5. Both `style.json` and `style-dark.json` get the same treatment and stay spec-valid.

## Design

### 1 — POI rank/zoom gating (`style.json` + `style-dark.json`, `poi-icons` layer)

**Filter** — wrap the existing class whitelist in an `all` with a zoom-vs-rank cap:

```json
"filter": ["all",
  ["in", ["get", "class"], ["literal", [ ...the existing 21 classes... ]]],
  ["<=", ["get", "rank"], ["step", ["zoom"], 3, 15, 6, 16, 99]]
]
```

So z14 → `rank ≤ 3`, z15 → `rank ≤ 6`, z16+ → `rank ≤ 99` (effectively all). The Planetiler/OMT `poi`
layer reliably emits `rank`; if it were ever absent, `["get","rank"]` is `null` and `<=` evaluates
`false`, which safely hides the rank-less POI at all zooms — acceptable for a declutter pass.

**Layout** — add a sort key so collisions favor prominence, and remove the dead default. In the
`icon-image` `match`, delete the trailing `"attraction"` default arm (the last bare `"attraction"` at the
end of the match array). Add to `layout`:

```json
"symbol-sort-key": ["get", "rank"]
```

`icon-allow-overlap`/`text-allow-overlap` stay unset (default `false`) so MapLibre's collision detection
de-dups; `symbol-sort-key` makes that deterministic by rank.

### 2 — Style + icon lint (`Makefile`, new `services/tiles/check-icons.mjs`, CI)

**`services/tiles/check-icons.mjs`** (new) — a dependency-free Node script that:
- reads `style/style.json` and `style/style-dark.json`,
- walks each `icon-image` `match` expression and collects the literal sprite names (the odd-position
  values + any default),
- asserts each name has a `services/tiles/sprites/<name>.svg`,
- exits non-zero listing any missing icon, else prints `OK`.

**`Makefile`** — add a `style-lint` target:

```makefile
.PHONY: style-lint
style-lint: ## (tiles) Validate styles against the spec + check icon/sprite coverage
	npx -y @maplibre/maplibre-gl-style-spec validate services/tiles/style/style.json
	npx -y @maplibre/maplibre-gl-style-spec validate services/tiles/style/style-dark.json
	node services/tiles/check-icons.mjs
```

**CI** — add a `style` job to `.github/workflows/ci.yml` mirroring the existing job style: checkout,
`actions/setup-node`, then run the two `npx ... validate` calls and `node services/tiles/check-icons.mjs`.

### Testing

- `make style-lint` is the test: the spec validator catches malformed expressions (including a mistake in
  the new filter/sort-key), and `check-icons.mjs` catches an icon name with no sprite. Both run in CI.
- The visual effect (less clutter at z14, important POIs retained) is verified manually with `make demo`
  and documented — styles have no unit-test harness.
- `check-icons.mjs` itself is exercised by `make style-lint` against the real styles (must pass on the
  current 20 sprites after the edits).

## Risks & Mitigations

- **`rank` semantics** — if a POI lacks `rank`, the `<=` comparison against a `null` is `false`, hiding it.
  OMT reliably emits `rank`, and hiding rank-less POIs only helps declutter; acceptable.
- **Two styles drift** — the same edit must land in `style.json` and `style-dark.json`; `make style-lint`
  validates both, and `check-icons.mjs` checks both, so a missed/garbled edit fails CI.
- **`npx` network fetch in CI** — `@maplibre/maplibre-gl-style-spec` is fetched on demand; if the runner is
  offline the job fails loudly (acceptable — CI has network).

## Definition of Done

- `make style-lint` passes locally (both styles spec-valid, all icon names covered).
- The `poi-icons` layer in both styles has the rank/zoom filter + `symbol-sort-key` and no dead default.
- CI has a `style` job running the same checks.
- `make demo` shows reduced z14 POI density with prominent POIs retained (manual check, noted in the tiles README).
