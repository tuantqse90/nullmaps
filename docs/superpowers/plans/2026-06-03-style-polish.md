# Style Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declutter the POI symbol layer with rank/zoom gating and add style-spec + icon-coverage lint to guard the hand-authored MapLibre styles.

**Architecture:** Edits to `services/tiles/style/style.json` + `style-dark.json` (POI layer), a dependency-free Node coverage checker, a `make style-lint` target, and a CI `style` job. The validator + checker ARE the tests (styles have no unit harness); the visual effect is a manual `make demo` check.

**Tech Stack:** MapLibre style spec, `@maplibre/maplibre-gl-style-spec` (`gl-style-validate` via npx), Node (ESM).

**Spec:** `docs/superpowers/specs/2026-06-03-style-polish-design.md`

**Branch:** `feat/style-polish` (already created)

**Verified upfront:** both current styles pass `npx -y -p @maplibre/maplibre-gl-style-spec gl-style-validate <file>` (exit 0). The correct binary is `gl-style-validate` (NOT `validate`).

---

## File Structure

- **Create** `services/tiles/check-icons.mjs` — asserts every `icon-image` sprite name has an SVG.
- **Modify** `Makefile` — `style-lint` target.
- **Modify** `services/tiles/style/style.json` + `services/tiles/style/style-dark.json` — POI rank/zoom gating, `symbol-sort-key`, remove the dead default.
- **Modify** `.github/workflows/ci.yml` — `style` job.
- **Modify** `services/tiles/README.md` — note the declutter + lint.

---

## Task 1: `check-icons.mjs` icon-coverage checker

**Files:**
- Create: `services/tiles/check-icons.mjs`

- [ ] **Step 1: Create the checker**

```js
// Verify every icon-image sprite name referenced by the styles has a matching SVG
// in services/tiles/sprites/. Dependency-free; run: node services/tiles/check-icons.mjs
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url)); // services/tiles
const SPRITES = join(here, "sprites");
const STYLES = ["style/style.json", "style/style-dark.json"];

// Extract sprite names from an icon-image value: a bare string, or a
// ["match", input, k0, v0, k1, v1, ..., default?] expression (values + default).
function spriteNames(ii) {
  if (typeof ii === "string") return [ii];
  if (!Array.isArray(ii) || ii[0] !== "match") return [];
  const rest = ii.slice(2);                 // drop "match" and the input expression
  const hasDefault = rest.length % 2 === 1;
  const pairEnd = hasDefault ? rest.length - 1 : rest.length;
  const names = [];
  for (let i = 1; i < pairEnd; i += 2) names.push(rest[i]);   // the value of each key/value pair
  if (hasDefault) names.push(rest[rest.length - 1]);
  return names.filter((n) => typeof n === "string");
}

const missing = [];
for (const sf of STYLES) {
  const style = JSON.parse(readFileSync(join(here, sf), "utf8"));
  for (const layer of style.layers || []) {
    const ii = layer.layout && layer.layout["icon-image"];
    if (!ii) continue;
    for (const name of spriteNames(ii)) {
      if (!existsSync(join(SPRITES, name + ".svg"))) {
        missing.push(`${sf}: layer '${layer.id}' -> ${name}.svg`);
      }
    }
  }
}

if (missing.length) {
  console.error("Missing sprite(s):\n" + missing.join("\n"));
  process.exit(1);
}
console.log(`OK: all icon-image names have sprites (${STYLES.length} styles checked)`);
```

- [ ] **Step 2: Verify it passes on the current styles**

Run: `cd /Users/nullshift-labs/dev/nullmap && node services/tiles/check-icons.mjs`
Expected: `OK: all icon-image names have sprites (2 styles checked)`

- [ ] **Step 3: Verify it actually fails when a sprite is missing (sanity)**

Run:
```bash
cd /Users/nullshift-labs/dev/nullmap
node -e 'import("./services/tiles/check-icons.mjs")' >/dev/null 2>&1; echo "current exit: $?"
# temporarily hide a sprite to prove the check catches it, then restore
mv services/tiles/sprites/bank.svg /tmp/bank.svg && (node services/tiles/check-icons.mjs; echo "missing exit: $?") ; mv /tmp/bank.svg services/tiles/sprites/bank.svg
```
Expected: `current exit: 0`, then a "Missing sprite(s)" line mentioning `bank.svg` and `missing exit: 1`, and `bank.svg` is restored.

- [ ] **Step 4: Commit**

