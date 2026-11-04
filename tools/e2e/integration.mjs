// End-to-end test of the school integration, with a school site of its own.
//
// A small Node server plays the school: on another origin than the portal, it
// signs its people in with the integration's secret (a token URL, as the
// studio page shows) and puts the live and a video of the library on its
// lesson pages with the script. A creator makes the integration and sets its
// look in the studio; the school's server schedules a private live for one
// class through the API; a student and a teacher of that class talk in the
// chat, the teacher wears the Streaming badge, answers a question, pins a
// message, slows the chat down and mutes the student for a moment; a student
// of another class is kept out; the two comment on a video and the teacher
// hides a comment; the school's server reads the attendance, blocks the
// student and erases their data. Text selectors follow the default
// interface language, Brazilian Portuguese, or avoid text altogether.
//
// Needs the portal running and an account that can create integrations and
// owns at least one ready video (the studio account of `manage.py seed_demo`):
//
//   BASE_URL=http://127.0.0.1:8000 E2E_HOST_EMAIL=studio@example.com \
//   E2E_PASSWORD=demo-pass-123 node tools/e2e/integration.mjs
//
// Optional: E2E_LIVE_HOOK_SECRET=<the portal's LIVE_HOOK_SECRET> puts the live
// on air with the ingest server's callback (no encoder needed), which also
// checks the attendance; E2E_SCHOOL_PORT (8765) for the school's server;
// E2E_SHOTS=<dir> for screenshots.
import { createHmac } from "node:crypto";
import { mkdirSync } from "node:fs";
import { createServer } from "node:http";
import { join } from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const HOST = process.env.E2E_HOST_EMAIL || "studio@example.com";
const PASSWORD = process.env.E2E_PASSWORD || "demo-pass-123";
const HOOK = process.env.E2E_LIVE_HOOK_SECRET || "";
const PORT = Number(process.env.E2E_SCHOOL_PORT || 8765);
const SCHOOL = `http://127.0.0.1:${PORT}`;
const SHOTS = process.env.E2E_SHOTS || "";
const stamp = Date.now().toString(36);
const WAIT = 30000; // a poll of a scheduled live comes every 10 s

let step = 0;
function ok(message) { console.log(`  ✓ ${String(++step).padStart(2, "0")} ${message}`); }
function fail(message) { throw new Error(message); }
function expect(condition, message) { if (!condition) fail(message); ok(message); }

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
    // With no ingest server, the stream's playlist never comes; the player
    // retries on purpose. Refusals checked below show as failed loads too.
    if (/\/hls\//.test(where) || /Failed to load resource/.test(message.text())) return;
    bucket.push(`console ${page.url()}: ${message.text()} ${where}`);
  });
}

// ------------------------------------------------------------ the school

const b64 = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");

/* The token the school's server makes, as on the studio page. */
function sign(claims, key, secret) {
  const header = b64({ alg: "HS256", typ: "JWT", kid: key });
  const body = b64(claims);
  const signature = createHmac("sha256", secret).update(`${header}.${body}`).digest("base64url");
  return `${header}.${body}.${signature}`;
}

const PEOPLE = {
  ana: { id: `ana-${stamp}`, name: "Ana Souza", classes: ["turma-a"] },
  rui: { id: `rui-${stamp}`, name: "Prof. Rui", classes: ["turma-a"], teacher: true },
  bia: { id: `bia-${stamp}`, name: "Bia Lima", classes: ["turma-z"] },
};

