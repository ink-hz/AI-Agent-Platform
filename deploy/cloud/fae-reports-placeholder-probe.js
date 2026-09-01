"use strict";

const fs = require("fs");

const PLACEHOLDER_HEADING = "分析报告尚未接入";
const PLACEHOLDER_DESCRIPTION =
  "Sessions 与问题治理可以正常使用；这里不会用演示数据代替 FAE 的真实分析结果。";

function placeholderExpression(expectedUrl) {
  const serializedUrl = JSON.stringify(expectedUrl);
  const serializedHeading = JSON.stringify(PLACEHOLDER_HEADING);
  const serializedDescription = JSON.stringify(PLACEHOLDER_DESCRIPTION);
  return `(() => {
    const expected = new URL(${serializedUrl});
    const current = new URL(window.location.href);
    if (current.href !== expected.href || current.origin !== expected.origin ||
        current.pathname !== expected.pathname || current.search !== "" || current.hash !== "") return false;
    const workbenches = document.querySelectorAll(".fae-workbench");
    const pendingMarkers = document.querySelectorAll('[data-fae-reports-state]');
    if (workbenches.length !== 1 || pendingMarkers.length !== 1 ||
        document.querySelector("article,table,[data-report-id],[data-metric],.report-card") !== null) return false;
    const workbench = workbenches[0];
    const content = workbench?.querySelector(":scope > .fae-workbench__content");
    const selected = workbench?.querySelector(':scope > .fae-workbench__sidebar a[aria-current="page"]');
    if (!content || content.children.length !== 1 || content.childNodes.length !== 1 ||
        selected?.href !== expected.href) return false;
    const placeholder = content.querySelector(':scope > [data-fae-reports-state="integration-pending"]');
    if (!placeholder || placeholder !== content.firstElementChild ||
        placeholder.getAttribute("class") !== "fae-workbench__empty" ||
        placeholder.getAttribute("role") !== "status") return false;
    const attributeNames = Array.from(placeholder.attributes, (attribute) => attribute.name).sort();
    if (attributeNames.join(",") !== "class,data-fae-reports-state,role") return false;
    const children = Array.from(placeholder.children);
    if (children.length !== 2 || children[0].tagName !== "H2" || children[1].tagName !== "P") return false;
    if (children[0].textContent?.trim() !== ${serializedHeading} ||
        children[1].textContent?.trim() !== ${serializedDescription}) return false;
    if (placeholder.textContent !== ${serializedHeading} + ${serializedDescription}) return false;
    return content.querySelector("ul,ol") === null;
  })()`;
}

function viewerDeniedExpression(expectedUrl) {
  const serializedUrl = JSON.stringify(expectedUrl);
  return `(() => {
    const expected = new URL(${serializedUrl});
    const current = new URL(window.location.href);
    if (current.href !== expected.href || current.origin !== expected.origin ||
        current.pathname !== expected.pathname || current.search !== "" || current.hash !== "") return false;
    const denied = document.querySelectorAll('section.permission-state[role="alert"]');
    if (denied.length !== 1 || document.querySelector('.fae-workbench,[data-fae-reports-state]')) return false;
    const state = denied[0];
    const attributes = Array.from(state.attributes, (attribute) => attribute.name).sort();
    const children = Array.from(state.children);
    return attributes.join(",") === "class,role" && state.childNodes.length === 2 &&
      children.length === 2 && children[0].tagName === "H1" && children[1].tagName === "P" &&
      children[0].textContent?.trim() === "无权访问" &&
      children[1].textContent?.trim() === "该页面不在你的后端授权范围内。";
  })()`;
}

module.exports = { placeholderExpression, viewerDeniedExpression };

