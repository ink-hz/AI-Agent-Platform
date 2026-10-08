import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";
import { createServer } from "vite";

const sessionKey = "admin:verified-mobile-check";
const summary = {
  session_key: sessionKey,
  agent_id: "ai-admin-agent",
  source_kind: "admin",
  channel: "admin",
  title: "会议室怎么预约？",
  created_at: "2026-10-08T08:00:00Z",
  last_active_at: "2026-10-08T08:01:00Z",
  turn_count: 1,
  feedback_count: 0,
  review_count: 0,
  latest_outcome: "resolved",
  source_synced_at: "2026-10-08T08:02:00Z",
  freshness: "fresh",
  participant_count: 1,
  primary_sender_name: "测试员工",
  primary_sender_department: "行政部",
  sender_identity_status: "resolved",
};

const server = await createServer({
  server: { host: "127.0.0.1", port: 0, strictPort: false },
});
let browser;
try {
  await server.listen();
  const address = server.httpServer?.address();
  assert(address && typeof address !== "string");
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_EXECUTABLE_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  });
  const page = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body = {};
    if (path === "/api/sessions") {
      body = { items: [summary], total: 1, limit: 50, offset: 0 };
    } else if (path === `/api/sessions/${encodeURIComponent(sessionKey)}`) {
      body = { ...summary, turns: [] };
    } else if (path === "/api/agents") {
      body = [];
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  const base = `http://127.0.0.1:${address.port}`;
  await page.goto(`${base}/admin/sessions`, { waitUntil: "networkidle" });
  const row = page.locator(".session-row");
  await row.waitFor();
  assert.match(await row.innerText(), /测试员工 · 行政部/);
  assert((await row.boundingBox())?.width <= 375);
  const output = await mkdtemp(join(tmpdir(), "platform-session-mobile-"));
  const listScreenshot = join(output, "list-375.png");
  await page.screenshot({ path: listScreenshot, fullPage: true });

  await row.click();
  await page.locator(".session-detail-head").waitFor();
  assert.match(await page.locator(".session-detail-head").innerText(), /提问人\s*测试员工 · 行政部/);
  const detailScreenshot = join(output, "detail-375.png");
  await page.screenshot({ path: detailScreenshot, fullPage: true });
  const width = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
  assert(width.document <= width.viewport, `detail overflow: ${JSON.stringify(width)}`);
  process.stdout.write(JSON.stringify({ viewport: 375, listScreenshot, detailScreenshot, width }) + "\n");
} finally {
  await browser?.close();
  await server.close();
}