function school(state) {
  const page = (title, widget) => `<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title} · Escola Virtual</title>
<style>
  body { margin: 0; font: 15px/1.5 system-ui, sans-serif; background: #f4f1ea; color: #2b2a26; }
  header { display: flex; align-items: center; gap: 12px; padding: 14px 28px; background: #1f3a2e; color: #fff; }
  header b { font-size: 17px; }
  main { max-width: 1180px; margin: 0 auto; padding: 22px 28px; }
  h1 { margin: 0 0 14px; font-size: 22px; }
</style></head>
<body><header><b>Escola Virtual</b><span>${title}</span></header>
<main><h1>${title}</h1>${widget}</main>
<script src="${BASE}/embed/sdk.js" async></script></body></html>`;

  return createServer((request, response) => {
    const url = new URL(request.url, SCHOOL);
    const who = PEOPLE[url.searchParams.get("who")] ? url.searchParams.get("who") : "";
    const send = (status, type, body) => {
      response.writeHead(status, { "Content-Type": type, "Cache-Control": "no-store" });
      response.end(body);
    };
    if (url.pathname === "/token" && who) {
      const person = PEOPLE[who];
      const now = Math.floor(Date.now() / 1000);
      const token = sign({ sub: person.id, name: person.name, moderator: Boolean(person.teacher),
        groups: person.classes, iat: now, exp: now + 600 }, state.key, state.secret);
      return send(200, "application/json", JSON.stringify({ token }));
    }
    if (url.pathname === "/aula" && who) {
      return send(200, "text/html; charset=utf-8", page("Aula ao vivo",
        `<div data-qqplay-live="${state.live}" data-layout="both" data-token-url="/token?who=${who}"></div>`));
    }
    if (url.pathname === "/video" && who) {
      return send(200, "text/html; charset=utf-8", page("Revisão em vídeo",
        `<div data-qqplay-video="${state.video}" data-layout="both" data-token-url="/token?who=${who}"></div>`));
    }
    return send(404, "text/plain", "not found");
  });
}

// --------------------------------------------------------------- the API

function apiToken(state) {
  const now = Math.floor(Date.now() / 1000);
  return sign({ scope: "api", iat: now, exp: now + 300 }, state.key, state.secret);
}

async function api(state, method, path, body, token) {
  const response = await fetch(`${BASE}/api/integrations/v1/${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token === undefined ? apiToken(state) : token}`,
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  return { status: response.status, data };
}

async function hook(event, state) {
  const [name, query] = state.streamKey.split("?");
  const key = new URLSearchParams(query).get("key");
  const response = await fetch(`${BASE}/live/hooks/${HOOK}/${event}/`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ name, key, addr: "127.0.0.1" }).toString(),
  });
  return response.status;
}

// ---------------------------------------------------------------- helpers

async function signIn(page, email) {
  await page.goto(`${BASE}/accounts/login/`);
  await page.fill('input[name="login"]', email);
  await page.fill('input[name="password"]', PASSWORD);
  await Promise.all([page.waitForNavigation(), page.click('.auth-card button[type="submit"]')]);
}

/* Answers "yes" in the portal's confirm dialog (app.js). */
async function confirmIn(page) {
  await page.locator('.confirm-dialog[open] button[value="ok"]').click();
}

/* A person of the school on a lesson page, and the QQplay frame in it. */
async function lesson(browser, errors, who, path = "aula") {
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await context.newPage();
  watchErrors(page, errors);
  await page.goto(`${SCHOOL}/${path}?who=${who}`);
  const frame = page.frameLocator("iframe");
  return { context, page, frame };
}

async function signedIn(frame, name) {
  await frame.locator("[data-chat-who] strong").filter({ hasText: name }).waitFor({ timeout: WAIT });
}

async function send(frame, text, question = false) {
  if (question) await frame.locator("[data-chat-ask]").click();
  await frame.locator("[data-chat-form] textarea").fill(text);
  await frame.locator('[data-chat-form] button[type="submit"]').click();
}

const message = (frame, text) => frame.locator(".chat-list .chat-msg").filter({ hasText: text }).last();

/* A moderator's tool on a message: hover it, then click. */
async function tool(frame, text, selector) {
  const item = message(frame, text);
  await item.hover();
  await item.locator(selector).click();
}

// ------------------------------------------------------------------ run

