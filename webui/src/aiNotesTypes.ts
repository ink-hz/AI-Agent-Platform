export interface AiNoteSummary {
  slug: string;
  title: string;
  filename: string;
  description: string;
  author: string;
  motto: string | null;
  published_at: string;
  updated_at: string | null;
  tags: string[];
  reading_minutes: number;
}

export interface AiNoteCategory {
  slug: string;
  title: string;
  articles: AiNoteSummary[];
}

export interface AiNotesIndex {
  categories: AiNoteCategory[];
}

export interface AiNoteArticle extends AiNoteSummary {
  category_slug: string;
  category_title: string;
  markdown: string;
}

export class AiNotesContractError extends Error {
  constructor() {
    super("AI notes response contract invalid");
    this.name = "AiNotesContractError";
  }
}

export class AiNotesApiError extends Error {
  constructor(public readonly status: number) {
    super(`AI notes API ${status}`);
    this.name = "AiNotesApiError";
  }
}

const SUMMARY_KEYS = [
  "slug", "title", "filename", "description", "author", "motto",
  "published_at", "updated_at", "tags", "reading_minutes",
] as const;
const ARTICLE_KEYS = [
  ...SUMMARY_KEYS, "category_slug", "category_title", "markdown",
] as const;
const CATEGORY_SLUG = /^[a-z0-9][a-z0-9-]{0,63}$/;
const ARTICLE_SLUG = /^[a-z0-9][a-z0-9-]{0,127}$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new AiNotesContractError();
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]): void {
  const actual = Object.keys(value).sort();
  const selected = [...expected].sort();
  if (actual.length !== selected.length || actual.some((key, index) => key !== selected[index])) {
    throw new AiNotesContractError();
  }
}

function nonEmptyString(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw new AiNotesContractError();
  return value;
}

function optionalNonEmptyString(value: unknown): string | null {
  return value === null ? null : nonEmptyString(value);
}

function slug(value: unknown, pattern: RegExp): string {
  const selected = nonEmptyString(value);
  if (!pattern.test(selected)) throw new AiNotesContractError();
  return selected;
}

function dateValue(value: unknown, optional = false): string | null {
  if (optional && value === null) return null;
  if (typeof value !== "string" || !ISO_DATE.test(value)) throw new AiNotesContractError();
  const [year, month, day] = value.split("-").map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > daysInMonth[month - 1]) {
    throw new AiNotesContractError();
  }
  return value;
}

function summaryFields(value: Record<string, unknown>): AiNoteSummary {
  if (!Array.isArray(value.tags) || value.tags.some((tag) => typeof tag !== "string" || !tag.trim())) {
    throw new AiNotesContractError();
  }
  if (!Number.isInteger(value.reading_minutes) || (value.reading_minutes as number) < 1) {
    throw new AiNotesContractError();
  }
  return {
    slug: slug(value.slug, ARTICLE_SLUG),
    title: nonEmptyString(value.title),
    filename: nonEmptyString(value.filename),
    description: nonEmptyString(value.description),
    author: nonEmptyString(value.author),
    motto: optionalNonEmptyString(value.motto),
    published_at: dateValue(value.published_at) as string,
    updated_at: dateValue(value.updated_at, true),
    tags: [...value.tags] as string[],
    reading_minutes: value.reading_minutes as number,
  };
}

function parseSummary(value: unknown): AiNoteSummary {
  const selected = record(value);
  exactKeys(selected, SUMMARY_KEYS);
  return summaryFields(selected);
}

export function parseAiNotesIndex(value: unknown): AiNotesIndex {
  const selected = record(value);
  exactKeys(selected, ["categories"]);
  if (!Array.isArray(selected.categories)) throw new AiNotesContractError();
  return {
    categories: selected.categories.map((item) => {
      const category = record(item);
      exactKeys(category, ["slug", "title", "articles"]);
      if (!Array.isArray(category.articles)) throw new AiNotesContractError();
      return {
        slug: slug(category.slug, CATEGORY_SLUG),
        title: nonEmptyString(category.title),
        articles: category.articles.map(parseSummary),
      };
    }),
  };
}

export function parseAiNoteArticle(value: unknown): AiNoteArticle {
  const selected = record(value);
  exactKeys(selected, ARTICLE_KEYS);
  if (typeof selected.markdown !== "string") throw new AiNotesContractError();
  return {
    ...summaryFields(selected),
    category_slug: slug(selected.category_slug, CATEGORY_SLUG),
    category_title: nonEmptyString(selected.category_title),
    markdown: selected.markdown,
  };
}
