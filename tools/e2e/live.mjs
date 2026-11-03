// End-to-end test of lives, driven in a real browser against a real ingest
// server, with ffmpeg standing in for OBS.
//
// A host creates a live in the studio, an anonymous viewer waits on its page,
// the encoder connects and the page switches to the stream by itself, a
// signed-in viewer and the host chat, the host hides a message, the encoder
// drops and comes back, the host ends the live, and its recording becomes a
// video that the same embed code then plays. Then a second live goes on air
// from the control room itself, streaming Chromium's fake camera and
// microphone from the browser. Text selectors follow the default interface
// language, Brazilian Portuguese.
//
// Needs the portal with its LIVE_* settings pointing at the ingest server
// (docker/live), the ingest server itself, ffmpeg on PATH and two accounts
// (the ones `manage.py seed_demo` creates work):
//
//   BASE_URL=http://127.0.0.1:8000 \
//   E2E_HOST_EMAIL=studio@example.com E2E_VIEWER_EMAIL=ana@example.com \
//   E2E_PASSWORD=demo-pass-123 node tools/e2e/live.mjs
//
// Optional: E2E_VIDEO=<clip.mp4> to stream your own file, E2E_SHOTS=<dir> for
// screenshots.
import { execFileSync, spawn } from "node:child_process";
import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const SHOTS = process.env.E2E_SHOTS || "";
const HOST = process.env.E2E_HOST_EMAIL || "studio@example.com";
const VIEWER = process.env.E2E_VIEWER_EMAIL || "ana@example.com";
const PASSWORD = process.env.E2E_PASSWORD || "demo-pass-123";
const stamp = Date.now().toString(36);
const TITLE = `Aula ao vivo E2E ${stamp}`;

let step = 0;
function ok(message) { console.log(`  ✓ ${String(++step).padStart(2, "0")} ${message}`); }
function fail(message) { throw new Error(message); }
function expect(condition, message) { if (!condition) fail(message); ok(message); }

function clip() {
  if (process.env.E2E_VIDEO) return process.env.E2E_VIDEO;
  const path = join(mkdtempSync(join(tmpdir(), "akon-live-e2e-")), "clip.mp4");
  execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=12", "-f", "lavfi", "-i", "sine=frequency=330:duration=12",
    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path]);
  return path;
}

/* ffmpeg publishing like OBS: H.264 with a 2 s keyframe interval, AAC. */
function encoder(source, target) {
  const proc = spawn("ffmpeg", ["-hide_banner", "-loglevel", "error", "-re", "-stream_loop", "-1", "-i", source,
    "-c:v", "libx264", "-preset", "veryfast", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
    "-c:a", "aac", "-f", "flv", target], { stdio: ["ignore", "ignore", "pipe"] });
  proc.stderr.on("data", () => {});
  return proc;
}

async function shot(page, name) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: join(SHOTS, `${name}.png`) });
}

