// Browser QA for the standalone cislunar replay viewer.
// Captures console errors, camera modes, scrubber behaviour and screenshots.
import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const viewer = resolve(root, "cislunar_replay.html");
const outDir = resolve(root, "docs");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({
  args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader", "--ignore-gpu-blocklist"],
});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const consoleErrors = [];
page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(msg.text()); });
page.on("pageerror", (err) => consoleErrors.push("pageerror: " + err.message));

await page.goto(pathToFileURL(viewer).href, { waitUntil: "load" });
await page.waitForFunction(() => window.__CISLUNAR_STATE__ && window.__CISLUNAR_STATE__.ready === true, null, { timeout: 20000 });
await page.waitForTimeout(800);

const initial = await page.evaluate(() => ({ ...window.__CISLUNAR_STATE__ }));
await page.screenshot({ path: resolve(outDir, "preview.png") });

await page.click('[data-jump="flyby"]');
await page.waitForTimeout(700);
const flyby = await page.evaluate(() => ({ ...window.__CISLUNAR_STATE__, hudMoonAlt: document.getElementById("moon-alt").textContent.trim() }));
await page.screenshot({ path: resolve(outDir, "preview-flyby.png") });

await page.click('[data-cam="chase"]');
await page.waitForTimeout(500);
const chase = await page.evaluate(() => ({ mode: window.__CISLUNAR_STATE__.mode }));
await page.screenshot({ path: resolve(outDir, "preview-chase.png") });

await page.click('[data-cam="moon"]');
await page.waitForTimeout(500);
const moonCam = await page.evaluate(() => ({ mode: window.__CISLUNAR_STATE__.mode }));
await page.screenshot({ path: resolve(outDir, "preview-moon.png") });

await page.evaluate(() => {
  const scrub = document.getElementById("scrub");
  scrub.value = "500";
  scrub.dispatchEvent(new Event("input", { bubbles: true }));
});
await page.waitForTimeout(300);
const mid = await page.evaluate(() => ({
  index: window.__CISLUNAR_STATE__.index,
  t_s: window.__CISLUNAR_STATE__.t_s,
  met: document.getElementById("met").textContent.trim(),
  phase: document.getElementById("phase").textContent.trim(),
  earth: document.getElementById("earth-range").textContent.trim(),
  moon: document.getElementById("moon-range").textContent.trim(),
}));

const hash = createHash("sha256").update(readFileSync(viewer)).digest("hex");
const result = {
  viewer: "cislunar_replay.html",
  viewer_sha256: hash,
  viewport: { width: 1440, height: 900 },
  webgl: { ready: initial.ready, renderer: initial.renderer, three: initial.three },
  initial: { index: initial.index, phase: initial.phase, t_s: initial.t_s },
  flyby: { index: flyby.index, moonAltitudeHud: flyby.hudMoonAlt },
  cameras: { chase: chase.mode, moon: moonCam.mode },
  scrubMidpoint: mid,
  consoleErrors,
  screenshots: ["preview.png", "preview-flyby.png", "preview-chase.png", "preview-moon.png"],
};

writeFileSync(resolve(outDir, "browser-qa.json"), JSON.stringify(result, null, 2) + "\n");
console.log(JSON.stringify(result, null, 2));
await browser.close();
