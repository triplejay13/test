#!/usr/bin/env node
/* Simple whatsapp-web.js bridge
 * POST /send {"text":"...","groupName":"Phantoms"}
 */

const http = require("http");
const qrcode = require("qrcode-terminal");
const { Client, LocalAuth } = require("whatsapp-web.js");

const PORT = Number(process.env.WA_BRIDGE_PORT || 8787);

function json(res, code, body) {
  const payload = JSON.stringify(body);
  res.writeHead(code, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

function norm(s) {
  return String(s || "").trim().toLowerCase();
}

let ready = false;
let client = null;
let readyWaiters = [];

function notifyReady() {
  for (const fn of readyWaiters) fn();
  readyWaiters = [];
}

function waitForReady(timeoutMs = 60000) {
  if (ready) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error(`WhatsApp not ready after ${timeoutMs}ms`)),
      timeoutMs
    );
    readyWaiters.push(() => {
      clearTimeout(timeout);
      resolve();
    });
  });
}

function isDetachedFrameError(err) {
  return String(err?.message || "").toLowerCase().includes("detached frame");
}

async function initClient() {
  ready = false;
  client = new Client({
    authStrategy: new LocalAuth({ clientId: "blackbox-phantom-bridge" }),
    puppeteer: {
      headless: true,
      args: ["--no-sandbox", "--disable-setuid-sandbox"],
    },
  });

  client.on("qr", (qr) => {
    console.log("[WA] Scan this QR in WhatsApp:");
    qrcode.generate(qr, { small: true });
  });

  client.on("authenticated", () => console.log("[WA] Authenticated"));
  client.on("ready", () => {
    ready = true;
    console.log("[WA] Ready");
    notifyReady();
  });
  client.on("auth_failure", (msg) => console.error("[WA] Auth failure:", msg));
  client.on("disconnected", (reason) => {
    ready = false;
    console.error("[WA] Disconnected:", reason);
  });

  await client.initialize();
}

async function restartClient() {
  console.log("[WA] Restarting client...");
  try {
    if (client) await client.destroy();
  } catch {}
  await initClient();
  await waitForReady(90000);
}

async function sendToGroup(groupName, text) {
  try {
    const chats = await client.getChats();
    const group = chats.find((c) => c.isGroup && norm(c.name) === norm(groupName));
    if (!group) throw new Error(`Group not found: ${groupName}`);
    await client.sendMessage(group.id._serialized, text);
  } catch (err) {
    if (!isDetachedFrameError(err)) throw err;
    console.warn("[WA] Detached frame detected, recovering client and retrying once...");
    await restartClient();
    const chats = await client.getChats();
    const group = chats.find((c) => c.isGroup && norm(c.name) === norm(groupName));
    if (!group) throw new Error(`Group not found after recovery: ${groupName}`);
    await client.sendMessage(group.id._serialized, text);
  }
}

const server = http.createServer((req, res) => {
  if (req.method !== "POST" || req.url !== "/send") {
    return json(res, 404, { ok: false, error: "Not found" });
  }

  let body = "";
  req.on("data", (chunk) => {
    body += chunk;
    if (body.length > 1_000_000) req.destroy();
  });

  req.on("end", async () => {
    if (!ready) return json(res, 503, { ok: false, error: "WhatsApp not ready" });

    try {
      const parsed = JSON.parse(body || "{}");
      const text = String(parsed.text || "").trim();
      const groupName = String(parsed.groupName || "").trim();

      if (!text) return json(res, 400, { ok: false, error: "Missing text" });
      if (!groupName) return json(res, 400, { ok: false, error: "Missing groupName" });

      await sendToGroup(groupName, text);
      return json(res, 200, { ok: true });
    } catch (err) {
      return json(res, 500, { ok: false, error: String(err.message || err) });
    }
  });
});

initClient().catch((err) => {
  console.error("[WA] Initialize failed:", err);
  process.exit(1);
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[WA] Webhook bridge listening on http://127.0.0.1:${PORT}/send`);
});
