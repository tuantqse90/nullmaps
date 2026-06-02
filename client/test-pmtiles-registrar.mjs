// Verifies the PMTiles registrar: no-op without the lib, idempotent with it.
import { NullMaps } from "./nullmaps.js";

let calls = 0;
const fakeMaplibre = { addProtocol: () => { calls++; } };

// No global pmtiles available -> must NOT register.
NullMaps.registerPmtilesProtocol(fakeMaplibre);
if (calls !== 0) { console.error("FAIL: registered without pmtiles present"); process.exit(1); }

// Provide a fake pmtiles lib -> registers exactly once, even if called twice.
globalThis.pmtiles = { Protocol: function () { this.tile = () => {}; } };
NullMaps.registerPmtilesProtocol(fakeMaplibre);
NullMaps.registerPmtilesProtocol(fakeMaplibre);
if (calls !== 1) { console.error(`FAIL: expected 1 registration, got ${calls}`); process.exit(1); }

console.log("OK: pmtiles registrar is idempotent and no-ops without the lib");
