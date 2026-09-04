import { readFileSync } from "node:fs";

const TARGETS = [
  ["https://agent.orbbec.com.cn/", "platform.brain"],
  ["https://agent.orbbec.com.cn/office/?view=services", "office.services"],
  ["https://agent.orbbec.com.cn/fae/", "fae.workspace"],
  ["https://agent.orbbec.com.cn/voc/", "voc.workspace"],
  ["https://agent.orbbec.com.cn/hr/", "hr.workspace"],
  ["https://agent.orbbec.com.cn/marketing/prospecting", "marketing.workspace"],
  ["https://agent.orbbec.com.cn/admin/", "admin.overview"],
];
const EXTERNAL_FAE_URL = "https://fae.orbbec.com.cn/";

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function run(socketUrl, cookiePath, rawDeadline = "90000") {
  const socketAddress = new URL(socketUrl);
  const deadlineMs = Number(rawDeadline);
  if (socketAddress.protocol !== "ws:" || !["127.0.0.1", "localhost"].includes(socketAddress.hostname)
      || !socketAddress.port || !Number.isInteger(deadlineMs) || deadlineMs < 10_000 || deadlineMs > 180_000) {
    throw new Error("probe input invalid");
  }
  const cookies = JSON.parse(readFileSync(cookiePath, "utf8"));
  const names = Object.keys(cookies).sort();
  if (names.join(",") !== "__Host-platform_csrf,__Host-platform_session"
      || names.some((name) => typeof cookies[name] !== "string" || !cookies[name])) {
    throw new Error("probe cookies invalid");
  }

  const deadlineAt = Date.now() + deadlineMs;
  const startedAt = new Date(Date.now() - 1_000).toISOString();
  const socket = new WebSocket(socketAddress.href);
  const pending = new Map();
  let nextId = 0;
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    const waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    clearTimeout(waiter.timer);
    if (message.error) waiter.reject(new Error("cdp command failed"));
    else waiter.resolve(message.result);
  };
  const opened = new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", () => reject(new Error("cdp connection failed")), { once: true });
  });
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error("cdp command timeout")); }, 5_000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const remaining = () => {
    const value = deadlineAt - Date.now();
    if (value <= 0) throw new Error("probe deadline exceeded");
    return value;
  };
  const historyExpression = `(() => fetch('/api/v1/manage/access-events?date_from=${encodeURIComponent(startedAt)}&limit=100', {credentials:'include'})
    .then(async response => ({status:response.status, body:response.ok ? await response.json() : null}))
    .catch(() => ({status:0, body:null})))()`;
  async function readEvents() {
    const result = await command("Runtime.evaluate", { expression: historyExpression, awaitPromise: true, returnByValue: true });
    const value = result.result?.value;
    return value?.status === 200 && Array.isArray(value.body?.items) ? value.body.items : null;
  }
  async function navigateAndWait(url, pageKey) {
    await command("Page.navigate", { url });
    const targetDeadline = Date.now() + Math.min(25_000, remaining());
    while (Date.now() < targetDeadline && remaining() > 0) {
      try {
        const events = await readEvents();
        if (events?.some((event) => event.page_key === pageKey && event.occurred_at >= startedAt)) return;
      } catch { /* execution context can be replaced while navigation settles */ }
      await delay(250);
    }
    throw new Error(`page event missing: ${pageKey}`);
  }

  try {
    await Promise.race([opened, delay(5_000).then(() => { throw new Error("websocket open timeout"); })]);
    await command("Network.enable");
    for (const name of names) {
      const result = await command("Network.setCookie", {
        name, value: cookies[name], url: "https://agent.orbbec.com.cn",
        secure: true, httpOnly: name === "__Host-platform_session", sameSite: "Lax",
      });
      if (!result.success) throw new Error("cookie rejected");
    }
    await command("Page.enable");
    await command("Runtime.enable");
    for (const [url, pageKey] of TARGETS) await navigateAndWait(url, pageKey);

    const beforeExternal = await readEvents();
    if (!beforeExternal) throw new Error("owner history unavailable");
    const beforeFaeIds = new Set(beforeExternal.filter((event) => event.workspace_key === "fae").map((event) => event.access_event_id));
    await command("Page.navigate", { url: EXTERNAL_FAE_URL });
    await delay(1_500);
    await navigateAndWait("https://agent.orbbec.com.cn/admin/", "admin.overview");
    const afterExternal = await readEvents();
    const afterFaeIds = new Set((afterExternal ?? []).filter((event) => event.workspace_key === "fae").map((event) => event.access_event_id));
    if (afterFaeIds.size !== beforeFaeIds.size || [...afterFaeIds].some((id) => !beforeFaeIds.has(id))) {
      throw new Error("external FAE produced a Platform access event");
    }
    process.stdout.write(`ACCESS_HISTORY_BROWSER_OK pages=${TARGETS.length} external_fae_events=0\n`);
  } finally {
    for (const waiter of pending.values()) { clearTimeout(waiter.timer); waiter.reject(new Error("probe finished")); }
    try { socket.close(); } catch { /* process exits */ }
  }
}

const [socketUrl, cookiePath, deadline] = process.argv.slice(2);
run(socketUrl, cookiePath, deadline).then(() => process.exit(0), () => process.exit(1));
