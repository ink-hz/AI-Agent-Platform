import { platformPath } from "./auth";
import {
  AiNotesApiError,
  parseAiNoteArticle,
  parseAiNotesIndex,
  type AiNoteArticle,
  type AiNotesIndex,
} from "./aiNotesTypes";


export interface AiNotesClient {
  fetchIndex(signal?: AbortSignal): Promise<AiNotesIndex>;
  fetchArticle(
    categorySlug: string,
    articleSlug: string,
    signal?: AbortSignal,
  ): Promise<AiNoteArticle>;
}

async function getJson(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(platformPath(path), {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new AiNotesApiError(response.status);
  return response.json();
}

export const aiNotesClient: AiNotesClient = {
  async fetchIndex(signal) {
    return parseAiNotesIndex(await getJson("/api/v1/ai-notes", signal));
  },

  async fetchArticle(categorySlug, articleSlug, signal) {
    const path = `/api/v1/ai-notes/${encodeURIComponent(categorySlug)}/${encodeURIComponent(articleSlug)}`;
    return parseAiNoteArticle(await getJson(path, signal));
  },
};
