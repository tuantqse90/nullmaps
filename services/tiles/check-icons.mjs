// Verify every icon-image sprite name referenced by the styles has a matching SVG
// in services/tiles/sprites/. Dependency-free; run: node services/tiles/check-icons.mjs
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url)); // services/tiles
const SPRITES = join(here, "sprites");
const STYLES = ["style/style.json", "style/style-dark.json", "style/style-terrain.json"];

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
