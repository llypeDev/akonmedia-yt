// End-to-end smoke test for QQplay, driven in a real browser.
//
// It walks the whole product the way a person would: sign up, upload a video
// in chunks (with a dropped connection on purpose), wait for encoding, play the
// adaptive stream and switch quality, comment and reply, build a playlist and
// play it, edit details/chapters/thumbnail, add and edit subtitles, use the
// studio, search, language switcher, theme and navigation, upload and delete an
// image, check the embed and phone layouts, and finally — if a manager account
// is given — change the new account's role from the manage page. Text selectors
// follow the default interface language, Brazilian Portuguese.
//
//   npm i -D playwright && npx playwright install chromium   (once)
//   BASE_URL=http://127.0.0.1:8000 node tools/e2e/smoke.mjs
//
// Optional: E2E_MANAGER_EMAIL / E2E_MANAGER_PASSWORD (manage step; the
// accounts from `manage.py seed_demo` work), E2E_SHOTS=<dir> for screenshots,
// E2E_VIDEO / E2E_IMAGE / E2E_VTT to use your own fixtures. Without them the
// fixtures are generated with ffmpeg.
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const SHOTS = process.env.E2E_SHOTS || "";
const stamp = Date.now().toString(36);
const EMAIL = `e2e-${stamp}@example.com`;
const PASSWORD = `E2e-pass-${stamp}-x`;

let step = 0;
function ok(message) { console.log(`  ✓ ${String(++step).padStart(2, "0")} ${message}`); }
function fail(message) { throw new Error(message); }
function expect(condition, message) { if (!condition) fail(message); ok(message); }

function fixtures() {
  if (process.env.E2E_VIDEO && process.env.E2E_IMAGE && process.env.E2E_VTT) {
    return { video: process.env.E2E_VIDEO, image: process.env.E2E_IMAGE, vtt: process.env.E2E_VTT };
  }
  const dir = mkdtempSync(join(tmpdir(), "akon-e2e-"));
  const video = join(dir, "e2e-clip.mp4");
  const image = join(dir, "e2e-still.png");
  const vtt = join(dir, "e2e.vtt");
  // ~13 MB so the upload needs three 5 MB chunks.
  execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=20", "-f", "lavfi", "-i", "sine=frequency=330:duration=20",
    "-c:v", "libx264", "-preset", "veryfast", "-b:v", "5M", "-maxrate", "5M", "-bufsize", "10M",
    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video]);
  execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "color=c=0x7c6cff:s=1280x720", "-frames:v", "1", image]);
  writeFileSync(vtt, "WEBVTT\n\n00:00:00.500 --> 00:00:03.000\nHello from the E2E track\n\n" +
    "00:00:03.500 --> 00:00:06.000\nSecond line\n");
  return { video, image, vtt };
}

async function shot(page, name) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: false });
}