function watchErrors(page, bucket) {
  page.on("pageerror", (error) => bucket.push(`pageerror ${page.url()}: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const where = (message.location() && message.location().url) || "";
    // The stream's playlist is missing while it starts and while the encoder
    // reconnects; the player retries on purpose.
    if (/\/hls\//.test(where) && /Failed to load resource/.test(message.text())) return;
    bucket.push(`console ${page.url()}: ${message.text()} ${where}`);
  });
}

async function signIn(page, email) {
  await page.goto(`${BASE}/accounts/login/`);
  await page.fill('input[name="login"]', email);
  await page.fill('input[name="password"]', PASSWORD);
  await Promise.all([page.waitForNavigation(), page.click('.auth-card button[type="submit"]')]);
}

function localInput(date) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

/* Answers "yes" in the portal's confirm dialog (app.js). */
async function confirmIn(page) {
  await page.locator('.confirm-dialog[open] button[value="ok"]').click();
}

/* Picks a day and a time in the portal's calendar (datepicker.js), as a person would. */
async function pickDateTime(page, when) {
  await page.click(".dtp-trigger");
  const panel = page.locator(".dtp-panel:not([hidden])");
  await panel.waitFor();
  const day = new Date(when.getFullYear(), when.getMonth(), when.getDate()).getTime();
  for (let i = 0; i < 2 && await panel.locator(`.dtp-day[data-time="${day}"]`).count() === 0; i++) {
    await panel.locator('.dtp-nav[data-step="1"]').click();
  }
  await panel.locator(`.dtp-day[data-time="${day}"]`).click();
  await panel.locator(".dtp-hour").selectOption(String(when.getHours()));
  await panel.locator(".dtp-minute").selectOption(String(when.getMinutes()));
  await panel.locator('[data-action="done"]').click();
  await panel.waitFor({ state: "hidden" });
}

async function playing(page, timeout = 30000) {
  await page.waitForFunction(() => {
    const video = document.querySelector("[data-live-player] video");
    if (!video || video.paused || video.readyState < 3) return false;
    window.__liveT = window.__liveT || video.currentTime;
    return video.currentTime > window.__liveT + 1;
  }, null, { timeout, polling: 250 });
  await page.evaluate(() => { window.__liveT = 0; });
}

async function main() {
  const errors = [];
  // A fake camera and microphone, granted without asking, for the browser
  // streaming part.
  const browser = await chromium.launch({
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
  });
  const source = clip();
  let encoderProcess = null;
  console.log(`QQplay live test against ${BASE} — host ${HOST}, viewer ${VIEWER}`);

  try {
    // ---------------------------------------------------------- the host
    const hostContext = await browser.newContext({
      viewport: { width: 1440, height: 960 },
      permissions: ["camera", "microphone"],
    });
    const host = await hostContext.newPage();
    watchErrors(host, errors);
    const browserDialogs = [];
    host.on("dialog", (dialog) => { browserDialogs.push(dialog.message()); dialog.accept(); });
    await signIn(host, HOST);

    await host.goto(`${BASE}/studio/lives/new/`);
    await host.fill('input[name="title"]', TITLE);
    await host.fill('textarea[name="description"]', "Teste de ponta a ponta das lives.");
    // A start a few minutes ahead, on the calendar's five-minute steps.
    const startsAt = new Date(Math.ceil((Date.now() + 2 * 60 * 1000) / 300000) * 300000);
    await pickDateTime(host, startsAt);
    const clock = `${String(startsAt.getHours()).padStart(2, "0")}:${String(startsAt.getMinutes()).padStart(2, "0")}`;
    expect(await host.inputValue('input[name="scheduled_for"]') === localInput(startsAt)
      && (await host.textContent(".dtp-value")).includes(clock),
      `the calendar fills in the start (${(await host.textContent(".dtp-value")).trim()})`);
    await Promise.all([host.waitForNavigation(), host.click('form.form-grid button[type="submit"]')]);
    expect(/\/studio\/lives\/[a-z0-9]+\/$/.test(host.url()), "live created; the control room opens");
    const liveUid = host.url().split("/").filter(Boolean).pop();
    const server = await host.inputValue("#ingest-url");
    const key = await host.inputValue("#encoder-key");
    expect(server.startsWith("rtmp://") && /\?key=/.test(key), "control room shows the RTMP server and the stream key");
    expect(await host.getAttribute("#encoder-key", "type") === "password", "the stream key is hidden until asked for");
    await shot(host, "control-waiting");

    // -------------------------------------------------- waiting viewers
    const anonContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const viewer = await anonContext.newPage();
    watchErrors(viewer, errors);
    await viewer.goto(`${BASE}/live/${liveUid}/`);
    await viewer.waitForSelector('[data-stage="scheduled"]:not([hidden])');
    const countdown = await viewer.textContent("[data-live-countdown]");
    expect(/Começa/.test(countdown), `viewer waits with a countdown ("${countdown.trim()}")`);
    expect(await viewer.isVisible('[data-chat-when="sign-in"]'), "anonymous viewers are invited to sign in to chat");
    await shot(viewer, "viewer-waiting");

    const memberContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const member = await memberContext.newPage();
    watchErrors(member, errors);
    await signIn(member, VIEWER);
    await member.goto(`${BASE}/live/${liveUid}/`);

    // ------------------------------------------------------------ on air
    encoderProcess = encoder(source, `${server}/${key}`);
    await viewer.waitForSelector('[data-stage="on-air"]:not([hidden])', { timeout: 30000 });
    await playing(viewer);
    expect(true, "the waiting page switched to the stream by itself and it plays");
    expect(await viewer.isVisible(".pl-live-btn"), "the player shows the LIVE button");
    expect((await viewer.textContent("[data-live-status]")).trim() === "Ao vivo", "status reads “Ao vivo”");
    await shot(viewer, "viewer-live");

    await host.waitForFunction(() => document.querySelector("[data-control-publishing]").classList.contains("is-on"),
      null, { timeout: 20000 });
    ok("control room sees the encoder connected");
    await host.waitForFunction(() => {
      const node = document.querySelector("[data-live-viewers]");
      return node && !node.hidden && /assistindo/.test(node.textContent) && parseInt(node.textContent, 10) >= 1;
    }, null, { timeout: 20000 });
    ok(`control room counts viewers (${(await host.textContent("[data-live-viewers]")).trim()})`);

    // --------------------------------------------------------------- chat
    const question = `Pergunta ${stamp}: dá para repetir o último slide?`;
    await member.waitForSelector("[data-chat-form]:not([hidden]) textarea");
    await member.fill("[data-chat-form] textarea", question);
    await member.press("[data-chat-form] textarea", "Enter");
    await member.waitForSelector(`.chat-msg.is-mine >> text=${question}`);
    ok("a signed-in viewer posts with Enter");
    await viewer.waitForSelector(`.chat-msg >> text=${question}`, { timeout: 15000 });
    ok("the message reaches the other viewers with the next poll");

    const answer = "Claro! Voltando ao slide anterior.";
    await host.fill("[data-chat-form] textarea", answer);
    await host.click('[data-chat-form] button[type="submit"]');
    await viewer.waitForSelector(".chat-msg.is-host >> text=Claro!", { timeout: 15000 });
    expect((await viewer.textContent(".chat-msg.is-host .chat-host")).trim() === "Apresentador",
      "the host's message carries the host badge");

    const spam = `spam ${stamp}`;
    await member.waitForTimeout(2100); // one message every two seconds
    await member.fill("[data-chat-form] textarea", spam);
    await member.press("[data-chat-form] textarea", "Enter");
    await host.waitForSelector(`.chat-msg >> text=${spam}`, { timeout: 15000 });
    const spamItem = host.locator(".chat-msg", { hasText: spam });
    await spamItem.hover();
    await spamItem.locator("[data-chat-hide]").click();
    await host.waitForSelector(`.chat-msg >> text=${spam}`, { state: "detached" });
    await viewer.waitForSelector(`.chat-msg >> text=${spam}`, { state: "detached", timeout: 15000 });
    ok("the host hides a message and it disappears for everyone");
    await shot(viewer, "viewer-chat");

    // ---------------------------------------------- the encoder drops out
    encoderProcess.kill("SIGKILL");
    await viewer.waitForSelector('[data-when-status="interrupted"]:not([hidden])', { timeout: 20000 });
    ok("viewers see that the stream was interrupted");
    await shot(viewer, "viewer-interrupted");
    encoderProcess = encoder(source, `${server}/${key}`);
    await viewer.waitForSelector('[data-when-status="interrupted"]', { state: "hidden", timeout: 30000 });
    await playing(viewer, 40000);
    ok("the encoder reconnects and the same page plays again");

    // ------------------------------------------------------------- the end
    await host.click('form[action$="/end/"] button');
    expect(await host.locator(".confirm-dialog[open].is-danger").count() === 1,
      "ending the live asks first, in the portal's dialog");
    await Promise.all([host.waitForNavigation(), confirmIn(host)]);
    expect((await host.textContent("[data-live-status]")).trim() === "Encerrada", "the host ends the live");
    await new Promise((resolve) => {
      if (encoderProcess.exitCode !== null) return resolve();
      encoderProcess.once("exit", resolve);
      setTimeout(resolve, 15000);
    });
    expect(encoderProcess.exitCode !== null, "ending the live cuts the encoder off");
    encoderProcess = null;

    await viewer.waitForSelector('[data-stage="ended"]:not([hidden])', { timeout: 20000 });
    ok("the viewer's page shows that the live has ended");
    await viewer.waitForSelector("[data-recording-link]:not([hidden])", { timeout: 180000 });
    ok("the recording was joined, encoded and linked from the live's page");
    await shot(viewer, "viewer-ended");
    expect(await viewer.isHidden("[data-chat-form]"), "the chat is closed after the live");

    await Promise.all([viewer.waitForNavigation(), viewer.click("[data-recording-link]")]);
    expect(/\/view\/[a-z0-9]+\/$/.test(viewer.url()), "the recording opens as a regular video");
    await viewer.waitForFunction(() => window.AkonPlayer && AkonPlayer.players[0] && AkonPlayer.players[0].levels().length >= 1,
      null, { timeout: 20000 });
    ok("the recording plays from its own HLS ladder");
    const recordingUid = viewer.url().split("/").filter(Boolean).pop();

    const embed = await anonContext.newPage();
    watchErrors(embed, errors);
    await embed.goto(`${BASE}/embed/live/${liveUid}/`);
    expect(embed.url().endsWith(`/embed/${recordingUid}/`), "the live's embed code now plays the recording");
    await embed.close();

    await host.goto(`${BASE}/studio/lives/`);
    expect(await host.isVisible(`tr:has-text("${TITLE}")`), "the live is listed in the studio with its recording");
    await shot(host, "studio-lives");

    // ------------------------------------------- streaming from the browser
    await host.goto(`${BASE}/studio/lives/new/`);
    await host.fill('input[name="title"]', `${TITLE} — pelo navegador`);
    await Promise.all([host.waitForNavigation(), host.click('form.form-grid button[type="submit"]')]);
    const castUid = host.url().split("/").filter(Boolean).pop();
    await host.click('[data-source-tab="browser"]');
    expect(await host.isVisible("[data-cast-start]"), "the control room offers streaming from the browser");
    await host.click("[data-cast-start]");
    await host.waitForFunction(() => /No ar/.test(document.querySelector("[data-cast-status]").textContent),
      null, { timeout: 30000 });
    ok("the host goes on air from the browser with the camera and microphone");
    await host.waitForFunction(() => document.querySelector("[data-control-publishing]").classList.contains("is-on"),
      null, { timeout: 30000 });
    ok("the ingest server receives the browser's stream like an encoder's");

    await viewer.goto(`${BASE}/live/${castUid}/`);
    await viewer.waitForSelector('[data-stage="on-air"]:not([hidden])', { timeout: 30000 });
    await playing(viewer, 40000);
    ok("viewers watch the stream sent by the browser");
    await shot(host, "control-browser");

    await host.click("[data-cast-stop]");
    await host.waitForFunction(() => /parada/i.test(document.querySelector("[data-cast-status]").textContent),
      null, { timeout: 20000 });
    await viewer.waitForSelector('[data-when-status="interrupted"]:not([hidden])', { timeout: 30000 });
    ok("stopping in the browser interrupts the live, as stopping OBS would");
    await host.click('form[action$="/end/"] button');
    await Promise.all([host.waitForNavigation(), confirmIn(host)]);
    await viewer.waitForSelector("[data-recording-link]:not([hidden])", { timeout: 180000 });
    expect(browserDialogs.length === 0, "no browser dialog window was used");
    ok("the recording of the browser stream is published as a video too");

    await hostContext.close();
    await anonContext.close();
    await memberContext.close();
  } finally {
    if (encoderProcess) encoderProcess.kill("SIGKILL");
    await browser.close();
  }

  if (errors.length) {
    console.error("\nJavaScript errors:\n  " + errors.join("\n  "));
    process.exit(1);
  }
  ok("no JavaScript errors in any page");
  console.log(`\nAll ${step} checks passed.`);
}

main().catch((error) => {
  console.error(`\n✗ ${error.message}`);
  process.exit(1);
});