```bash
git add services/tiles/check-icons.mjs
git commit -m "feat(tiles): icon-image sprite coverage checker"
```

---

## Task 2: `make style-lint`

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Add the target**

Append to `Makefile` (after the `matrix-test` / `backup-test` targets near the end):

```makefile
.PHONY: style-lint
style-lint: ## (tiles) Validate styles vs the MapLibre spec + check icon/sprite coverage
	npx -y -p @maplibre/maplibre-gl-style-spec gl-style-validate services/tiles/style/style.json
	npx -y -p @maplibre/maplibre-gl-style-spec gl-style-validate services/tiles/style/style-dark.json
	node services/tiles/check-icons.mjs
```

- [ ] **Step 2: Verify it passes on the current styles**

Run: `cd /Users/nullshift-labs/dev/nullmap && make style-lint`
Expected: both `gl-style-validate` calls print nothing and exit 0, then `OK: all icon-image names have sprites (2 styles checked)`.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "build(tiles): make style-lint (spec validate + icon coverage)"
```

---

## Task 3: POI rank/zoom gating in `style.json`

**Files:**
- Modify: `services/tiles/style/style.json` (`poi-icons` layer ~lines 623-733)

- [ ] **Step 1: Wrap the filter with a rank/zoom cap**

In `services/tiles/style/style.json`, replace the `poi-icons` layer's filter block exactly:

```json
      "filter": [
        "in",
        [
          "get",
          "class"
        ],
        [
          "literal",
          [
            "restaurant",
            "cafe",
            "fast_food",
            "fuel",
            "hospital",
            "pharmacy",
            "school",
            "college",
            "bank",
            "lodging",
            "parking",
            "bus",
            "grocery",
            "supermarket",
            "park",
            "police",
            "fire_station",
            "place_of_worship",
            "cinema",
            "attraction",
            "car"
          ]
        ]
      ],
```

with:

```json
      "filter": [
        "all",
        [
          "in",
          ["get", "class"],
          ["literal", [
            "restaurant", "cafe", "fast_food", "fuel", "hospital", "pharmacy",
            "school", "college", "bank", "lodging", "parking", "bus", "grocery",
            "supermarket", "park", "police", "fire_station", "place_of_worship",
            "cinema", "attraction", "car"
          ]]
        ],
        ["<=", ["get", "rank"], ["step", ["zoom"], 3, 15, 6, 16, 99]]
      ],
```

- [ ] **Step 2: Replace the dead trailing default with a no-op empty-string fallback**

> **Implementation note:** `gl-style-validate` requires an even number of arguments in a `match`
> expression (each key must have a paired value, plus an explicit default). Removing the default
> entirely (`["match", ..., "car", "car"]`) yields `'Expected an even number of arguments'` (exit 1).
> The spec-compliant equivalent is to replace the dead `"attraction"` default with `["literal", ""]`,
> which renders no icon for any unmatched class — functionally identical to no default.

Replace:

```json
          "car",
          "car",
          "attraction"
        ],
```

with:

```json
          "car",
          "car",
          ["literal", ""]
        ],
```

- [ ] **Step 3: Add `symbol-sort-key` so prominent POIs win collisions**

Replace:

```json
        "icon-size": 1,
        "icon-optional": false,
```

with:

```json
        "icon-size": 1,
        "icon-optional": false,
        "symbol-sort-key": ["get", "rank"],