function watchErrors(page, bucket) {
  page.on("pageerror", (error) => bucket.push(`pageerror ${page.url()}: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (/ERR_FAILED|ERR_ABORTED/.test(text)) return; // the connection drop we inject on purpose
    bucket.push(`console ${page.url()}: ${text}`);
  });
}

async function noHorizontalScroll(page, url) {
  await page.goto(BASE + url, { waitUntil: "networkidle" });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow <= 1, `phone layout of ${url} has no horizontal scroll (overflow ${overflow}px)`);
}

async function main() {
  const files = fixtures();
  const errors = [];
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
  const page = await context.newPage();
  watchErrors(page, errors);
  // Every question is asked in the portal's own dialog; a browser window
  // ("this page says") counts as a failure at the end.
  const browserDialogs = [];
  page.on("dialog", (dialog) => { browserDialogs.push(dialog.message()); dialog.accept(); });

  console.log(`QQplay smoke test against ${BASE} as ${EMAIL}`);

  // ------------------------------------------------------------ health
  const ready = await (await page.request.get(`${BASE}/health/ready/`)).json();
  expect(ready.status === "ok", "readiness probe reports ok");

  // ------------------------------------------------------------ sign up
  await page.goto(`${BASE}/accounts/signup/`);
  await page.fill('input[name="email"]', EMAIL);
  await page.fill('input[name="password1"]', PASSWORD);
  await page.fill('input[name="password2"]', PASSWORD);
  await Promise.all([page.waitForNavigation(), page.click('.auth-card button[type="submit"]')]);
  expect(await page.locator(".user-chip").count() === 1, "sign-up logs the new account in");

  // ------------------------------------------------------------ chunked upload with a dropped connection
  await page.goto(`${BASE}/upload/`);
  let dropped = false;
  await page.route(/\/chunk\/\?index=1$/, (route) => {
    if (!dropped) { dropped = true; return route.abort("failed"); }
    return route.continue();
  });
  await page.setInputFiles("#file-input", files.video);
  await page.locator("#file-card").waitFor();
  await page.fill("#title-input", "E2E clip");
  await page.fill("#description-input", "Uploaded by the smoke test. Jump to 0:05 for the middle.");
  await page.click("#submit-button");
  await page.waitForURL(/\/view\/[0-9a-z]+\/$/, { timeout: 300_000 });
  await page.unroute(/\/chunk\/\?index=1$/);
  expect(dropped, "a dropped chunk was retried and the upload still completed");
  const uid = page.url().match(/\/view\/([0-9a-z]+)\//)[1];
  if (await page.locator(".processing-banner[data-poll-url]").count()) {
    // A background worker is encoding: the page polls and reloads by itself.
    await page.locator(".processing-banner[data-poll-url]").waitFor({ state: "detached", timeout: 300_000 });
    ok("processing banner polled the API and reloaded when encoding finished");
  }

  // ------------------------------------------------------------ adaptive playback
  await page.waitForFunction(() => {
    const player = window.AkonPlayer && window.AkonPlayer.players[0];
    return player && player.engine === "hls.js" && player.levels().length >= 2;
  }, null, { timeout: 30_000 });
  const ladder = await page.evaluate(() => window.AkonPlayer.players[0].levels().map((l) => l.height));
  expect(ladder.includes(720) && ladder.includes(480), `HLS ladder loaded (${ladder.join("/")}p)`);
  expect(!(await page.locator(".player video").getAttribute("controls") !== null), "custom controls replaced the native ones");
  await page.click(".pl-bigplay");
  await page.waitForFunction(() => document.querySelector(".player video").currentTime > 1, null, { timeout: 20_000 });
  ok("video plays");
  await page.click(".pl-gear");
  await page.locator(".pl-menu .pl-mi", { hasText: "Qualidade" }).click();
  // Exact label: "Auto (480p)" also contains the text while auto picks that rung.
  await page.locator(".pl-menu .pl-mi", { has: page.locator(".pl-mi-label", { hasText: /^480p$/ }) }).click();
  await page.waitForFunction(() => {
    const hls = window.AkonPlayer.players[0].hls;
    return hls && !hls.autoLevelEnabled && hls.levels[hls.loadLevel] && hls.levels[hls.loadLevel].height === 480;
  }, null, { timeout: 15_000 });
  ok("quality menu switches to 480p");
  await page.keyboard.press("i");
  expect(await page.locator(".pl-stats").isVisible(), "stats overlay opens with I");
  await page.keyboard.press("i");
  await page.keyboard.press("k");
  await page.waitForFunction(() => document.querySelector(".player video").paused);
  ok("K pauses playback");
  await page.keyboard.press("t");
  expect(await page.evaluate(() => document.body.classList.contains("is-theater")), "T switches to theater mode");
  await page.keyboard.press("t");
  await shot(page, "watch");

  // ------------------------------------------------------------ comments
  await page.fill('.composer textarea[name="body"]', "Great clip, see 0:03 for the sweep.");
  await page.check(".composer [data-time-toggle]");
  await Promise.all([page.waitForNavigation(), page.click('.composer button[type="submit"]')]);
  expect(await page.locator(".comment .ts-link").count() >= 1, "comment posted with a clickable timestamp");
  expect(await page.locator(".comment .ts-chip").count() >= 1, "comment pinned to the playback moment");
  await page.locator(".comment details.reply > summary").first().click();
  await page.fill('.comment details.reply[open] textarea[name="body"]', "Replying to myself.");
  await Promise.all([page.waitForNavigation(), page.click('.comment details.reply[open] button[type="submit"]')]);
  expect(await page.locator(".replies .comment-text", { hasText: "Replying to myself." }).count() === 1, "reply appears in the thread");

  // ------------------------------------------------------------ playlists
  await page.locator("summary", { hasText: "Salvar" }).click();
  await page.fill('input[name="new_title"]', "E2E playlist");
  await Promise.all([page.waitForNavigation(), page.locator("button", { hasText: "Criar" }).click()]);
  expect(await page.locator(".toast", { hasText: "Adicionada a" }).count() === 1, "saved into a new playlist from the player page");
  await page.goto(`${BASE}/playlists/`);
  await page.locator(".playlist-card", { hasText: "E2E playlist" }).locator("a.media-title, .media-title a").first().click();
  await Promise.all([page.waitForNavigation(), page.locator("a", { hasText: "Reproduzir tudo" }).click()]);
  expect(page.url().includes("?list=") && await page.locator(".side-card").count() === 1, "playlist plays with its queue beside the player");

  // ------------------------------------------------------------ edit details, chapters, thumbnail
  await page.goto(`${BASE}/view/${uid}/edit/`);
  await page.fill("#id_title", "E2E clip (edited)");
  await Promise.all([page.waitForNavigation(), page.locator("button", { hasText: "Salvar detalhes" }).click()]);
  expect(await page.inputValue("#id_title") === "E2E clip (edited)", "details saved");
  await page.click("[data-row-add]");
  await page.click("[data-row-add]");
  const starts = page.locator('input[name="chapter_start"]');
  const titles = page.locator('input[name="chapter_title"]');
  await starts.nth(0).fill("0:00"); await titles.nth(0).fill("Intro");
  await starts.nth(1).fill("0:08"); await titles.nth(1).fill("Middle");
  await Promise.all([page.waitForNavigation(), page.locator("button", { hasText: "Salvar capítulos" }).click()]);
  expect(await page.locator(".toast", { hasText: "2 capítulos salvos" }).count() === 1, "chapters saved");
  await page.setInputFiles('input[name="poster"]', files.image);
  await Promise.all([page.waitForNavigation(), page.locator("button", { hasText: "Trocar miniatura" }).click()]);
  expect(await page.locator(".toast", { hasText: "Miniatura atualizada" }).count() === 1, "custom thumbnail uploaded");

  // ------------------------------------------------------------ subtitles
  await page.goto(`${BASE}/view/${uid}/`);
  expect(await page.locator(".chapter-list li").count() === 2, "chapters listed under the player");
  await page.fill('input[name="language"]', "en");
  await page.fill('input[name="label"]', "English");
  await page.setInputFiles('input[name="file"][accept*=".srt"]', files.vtt);
  await Promise.all([page.waitForNavigation(), page.locator("button", { hasText: "Enviar faixa" }).click()]);
  expect(page.url().includes("/subtitles/en/"), "subtitle upload opens the cue editor");
  await page.click("[data-row-add]");
  await page.locator('textarea[name="cue_text"]').last().fill("Added in the editor");
  await Promise.all([page.waitForNavigation(), page.locator("button", { hasText: "Salvar falas" }).click()]);
  const vtt = await (await page.request.get(`${BASE}/view/${uid}/subtitles/en/track.vtt`)).text();
  expect(vtt.startsWith("WEBVTT") && vtt.includes("Added in the editor"), "edited cue is served as WebVTT");

  // ------------------------------------------------------------ studio, search, chrome
  await page.goto(`${BASE}/studio/`);
  expect(await page.locator(".row-title", { hasText: "E2E clip (edited)" }).count() === 1, "studio lists the upload");
  await page.goto(`${BASE}/`);
  await page.keyboard.press("/");
  await page.keyboard.type("edited");
  await Promise.all([page.waitForNavigation(), page.keyboard.press("Enter")]);
  expect(await page.locator(".media-title", { hasText: "E2E clip (edited)" }).count() >= 1, "“/” focuses search and finds the upload");
  expect(await page.getAttribute("html", "lang") === "pt-br", "interface is in Portuguese by default");
  await Promise.all([page.waitForNavigation(), page.locator(".site-footer select[name=language]").selectOption("en")]);
  expect(await page.getAttribute("html", "lang") === "en" && await page.locator(".nav-link", { hasText: "Home" }).count() >= 1,
    "language switcher changes the interface to English");
  await Promise.all([page.waitForNavigation(), page.locator(".site-footer select[name=language]").selectOption("pt-br")]);
  expect(await page.locator(".nav-link", { hasText: "Início" }).count() >= 1, "and back to Portuguese");
  const before = await page.getAttribute("html", "data-theme");
  await page.click('[data-action="toggle-theme"]');
  expect(await page.getAttribute("html", "data-theme") !== before, "theme toggles between dark and light");
  await page.click('[data-action="toggle-theme"]');
  await page.locator('.topbar [data-action="toggle-sidebar"]').click();
  expect(await page.getAttribute("html", "data-sidebar") === "collapsed", "sidebar collapses to a rail");
  await page.locator('.topbar [data-action="toggle-sidebar"]').click();

  // ------------------------------------------------------------ image upload, then delete it
  await page.goto(`${BASE}/upload/`);
  await page.setInputFiles("#file-input", files.image);
  await page.fill("#title-input", "E2E still");
  await page.click("#submit-button");
  await page.waitForURL(/\/view\/[0-9a-z]+\/$/, { timeout: 120_000 });
  expect(await page.locator(".stage-image img").count() === 1, "image upload shows the picture");
  const stillUid = page.url().match(/\/view\/([0-9a-z]+)\//)[1];
  await page.goto(`${BASE}/view/${stillUid}/edit/`);
  const deleteButton = page.locator("form[data-confirm] button", { hasText: "Excluir mídia" });
  await deleteButton.click();
  const modal = page.locator(".confirm-dialog[open]");
  await modal.waitFor();
  expect((await modal.locator(".confirm-title").textContent()).includes("E2E still")
    && (await modal.locator('button[value="ok"]').textContent()).trim() === "Excluir mídia"
    && await modal.evaluate((node) => node.classList.contains("is-danger")),
    "deleting asks first in the portal's dialog, naming the item and the action in red");
  await modal.locator('button[value="cancel"]').click();
  await modal.waitFor({ state: "detached" });
  expect(page.url().endsWith("/edit/"), "cancelling the dialog keeps the media");
  await deleteButton.click();
  await Promise.all([page.waitForNavigation(), page.locator('.confirm-dialog[open] button[value="ok"]').click()]);
  expect(page.url().endsWith("/studio/") && await page.locator(".row-title", { hasText: "E2E still" }).count() === 0, "media deleted from the edit page");

  // ------------------------------------------------------------ embed
  await page.goto(`${BASE}/embed/${uid}/?controls=0&title=1`);
  expect(await page.locator(".embed-title").isVisible(), "embed shows the optional title");
  expect(await page.locator(".player[data-controls='0'] .pl-controls:visible").count() === 0, "embed honours controls=0");

  // ------------------------------------------------------------ phone layout
  const phone = await browser.newContext({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    colorScheme: "dark", storageState: await context.storageState(),
  });
  const mobile = await phone.newPage();
  watchErrors(mobile, errors);
  for (const url of ["/", `/view/${uid}/`, "/upload/", "/studio/", "/playlists/", `/view/${uid}/edit/`]) {
    await noHorizontalScroll(mobile, url);
  }
  await mobile.goto(`${BASE}/`);
  await mobile.locator('.topbar [data-action="toggle-sidebar"]').click();
  await mobile.waitForTimeout(400);
  expect(await mobile.evaluate(() => document.body.classList.contains("drawer-open")), "hamburger opens the navigation drawer");
  await shot(mobile, "phone-drawer");
  await phone.close();

  // ------------------------------------------------------------ manager changes the new account's role
  if (process.env.E2E_MANAGER_EMAIL) {
    const admin = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const desk = await admin.newPage();
    watchErrors(desk, errors);
    await desk.goto(`${BASE}/accounts/login/`);
    await desk.fill('input[name="login"]', process.env.E2E_MANAGER_EMAIL);
    await desk.fill('input[name="password"]', process.env.E2E_MANAGER_PASSWORD || "");
    await Promise.all([desk.waitForNavigation(), desk.click('.auth-card button[type="submit"]')]);
    await desk.goto(`${BASE}/manage/?tab=users`);
    const row = desk.locator("tr", { hasText: EMAIL });
    await Promise.all([desk.waitForNavigation(), row.locator('select[name="role"]').selectOption("advanced")]);
    expect(await desk.locator("tr", { hasText: EMAIL }).locator('select[name="role"]').inputValue() === "advanced",
      "manager promotes the new account from the manage page");
    await admin.close();
  } else {
    console.log("  - manage step skipped (set E2E_MANAGER_EMAIL / E2E_MANAGER_PASSWORD)");
  }

  // ------------------------------------------------------------ sign out
  await page.goto(`${BASE}/`);
  await page.click(".user-chip");
  await Promise.all([page.waitForNavigation(), page.locator(".menu-item", { hasText: "Sair" }).click()]);
  expect(await page.locator(".user-chip").count() === 0, "sign out ends the session");

  await browser.close();
  if (errors.length) fail(`browser reported errors:\n    ${errors.join("\n    ")}`);
  expect(browserDialogs.length === 0, `no browser dialog window was used (${browserDialogs.join(" | ") || "none"})`);
  ok("no JavaScript errors in any page");
  console.log(`\nAll ${step} checks passed.`);
}

main().catch((error) => {
  console.error(`\n  ✗ ${error.message}`);
  process.exit(1);
});
