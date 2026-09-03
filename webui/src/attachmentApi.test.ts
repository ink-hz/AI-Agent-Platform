/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  beginAttachmentUpload,
  completeAttachmentUpload,
  issueAttachmentTicket,
  listConversationAttachments,
  parseArtifactVersion,
  parseConversationCitation,
  parseConversationReadState,
  uploadAttachmentContent,
} from "./attachmentApi";


const CONVERSATION_ID = "8c13c965-1b60-472e-b275-199987d1d109";
const ATTACHMENT_ID = "4e2ac19d-00cc-43ca-a953-f678b8bf7029";
const UPLOAD_ID = "adac44bf-cb88-4d60-bc23-492cd5fbb69f";

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function upload(state: string, uploadedBytes: number) {
  return {
    upload_id: UPLOAD_ID,
    attachment_id: ATTACHMENT_ID,
    conversation_id: CONVERSATION_ID,
    original_name: "candidate.pdf",
    declared_mime: "application/pdf",
    declared_size: 7,
    state,
    uploaded_bytes: uploadedBytes,
    expires_at: "2026-09-04T10:00:00Z",
  };
}

function attachment(state = "validating") {
  return {
    attachment_id: ATTACHMENT_ID,
    conversation_id: CONVERSATION_ID,
    original_name: "candidate.pdf",
    declared_mime: "application/pdf",
    detected_mime: "application/pdf",
    size_bytes: 7,
    state,
    created_at: "2026-09-03T10:00:00Z",
    retained_until: "2027-09-03T10:00:00Z",
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("conversation attachment API", () => {
  it("reports each server-owned upload state without inventing ready", async () => {
    const file = new File(["payload"], "candidate.pdf", { type: "application/pdf" });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(upload("uploading", 0), 201))
      .mockResolvedValueOnce(jsonResponse(upload("uploading", 7)))
      .mockResolvedValueOnce(jsonResponse(attachment("validating")));
    vi.stubGlobal("fetch", fetchMock);

    await expect(beginAttachmentUpload(CONVERSATION_ID, file, "csrf"))
      .resolves.toMatchObject({ state: "uploading", uploadedBytes: 0 });
    await expect(uploadAttachmentContent(UPLOAD_ID, file, "csrf"))
      .resolves.toMatchObject({ state: "uploading", uploadedBytes: 7 });
    await expect(completeAttachmentUpload(UPLOAD_ID, "csrf"))
      .resolves.toMatchObject({ state: "validating", preview: null });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/attachments/uploads");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: "POST",
      credentials: "include",
      body: JSON.stringify({
        conversation_id: CONVERSATION_ID,
        original_name: "candidate.pdf",
        declared_mime: "application/pdf",
        declared_size: 7,
      }),
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf" }),
    });
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      method: "PUT", credentials: "include", body: file,
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf" }),
    });
  });

  it("strictly parses attachment lists and rejects unknown storage fields", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([attachment("ready")])));

    await expect(listConversationAttachments(CONVERSATION_ID)).resolves.toEqual([
      expect.objectContaining({
        attachmentId: ATTACHMENT_ID,
        source: "user",
        displayName: "candidate.pdf",
        state: "ready",
        preview: { attachmentId: ATTACHMENT_ID, detectedMime: "application/pdf" },
      }),
    ]);

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([
      { ...attachment("ready"), object_ref_ciphertext: "private" },
    ])));
    await expect(listConversationAttachments(CONVERSATION_ID))
      .rejects.toThrow("Attachment response invalid");
  });

  it("strictly parses short-lived tickets", async () => {
    const ticket = "t".repeat(43);
    const payload = {
      ticket,
      expires_at: "2026-09-03T10:01:00Z",
      content_path: `/api/v1/attachments/content/${ticket}`,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);

    await expect(issueAttachmentTicket(ATTACHMENT_ID, "preview", "csrf"))
      .resolves.toEqual({ ticket, expiresAt: payload.expires_at, contentPath: payload.content_path });
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: "POST",
      credentials: "include",
      body: JSON.stringify({ purpose: "preview" }),
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf" }),
    });
  });

  it("strictly parses citations, artifact versions, and read state", () => {
    const citation = {
      citation_key: "source-1", title: "岗位资料", url: "https://example.com/a",
      site: "example.com", retrieved_at: "2026-09-03T10:00:00Z", supports: ["结论一"],
    };
    expect(parseConversationCitation(citation)).toEqual({
      citationKey: "source-1", title: "岗位资料", url: "https://example.com/a",
      site: "example.com", retrievedAt: "2026-09-03T10:00:00Z", supports: ["结论一"],
    });
    expect(parseArtifactVersion({
      artifact_key: "interview-plan", version_no: 2, producer_version_id: "producer-2",
      current: true, status: "ready", attachment: attachment("ready"),
    })).toMatchObject({ artifactKey: "interview-plan", versionNo: 2, current: true });
    expect(parseConversationReadState({
      conversation_id: CONVERSATION_ID,
      last_read_message_seq: 4,
      last_read_at: "2026-09-03T10:00:00Z",
    })).toEqual({
      conversationId: CONVERSATION_ID,
      lastReadMessageSeq: 4,
      lastReadAt: "2026-09-03T10:00:00Z",
    });

    expect(() => parseConversationCitation({ ...citation, internal_id: "private" }))
      .toThrow("Citation response invalid");
    expect(() => parseArtifactVersion({
      artifact_key: "x", version_no: 1, producer_version_id: "v1",
      current: false, status: "processing", attachment: null, extra: true,
    })).toThrow("Artifact version response invalid");
  });
});