```

- [ ] **Step 4: Verify the style is still spec-valid + icons covered**

Run: `cd /Users/nullshift-labs/dev/nullmap && make style-lint`
Expected: exit 0, `OK: all icon-image names have sprites (2 styles checked)`. (gl-style-validate accepts the new `all`/`step`/`symbol-sort-key` expressions; replacing the dead default with `["literal", ""]` does not drop any sprite name since `attraction` is still a real match arm, and the empty-string fallback is filtered out by `check-icons.mjs`.)

- [ ] **Step 5: Commit**

```bash
git add services/tiles/style/style.json
git commit -m "feat(tiles): POI rank/zoom gating + symbol-sort-key (light style)"
```

---

## Task 4: Same POI gating in `style-dark.json`

**Files:**
- Modify: `services/tiles/style/style-dark.json` (`poi-icons` layer)

- [ ] **Step 1: Apply the identical three edits**

`style-dark.json`'s `poi-icons` layer is a copy of `style.json`'s, so apply the **same three edits as Task 3** to `services/tiles/style/style-dark.json`:
1. Wrap the filter (Task 3 Step 1 old → new).
2. Replace the dead trailing default with `["literal", ""]` (Task 3 Step 2 old → new; see Task 3 Step 2 note — bare removal is rejected by gl-style-validate).
3. Add `symbol-sort-key` (Task 3 Step 3 old → new).

First read the `poi-icons` layer in `style-dark.json` to confirm the three anchor strings match byte-for-byte (they are expected identical). If any anchor differs (e.g. a color line nearby), apply the same logical change to that file's exact text — the transformation is identical, only surrounding context could differ.

- [ ] **Step 2: Verify both styles still lint**

Run: `cd /Users/nullshift-labs/dev/nullmap && make style-lint`
Expected: exit 0, `OK: all icon-image names have sprites (2 styles checked)`.

- [ ] **Step 3: Confirm both styles got the change**

Run:
```bash
cd /Users/nullshift-labs/dev/nullmap
grep -c "symbol-sort-key" services/tiles/style/style.json services/tiles/style/style-dark.json
```
Expected: `1` for each file.

- [ ] **Step 4: Commit**

```bash
git add services/tiles/style/style-dark.json
git commit -m "feat(tiles): POI rank/zoom gating + symbol-sort-key (dark style)"
```

---

## Task 5: CI `style` job

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add the job**

In `.github/workflows/ci.yml`, after the `shell` job (end of file), add:

```yaml
  style:
    name: style validate + icon coverage
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - name: Validate styles + icon coverage
        run: |
          npx -y -p @maplibre/maplibre-gl-style-spec gl-style-validate services/tiles/style/style.json
          npx -y -p @maplibre/maplibre-gl-style-spec gl-style-validate services/tiles/style/style-dark.json
          node services/tiles/check-icons.mjs
```

- [ ] **Step 2: Verify the workflow YAML parses + the commands run locally**

Run:
```bash
cd /Users/nullshift-labs/dev/nullmap
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"
make style-lint && echo "style-lint ok"
```
Expected: `yaml ok` then `style-lint ok`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: style-spec validate + icon coverage job"
```

---

## Task 6: Document + final verification

**Files:**
- Modify: `services/tiles/README.md`

- [ ] **Step 1: Add a README note**

Append to `services/tiles/README.md`:

```markdown
## POI declutter + style lint (④a)

- The `poi-icons` layer is rank/zoom-gated: z14 shows only the most prominent POIs (`rank ≤ 3`),
  relaxing to `rank ≤ 6` at z15 and all at z16+, with `symbol-sort-key` so prominent POIs win
  collisions. Tune the `["step", ["zoom"], 3, 15, 6, 16, 99]` thresholds to taste.
- `make style-lint` validates `style.json` + `style-dark.json` against the MapLibre style spec and
  checks every `icon-image` name has a `sprites/<name>.svg` (`services/tiles/check-icons.mjs`). CI runs it.
- Verify the visual effect with `make demo`: fewer POI pins in dense HCMC at z14, important POIs retained.
```

- [ ] **Step 2: Final verification**

Run:
```bash
cd /Users/nullshift-labs/dev/nullmap
make style-lint && echo "lint ok"
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"
git log --oneline main..HEAD
```
Expected: `lint ok`, `yaml ok`, and the git log shows the spec + plan commits + the 6 task commits.

- [ ] **Step 3: Commit**

```bash
git add services/tiles/README.md
git commit -m "docs(tiles): document POI declutter + style lint"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** §1 POI rank/zoom gating + sort-key + dead-default replaced with `["literal",""]` no-op fallback (gl-style-validate requires an explicit fallback), both styles (T3 light + T4 dark) ✓ · §2 check-icons.mjs (T1) + style-lint (T2) + CI job (T5) ✓ · README note (T6) ✓. All success criteria mapped. Tooling (T1/T2) lands before the style edits so the "test" exists first.
- **Placeholder scan:** none — full file for check-icons.mjs, exact old→new JSON blocks for the style edits, exact Makefile/CI snippets. T4 reuses T3's exact anchors with a documented fallback (read + apply the same logical change) because the two styles are copies — not a vague instruction.
- **Type/name consistency:** the validator binary is `gl-style-validate` (verified) everywhere (Makefile T2, CI T5); `check-icons.mjs` (T1) is the same path invoked by `make style-lint` (T2) and CI (T5); `symbol-sort-key`/`["<=", ["get","rank"], ["step", ...]]` are identical in T3 and T4; the `node-version: "20"` CI runner has npx for the on-demand `@maplibre/maplibre-gl-style-spec` fetch.
```
