// Frontend smoke / bug harness for the NullMaps web app (services/tiles/style/index.html).
//
// Drives the live page in headless Chrome and asserts that every feature wires up
// without a single uncaught JS error, plus a few behavioural invariants. Captures a
// screenshot per phase for eyeballing.
//
// Hard-won gotchas baked in (don't regress these):
//   - the page + /app are basic-auth gated -> page.authenticate()
//   - navigator.clipboard.readText() HANGS headless -> never read it; check the toast
//   - the terrain/3D theme can hang WebGL readback -> run it LAST, in its own guard
//   - puppeteer can stall -> a Promise.race hard timeout wraps the whole run
//   - WebGL canvas pixel-readback is black headless -> assert via screenshot, not pixels
//   - puppeteer pkg's chromium download is flaky -> use system Chrome
//
// Usage:
//   NM_BASE=https://maps.nullshift.sh NM_BASIC=nullshift:PASS node harness/fe_smoke.mjs
//   (CHROME=/path/to/Chrome to override the executable)
//
// Needs puppeteer-core installed somewhere importable (npm i -g puppeteer-core, or run
// from a dir that has it). Exits non-zero if any pageerror or failed invariant.

import puppeteer from "puppeteer-core";

const BASE = process.env.NM_BASE || "http://localhost:8088";
const [USER, PASS] = (process.env.NM_BASIC || "nullshift:").split(":");
const CHROME = process.env.CHROME ||
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const SHOT_DIR = process.env.NM_SHOTS || "/tmp/nm-fe-shots";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fails = [];
const note = (ok, msg) => { console.log(`  ${ok ? "✓" : "✗"} ${msg}`); if (!ok) fails.push(msg); };

