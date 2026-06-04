// Playwright visual + behavioural regression test for the NullMaps theme switcher
// (light / dark / 3D-terrain). The headless feature smoke (fe_smoke.mjs) only checked
// that switching "didn't throw" — it missed a purely-visual bug where the active green
// bled past the rounded pill corner. This test catches that class:
//
//   - corner-bleed: the active button's fill must NOT reach the rounded corner pixel
//     (read via an in-page canvas from an element screenshot — no native PNG deps)
//   - switching: light -> /style.json + active=light + not dark; dark -> /style-dark.json
//     + active=dark + body.dark; terrain -> active=terrain, applies setTerrain (no new style)
//
// Uses playwright-core with the system Chrome channel (no browser download). Install:
//   ( cd harness && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm i playwright-core )
//
//   NM_BASE=https://maps.nullshift.sh NM_BASIC=nullshift:PASS node harness/fe_theme.mjs

import { chromium } from "playwright-core";

const BASE = process.env.NM_BASE || "http://localhost:8088";
const [USER, PASS] = (process.env.NM_BASIC || "nullshift:").split(":");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fails = [];
const note = (ok, msg) => { console.log(`  ${ok ? "✓" : "✗"} ${msg}`); if (!ok) fails.push(msg); };

const browser = await chromium.launch({ channel: "chrome", headless: true,
  args: ["--use-gl=angle", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"] });
const ctx = await browser.newContext({
  httpCredentials: USER ? { username: USER, password: PASS } : undefined,
  viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2,
});
const page = await ctx.newPage();
page.on("dialog", (d) => d.dismiss().catch(() => {}));
const styles = [];
page.on("response", (r) => { const m = r.url().match(/style(-dark|-terrain)?\.json/); if (m) styles.push(m[0]); });

try {
  await page.goto(BASE + "/?theme-test=" + Date.now(), { waitUntil: "networkidle", timeout: 30000 });
  await sleep(3000);

  // structure / the fix: outer buttons must be rounded so the active fill follows the pill
  const css = await page.evaluate(() => {
    const f = document.querySelector("#theme button:first-child");
    const l = document.querySelector("#theme button:last-child");
    const px = (v) => parseFloat(v) || 0;
    return {
      overflow: getComputedStyle(document.getElementById("theme")).overflow,
      firstTL: px(getComputedStyle(f).borderTopLeftRadius),
      lastTR: px(getComputedStyle(l).borderTopRightRadius),
    };
  });
  note(css.overflow === "hidden" && css.firstTL >= 6 && css.lastTR >= 6,
    `outer theme buttons rounded (first ${css.firstTL}px, last ${css.lastTR}px, overflow ${css.overflow})`);

  // corner-bleed: activate light, sample the control's top-left corner pixel — with the
  // rounded fill it must be the page/border colour, NOT the active green (#00b260).
  await page.click('#theme button[data-theme="light"]'); await sleep(800);
  const shot = await page.locator("#theme").screenshot();
  const corner = await page.evaluate(async (b64) => {
    const img = new Image(); img.src = "data:image/png;base64," + b64; await img.decode();
    const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
    const x = c.getContext("2d"); x.drawImage(img, 0, 0);
    const d = x.getImageData(3, 3, 1, 1).data;             // a few px into the rounded corner
    return [d[0], d[1], d[2], d[3]];
  }, shot.toString("base64"));
  const isGreen = corner[3] > 40 && corner[1] > 130 && corner[0] < 90 && corner[2] < 140;
  note(!isGreen, `active fill does not bleed into the rounded corner (corner px rgba ${corner.join(",")})`);

  // switching correctness
  const stateOf = async () => page.evaluate(() => ({
    active: [...document.querySelectorAll("#theme button")].filter((b) => b.classList.contains("on")).map((b) => b.dataset.theme),
    dark: document.body.classList.contains("dark"),
  }));
  let s = await stateOf();
  note(s.active.join() === "light" && !s.dark && styles.includes("style.json"), `light: ${JSON.stringify(s.active)} dark=${s.dark}`);

  styles.length = 0;
  await page.click('#theme button[data-theme="dark"]'); await sleep(2500);
  s = await stateOf();
  note(s.active.join() === "dark" && s.dark && styles.includes("style-dark.json"), `dark: ${JSON.stringify(s.active)} dark=${s.dark} style=${JSON.stringify(styles)}`);

  styles.length = 0;
  await page.click('#theme button[data-theme="terrain"]'); await sleep(3000);
  s = await stateOf();
  note(s.active.join() === "terrain" && styles.length === 0, `terrain: ${JSON.stringify(s.active)} (applies setTerrain, no new style: ${JSON.stringify(styles)})`);
} catch (e) {
  note(false, "FATAL: " + String(e).slice(0, 120));
}
await browser.close();
console.log(`\n# fe_theme: ${fails.length} failures`);
process.exit(fails.length ? 1 : 0);