function bounded(promise, milliseconds, label) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${label} timeout`)), milliseconds);
    }),
  ]).finally(() => clearTimeout(timer));
}

async function runProbe(socketUrl, cookiePath, requestedUrl, mode, deadlineMs, commandTimeoutMs) {
  const expected = new URL(requestedUrl);
  const socketAddress = new URL(socketUrl);
  const expectedHref = mode === "placeholder"
    ? "https://agent.orbbec.com.cn/admin/fae/reports"
    : mode === "viewer-denied" ? "https://agent.orbbec.com.cn/admin/fae" : "";
  if (expected.href !== expectedHref ||
      expected.search !== "" || expected.hash !== "" ||
      socketAddress.protocol !== "ws:" ||
      !["127.0.0.1", "localhost"].includes(socketAddress.hostname) ||
      socketAddress.username || socketAddress.password || !socketAddress.port) {
    throw new Error("probe input invalid");
  }
  if (!Number.isInteger(deadlineMs) || deadlineMs < 500 || deadlineMs > 30_000 ||
      !Number.isInteger(commandTimeoutMs) || commandTimeoutMs < 100 ||
      commandTimeoutMs > 5_000 || commandTimeoutMs >= deadlineMs) {
    throw new Error("probe deadline invalid");
  }
  const cookies = JSON.parse(fs.readFileSync(cookiePath, "utf8"));
  const cookieNames = Object.keys(cookies).sort();
  if (cookieNames.join(",") !== "__Host-platform_csrf,__Host-platform_session" ||
      !cookieNames.every((name) => typeof cookies[name] === "string" && cookies[name])) {
    throw new Error("probe cookies invalid");
  }

  const deadlineAt = Date.now() + deadlineMs;
  const socket = new WebSocket(socketAddress.href);
  const pending = new Map();
  let commandId = 0;
  const rejectPending = (error) => {
    for (const value of pending.values()) {
      clearTimeout(value.timer);
      value.reject(error);
    }
    pending.clear();
  };
  socket.onmessage = ({ data }) => {
    try {
      const message = JSON.parse(data);
      if (!message.id || !pending.has(message.id)) return;
      const value = pending.get(message.id);
      pending.delete(message.id);
      clearTimeout(value.timer);
      if (message.error) value.reject(new Error("cdp command failed"));
      else value.resolve(message.result);
    } catch (_error) {
      rejectPending(new Error("cdp response invalid"));
    }
  };
  socket.addEventListener("error", () => rejectPending(new Error("cdp connection failed")));
  socket.addEventListener("close", () => rejectPending(new Error("cdp connection closed")));

  const opened = new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener(
      "error",
      () => reject(new Error("cdp connection failed")),
      { once: true },
    );
  });
  const remaining = () => {
    const value = deadlineAt - Date.now();
    if (value <= 0) throw new Error("probe deadline exceeded");
    return value;
  };
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++commandId;
    const timeout = Math.min(commandTimeoutMs, remaining());
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error("cdp command timeout"));
    }, timeout);
    pending.set(id, { resolve, reject, timer });
    try {
      socket.send(JSON.stringify({ id, method, params }));
    } catch (_error) {
      clearTimeout(timer);
      pending.delete(id);
      reject(new Error("cdp command send failed"));
    }
  });

  try {
    await bounded(opened, Math.min(commandTimeoutMs, remaining()), "websocket open");
    await command("Network.enable");
    for (const name of ["__Host-platform_session", "__Host-platform_csrf"]) {
      const cookie = await command("Network.setCookie", {
        name,
        value: cookies[name],
        url: expected.origin,
        secure: true,
        httpOnly: name === "__Host-platform_session",
        sameSite: "Lax",
      });
      if (!cookie.success) throw new Error("cookie rejected");
    }
    await command("Page.enable");
    await command("Runtime.enable");
    await command("Page.navigate", { url: expected.href });
    while (remaining() > 0) {
      const evaluation = await command("Runtime.evaluate", {
        expression: mode === "placeholder"
          ? placeholderExpression(expected.href)
          : viewerDeniedExpression(expected.href),
        returnByValue: true,
      });
      if (evaluation.result?.value === true) {
        process.stdout.write(
          mode === "placeholder" ? "FAE_REPORTS_PLACEHOLDER_OK\n" : "FAE_VIEWER_DENIED_OK\n",
        );
        return;
      }
      await bounded(new Promise((resolve) => setTimeout(resolve, 200)), remaining(), "render wait");
    }
  } finally {
    rejectPending(new Error("probe finished"));
    try { socket.close(); } catch (_error) { /* process exits below */ }
  }
}

if (require.main === module) {
  const [socketUrl, cookiePath, requestedUrl, mode, rawDeadline, rawCommandTimeout] = process.argv.slice(2);
  runProbe(
    socketUrl,
    cookiePath,
    requestedUrl,
    mode,
    Number(rawDeadline),
    Number(rawCommandTimeout),
  ).then(
    () => process.exit(0),
    () => process.exit(1),
  );
}

module.exports.runProbe = runProbe;
