/** @vitest-environment jsdom */
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { AgentSourceStatus } from "../types";
import { SourceFreshness } from "./SourceFreshness";

const status: AgentSourceStatus = {
  mode: "cloud_direct", publisher: "cloud", source_kind: "fae", source_instance_id: "cloud-fae",
  last_source_success_at: "2026-10-29T08:00:00Z", last_published_at: "2026-10-29T08:00:01Z",
  last_failure_at: "2026-10-29T08:00:05Z", last_error: "source_unavailable", lag_seconds: 120,
};

it("shows independent source mode, observation, publication, lag and failure", () => {
  const html = renderToStaticMarkup(<SourceFreshness freshness="stale" status={status} />);
  expect(html).toContain("云端直连");
  expect(html).toContain("source_unavailable");
  expect(html).toContain("2026-10-29T08:00:00Z");
  expect(html).toContain("2026-10-29T08:00:01Z");
  expect(html).toContain("120s");
  expect(html).toContain("freshness-stale");
});

it("supports old APIs without source status and identifies native without inventing sync health", () => {
  expect(renderToStaticMarkup(<SourceFreshness freshness="fresh" />)).toContain("最新");
  const html = renderToStaticMarkup(<SourceFreshness freshness="stale" status={{ ...status, mode: "native", publisher: "native", last_error: null }} />);
  expect(html).toContain("云端原生");
  expect(html).toContain("无需同步");
  expect(html).not.toContain("数据已过期");
});