async function run() {
  const fs = await import("node:fs");
  fs.mkdirSync(SHOT_DIR, { recursive: true });

  const browser = await puppeteer.launch({
    headless: "new", protocolTimeout: 60000, executablePath: CHROME,
    args: ["--no-sandbox", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"],
  });
  const ctx = browser.defaultBrowserContext();
  try { await ctx.overridePermissions(BASE, ["clipboard-write"]); } catch {}

  const page = await browser.newPage();
  if (USER) await page.authenticate({ username: USER, password: PASS });
  await page.setViewport({ width: 1100, height: 800 });
  // headless clipboard.writeText rejects -> the app falls back to prompt(), a BLOCKING
  // dialog that freezes the page; auto-dismiss it so the smoke doesn't hang.
  page.on("dialog", (d) => d.dismiss().catch(() => {}));

  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));
  page.on("requestfailed", (r) => { if (r.url().includes("/app/")) errors.push("REQFAIL " + r.url()); });
  const api = {};
  page.on("response", (r) => {
    const k = ["autocomplete", "directions", "nearbysearch", "isochrone", "geocode"].find((x) => r.url().includes(x));
    if (k) api[k] = r.status();
  });
  const click = async (x, y) => {
    const bb = await page.$eval("canvas", (c) => { const b = c.getBoundingClientRect(); return { x: b.x, y: b.y }; });
    await page.mouse.click(bb.x + x, bb.y + y);
  };

  await page.goto(BASE + "/?harness=" + Date.now(), { waitUntil: "domcontentloaded", timeout: 30000 });
  await sleep(3000);

  // --- structure present ---
  const ui = await page.evaluate(() => ({
    search: !!document.getElementById("q"), tools: document.querySelectorAll(".tools button").length,
    chips: document.querySelectorAll("#cats .chip").length, dirToggle: !!document.getElementById("dir-toggle"),
    myLoc: !!document.getElementById("my-loc"), swap: !!document.getElementById("swap"),
    addStop: !!document.getElementById("add-stop"), theme: document.querySelectorAll("#theme button").length,
  }));
  note(ui.search && ui.tools === 3 && ui.chips === 8 && ui.theme === 3 && ui.swap && ui.myLoc,
    `UI present (tools ${ui.tools}, chips ${ui.chips}, theme ${ui.theme})`);

  // --- search + autocomplete ---
  await page.click("#q"); await page.type("#q", "ben thanh", { delay: 40 }); await sleep(1400);
  const ac = await page.evaluate(() => ({ open: document.getElementById("ac-q").classList.contains("open"), items: document.querySelectorAll("#ac-q div[data-name]").length }));
  note(api.autocomplete === 200 && ac.open && ac.items > 0, `autocomplete (${api.autocomplete}, ${ac.items} items)`);
  await page.$eval("#ac-q div[data-name]", (d) => d.click()); await sleep(1300);
  note((await page.$$eval(".nm-pin", (m) => m.length)) >= 1, "search drops a marker");
  await page.screenshot({ path: `${SHOT_DIR}/1-search.png` });

  // --- directions via map clicks (A, B) + route ---
  await page.click("#dir-toggle"); await sleep(300);
  await click(700, 360); await sleep(400); await click(840, 520); await sleep(400);
  note((await page.$$eval(".nm-pin", (m) => m.length)) === 2, "A/B pins after two map clicks");
  await page.click("#route-btn"); await sleep(3000);
  const route = await page.evaluate(() => ({ open: document.getElementById("result").classList.contains("open"), steps: document.querySelectorAll("#r-steps li").length, eta: document.getElementById("r-eta").textContent }));
  note(api.directions === 200 && route.open && route.steps > 0, `route renders (${api.directions}, ${route.steps} steps, ${route.eta})`);
  await page.screenshot({ path: `${SHOT_DIR}/2-route.png` });

  // --- nearby ---
  await page.click("#t-near"); await sleep(250);
  await page.$eval('#cats .chip[data-t="cafe"]', (c) => c.click()); await sleep(2000);
  const near = await page.evaluate(() => ({ list: document.querySelectorAll("#r-steps li.pick").length, pins: document.querySelectorAll(".nm-pin").length }));
  note(api.nearbysearch === 200 && near.list > 0 && near.pins > 1, `nearby (${near.list} results, ${near.pins} pins)`);
  await page.screenshot({ path: `${SHOT_DIR}/3-nearby.png` });

  // --- isochrone toggle + map click ---
  await page.click("#t-iso"); await sleep(250);
  note(await page.$eval("#t-iso", (e) => e.classList.contains("on")), "isochrone toggle activates");
  await click(640, 430); await sleep(3500);   // isochrone draws WebGL fills — let the GPU settle
  note(api.isochrone === 200, `isochrone request (${api.isochrone})`);

  // --- share: clicking must not throw; the round-trip is verified by the restore step
  // below. (Headless has no real clipboard, so writeText rejects -> the app's prompt()
  // fallback fires, which we auto-dismiss; the success toast only shows in a real
  // browser, so don't assert it.) ---
  try {
    await page.click("#t-share"); await sleep(800);
    const t = await page.$eval("#toast", (e) => e.textContent);
    note(true, `share clicked (toast: ${t ? JSON.stringify(t) : "n/a in headless"})`);
  } catch (e) { note(false, "share step flaked (headless): " + String(e).slice(0, 60)); }

  // --- restore from a share URL (auto-routes) + mobile reflow ---
  try {
    const p2 = await browser.newPage();
    if (USER) await p2.authenticate({ username: USER, password: PASS });
    p2.on("dialog", (d) => d.dismiss().catch(() => {}));
    const e2 = []; p2.on("pageerror", (e) => e2.push(String(e).slice(0, 160)));
    await p2.goto(BASE + "/?route=10.77693,106.70090;10.76260,106.68220&mode=two_wheeler", { waitUntil: "domcontentloaded", timeout: 30000 });
    await sleep(5000);
    const restore = await p2.evaluate(() => ({ open: !!document.getElementById("result") && document.getElementById("result").classList.contains("open"), steps: document.querySelectorAll("#r-steps li").length }));
    note(restore.open && restore.steps > 0, `share-link restore auto-routes (${restore.steps} steps)`);
    await p2.setViewport({ width: 390, height: 780 }); await sleep(400);
    const mob = await p2.$eval("#panel", (e) => { const r = e.getBoundingClientRect(); return { left: Math.round(r.left), width: Math.round(r.width) }; });
    note(mob.left <= 12 && mob.width > 300, `mobile panel reflows (left ${mob.left}, width ${mob.width})`);
    errors.push(...e2);
    await p2.close();
  } catch (e) { note(false, "restore/mobile step flaked (headless): " + String(e).slice(0, 60)); }

  // --- theme cycle LAST (terrain can hang WebGL) ---
  try {
    await page.click('#theme button[data-theme="dark"]'); await sleep(1500);
    await page.click('#theme button[data-theme="terrain"]'); await sleep(2500);
    note(true, "theme cycle (dark + terrain) without throwing");
  } catch (e) { note(false, "theme cycle threw: " + String(e).slice(0, 80)); }
  await page.screenshot({ path: `${SHOT_DIR}/4-theme.png` });

  note(errors.length === 0, `0 page errors${errors.length ? " -> " + JSON.stringify(errors) : ""}`);
  await browser.close();
}

const HARD = 150000;
await Promise.race([
  run(),
  sleep(HARD).then(() => { throw new Error(`HARD_TIMEOUT ${HARD}ms`); }),
]).catch((e) => { console.error("FATAL:", String(e).slice(0, 200)); fails.push("FATAL " + String(e).slice(0, 80)); });

console.log(`\n# fe_smoke: ${fails.length} failures  (screenshots in ${SHOT_DIR})`);
process.exit(fails.length ? 1 : 0);