async function main() {
  const errors = [];
  const state = { key: "", secret: "", live: "", video: "", streamKey: "" };
  const server = school(state);
  await new Promise((resolve) => server.listen(PORT, "127.0.0.1", resolve));
  const browser = await chromium.launch();
  let studio;
  let onAir = false;
  try {
    // ------------------------------------------------ the creator's studio
    studio = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    watchErrors(studio, errors);
    await signIn(studio, HOST);
    await studio.goto(`${BASE}/studio/integrations/`);
    await studio.fill('input[name="name"]', `Escola Virtual E2E ${stamp}`);
    await studio.fill('textarea[name="origins"]', SCHOOL);
    await Promise.all([studio.waitForNavigation(), studio.click('form.form-grid button[type="submit"]')]);
    state.key = await studio.inputValue("#integration-key");
    state.secret = await studio.inputValue("#integration-secret");
    expect(state.key.startsWith("sch_") && state.secret.length > 20, "the studio creates the integration, with its key and secret");
    const detailUrl = studio.url();

    const look = studio.locator("form", { has: studio.locator('input[name="chat_title"]:not([type="hidden"])') });
    await look.locator('input[name="accent_color"][data-color-text]').fill("#7c3aed");
    await look.locator('input[name="chat_title"]').fill("Sala de aula");
    await Promise.all([studio.waitForNavigation(), look.locator('button[type="submit"]').click()]);
    expect(await studio.locator('input[name="chat_title"]:not([type="hidden"])').inputValue() === "Sala de aula",
      "the look of the frames is saved (colour and the chat's title)");

    // ------------------------------------------------ the school's server
    expect((await api(state, "GET", "lives/", undefined, "")).status === 401, "the API refuses a request without a token");
    const personToken = sign({ sub: "x", name: "X", exp: Math.floor(Date.now() / 1000) + 60 }, state.key, state.secret);
    expect((await api(state, "GET", "lives/", undefined, personToken)).status === 401, "...and a person's token");
    const created = await api(state, "POST", "lives/", {
      title: `Revisão para a prova ${stamp}`, classes: ["turma-a"], chat: true,
      scheduled_for: new Date(Date.now() + 3600 * 1000).toISOString(),
    });
    expect(created.status === 201 && created.data.visibility === "private" && created.data.classes[0] === "turma-a",
      "the school's server schedules a private live for one class");
    state.live = created.data.id;
    state.streamKey = created.data.stream.key;
    const patched = await api(state, "PATCH", `lives/${state.live}/`, { description: "Traga suas dúvidas." });
    expect(patched.status === 200 && patched.data.description === "Traga suas dúvidas.", "...and changes it");
    const videos = await api(state, "GET", "videos/");
    const video = (videos.data.videos || []).find((item) => item.kind === "video");
    expect(Boolean(video), "the API lists the creator's videos");
    state.video = video.id;

    if (HOOK) {
      onAir = (await hook("publish", state)) === 200;
      expect(onAir, "the encoder connects (the ingest server's callback): the live is on air");
    }

    // ------------------------------------------------ a student and a teacher
    const ana = await lesson(browser, errors, "ana");
    await signedIn(ana.frame, "Ana Souza");
    expect(true, "the school signs the student in inside the frame, with no QQplay account");
    expect((await ana.frame.locator(".live-chat h2").textContent()).includes("Sala de aula"), "the chat carries the school's title");
    await send(ana.frame, "Bom dia, turma!");
    await message(ana.frame, "Bom dia, turma!").waitFor();
    await ana.page.waitForTimeout(2100); // one message every 2 s per person
    await send(ana.frame, "Vai cair na prova?", true);
    await ana.frame.locator(".chat-msg.is-question").filter({ hasText: "Vai cair na prova?" }).waitFor();
    await ana.frame.locator("[data-questions-count]:not([hidden])").filter({ hasText: /^1$/ }).waitFor({ timeout: WAIT });
    expect(true, "the student talks and asks a question, listed apart for the host");
    await shot(ana.page, "integration-student");

    const rui = await lesson(browser, errors, "rui");
    await signedIn(rui.frame, "Prof. Rui");
    await message(rui.frame, "Bom dia, turma!").waitFor({ timeout: WAIT });
    await send(rui.frame, "Bem-vindos! Já começamos.");
    const badge = message(rui.frame, "Bem-vindos!").locator(".chat-host");
    await badge.waitFor();
    expect(await badge.textContent() === "Streaming", "the teacher wears the Streaming badge");
    await message(ana.frame, "Bem-vindos!").locator(".chat-host").waitFor({ timeout: WAIT });
    expect(true, "...and the student sees it");

    await rui.frame.locator('[data-chat-tab="questions"]').click();
    await rui.frame.locator("[data-question]").filter({ hasText: "Vai cair na prova?" }).locator("[data-chat-answer]").click();
    await ana.frame.locator(".chat-msg.is-answered").filter({ hasText: "Vai cair na prova?" }).waitFor({ timeout: WAIT });
    expect(true, "the teacher answers the question, and the student sees it answered");
    await rui.frame.locator('[data-chat-tab="chat"]').click();

    await tool(rui.frame, "Bom dia, turma!", '[data-chat-pin][data-pin-on="1"]');
    await ana.frame.locator("[data-chat-pinned]:not([hidden])").filter({ hasText: "Bom dia, turma!" }).waitFor({ timeout: WAIT });
    expect(true, "the teacher pins a message over everyone's chat");

    await rui.frame.locator("[data-chat-mod] summary").click();
    await rui.frame.locator("[data-slow-mode]").selectOption("10");
    await ana.frame.locator("[data-chat-slow]:not([hidden])").waitFor({ timeout: WAIT });
    expect(true, "the teacher turns the slow mode on, and the student is told");
    await rui.frame.locator("[data-slow-mode]").selectOption("0");
    await ana.frame.locator("[data-chat-slow]").waitFor({ state: "hidden", timeout: WAIT });

    await tool(rui.frame, "Bom dia, turma!", "[data-chat-mute]");
    await ana.frame.locator("[data-chat-muted]:not([hidden])").waitFor({ timeout: WAIT });
    expect(await ana.frame.locator("[data-chat-form]").isHidden(), "the teacher mutes the student, whose box goes away");
    await rui.frame.locator("[data-muted-list] [data-unmute]").click();
    await ana.frame.locator("[data-chat-form]").waitFor({ state: "visible", timeout: WAIT });
    expect(true, "...and lets them talk again");
    await shot(rui.page, "integration-teacher");

    // ------------------------------------------------ another class
    const bia = await lesson(browser, errors, "bia");
    const title = bia.frame.locator("[data-awaiting-title]");
    await title.waitFor();
    const before = await title.textContent();
    await bia.frame.locator("[data-awaiting-title]").filter({ hasNotText: before }).waitFor({ timeout: WAIT });
    expect(await bia.frame.locator("[data-chat-form]").isHidden() && await bia.frame.locator(".chat-msg").count() === 0,
      "a student of another class is kept out of the live");
    await shot(bia.page, "integration-other-class");
    await bia.context.close();

    // ------------------------------------------------ attendance
    if (onAir) {
      const report = await api(state, "GET", `lives/${state.live}/attendance/`);
      const students = (report.data.attendance || []).map((row) => row.student).sort();
      expect(students.join() === [PEOPLE.ana.id, PEOPLE.rui.id].sort().join(),
        "the attendance lists the student and the teacher, with their ids at the school");
    }

    // ------------------------------------------------ a video and its comments
    const anaVideo = await lesson(browser, errors, "ana", "video");
    const anaForm = anaVideo.frame.locator("[data-comment-form]");
    await anaForm.waitFor({ state: "visible", timeout: WAIT });
    await anaForm.locator("textarea").fill("Ótima revisão, obrigada!");
    await anaForm.locator('button[type="submit"]').click();
    await anaVideo.frame.locator("[data-comment].is-mine").filter({ hasText: "Ótima revisão" }).waitFor();
    expect(true, "the student comments on a video of the library");

    const ruiVideo = await lesson(browser, errors, "rui", "video");
    const comment = ruiVideo.frame.locator("[data-comment]").filter({ hasText: "Ótima revisão" }).first();
    await comment.waitFor({ timeout: WAIT });
    await comment.locator("[data-reply]").first().click();
    const ruiForm = ruiVideo.frame.locator("[data-comment-form]");
    await ruiForm.locator("textarea").fill("Bons estudos!");
    await ruiForm.locator('button[type="submit"]').click();
    const reply = ruiVideo.frame.locator(".comment-replies [data-comment]").filter({ hasText: "Bons estudos!" });
    await reply.waitFor();
    expect(await reply.locator(".chat-host").textContent() === "Streaming", "the teacher replies, with the badge");
    await shot(ruiVideo.page, "integration-video");
    await comment.locator("[data-hide-comment]").first().click();
    await ruiVideo.frame.locator("[data-comment]").filter({ hasText: "Ótima revisão" }).waitFor({ state: "detached", timeout: WAIT });
    await anaVideo.page.reload();
    await anaVideo.frame.locator("[data-comment-form]").waitFor({ state: "visible", timeout: WAIT });
    expect(await anaVideo.frame.locator("[data-comment]").filter({ hasText: "Ótima revisão" }).count() === 0,
      "the teacher hides the comment, for everyone");
    await anaVideo.context.close();
    await ruiVideo.context.close();

    // ------------------------------------------------ blocking and erasing
    const blocked = await api(state, "POST", `students/${PEOPLE.ana.id}/block/`);
    expect(blocked.status === 200 && blocked.data.blocked, "the school's server blocks the student");
    const refused = ana.page.waitForResponse((response) => response.url().includes("/embed/api/session/"));
    await ana.page.reload();
    expect((await refused).status() === 403, "...who can no longer sign in through the school");
    await ana.frame.locator("[data-chat-form]").waitFor({ state: "hidden" });
    const erased = await api(state, "DELETE", `students/${PEOPLE.ana.id}/`);
    expect(erased.status === 200 && (await api(state, "POST", `students/${PEOPLE.ana.id}/block/`)).status === 404,
      "the school's server erases the student's data");
    await ana.context.close();
    await rui.context.close();

    // ------------------------------------------------ the end
    if (onAir) await hook("publish-done", state);
    const ended = await api(state, "POST", `lives/${state.live}/end/`);
    expect(ended.status === 200 && ended.data.status === "ended", "the school's server ends the live");
    expect((await api(state, "DELETE", `lives/${state.live}/`)).status === 200, "...and deletes it");
    state.live = "";

    await studio.goto(detailUrl);
    await studio.locator('form[action$="/delete/"] button[type="submit"]').click();
    await confirmIn(studio);
    await studio.waitForURL(`${BASE}/studio/integrations/`);
    expect(true, "the creator deletes the integration");
    state.key = "";

    if (errors.length) fail(`errors in the pages:\n${errors.join("\n")}`);
    ok("no errors in any page");
  } finally {
    if (state.live && state.key) {
      if (onAir) await hook("publish-done", state).catch(() => {});
      await api(state, "POST", `lives/${state.live}/end/`).catch(() => {});
      await api(state, "DELETE", `lives/${state.live}/`).catch(() => {});
    }
    await browser.close();
    server.close();
  }
}

console.log(`QQplay ⇄ school integration at ${BASE}, the school at ${SCHOOL}`);
main().then(
  () => console.log(`\n${step} checks passed.`),
  (error) => {
    console.error(`\n✗ ${error.message}`);
    process.exit(1);
  },
);
