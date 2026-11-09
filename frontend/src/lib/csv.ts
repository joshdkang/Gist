import Papa from "papaparse";

import type { ColumnMapping, RatingScale } from "@/lib/types";

// The API's limits (src/config.py). It enforces them; checking here saves a pointless upload.
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
export const MAX_UPLOAD_ROWS = 5000;

export interface ParsedCsv {
  columns: string[];
  rows: Record<string, string>[];
}

/** Reads the whole file in the browser, so the row count and the guesses see every row. */
export function parseCsv(file: File): Promise<ParsedCsv> {
  return new Promise((resolve, reject) => {
    Papa.parse<Record<string, string>>(file, {
      header: true,
      skipEmptyLines: true,
      complete: (result) => resolve({ columns: result.meta.fields ?? [], rows: result.data }),
      error: (error) => reject(error),
    });
  });
}

export type MappedField = "title" | "rating" | "review_date" | "external_id";

const FIELD_PATTERNS: Record<MappedField, RegExp[]> = {
  title: [/^(title|summary|headline|subject)$/i, /title|summary|headline/i],
  rating: [/^(rating|stars?|score)$/i, /rating|stars?|score/i],
  review_date: [/^(date|review_?date|created_?at|timestamp)$/i, /date|time|created/i],
  external_id: [/^(id|review_?id)$/i, /(^|[._])id$/i],
};

/** Guesses which column feeds each field. The review text is the column with the longest values. */
export function guessMapping(parsed: ParsedCsv): { text: string } & Partial<Record<MappedField, string>> {
  const sample = parsed.rows.slice(0, 200);
  const averageLength = (column: string) =>
    sample.reduce((sum, row) => sum + (row[column]?.length ?? 0), 0) / Math.max(1, sample.length);
  const text = [...parsed.columns].sort((a, b) => averageLength(b) - averageLength(a))[0] ?? "";

  const used = new Set([text]);
  const guesses: Partial<Record<MappedField, string>> = {};
  for (const field of Object.keys(FIELD_PATTERNS) as MappedField[]) {
    for (const pattern of FIELD_PATTERNS[field]) {
      const match = parsed.columns.find((column) => !used.has(column) && pattern.test(column));
      if (match) {
        guesses[field] = match;
        used.add(match);
        break;
      }
    }
  }
  return { text, ...guesses };
}

// The values the API's binary scale accepts (src/ingestion.py).
const BINARY_VALUES = new Set(["1", "true", "yes", "y", "positive", "up", "+", "0", "false", "no", "n", "negative", "down", "-"]);

export interface ScaleDraft {
  type: RatingScale["type"];
  min: string;
  max: string;
  labels: string; // comma-separated, lowest first
}

/** Guesses the rating scale from the values in the rating column. */
export function guessScale(values: string[]): ScaleDraft {
  const cleaned = values.map((value) => value.trim()).filter(Boolean);
  const draft: ScaleDraft = { type: "numeric", min: "1", max: "5", labels: "" };
  if (cleaned.length === 0) return draft;

  const unique = [...new Set(cleaned)];
  if (unique.every((value) => BINARY_VALUES.has(value.toLowerCase())) && unique.length <= 2) {
    return { ...draft, type: "binary" };
  }
  const numbers = cleaned.map(Number);
  if (numbers.every(Number.isFinite)) {
    const low = Math.min(...numbers);
    const high = Math.max(...numbers);
    const max = high <= 5 ? 5 : high <= 10 ? 10 : high <= 100 ? 100 : Math.ceil(high);
    return { ...draft, min: String(low >= 1 ? 1 : Math.floor(low)), max: String(max) };
  }
  return { ...draft, type: "labels", labels: unique.slice(0, 20).join(", ") };
}

export function buildScale(draft: ScaleDraft): RatingScale | string {
  if (draft.type === "binary") return { type: "binary" };
  if (draft.type === "labels") {
    const labels = draft.labels.split(",").map((label) => label.trim()).filter(Boolean);
    return labels.length >= 2 ? { type: "labels", labels } : "List at least two rating labels, lowest first.";
  }
  const min = Number(draft.min);
  const max = Number(draft.max);
  if (!Number.isFinite(min) || !Number.isFinite(max) || min >= max) return "The rating range needs a minimum below its maximum.";
  return { type: "numeric", min, max };
}

export function buildMapping(
  fields: { text: string } & Partial<Record<MappedField, string>>,
  metadataColumns: string[],
  dateFormat: string,
): ColumnMapping {
  const mapping: ColumnMapping = { text: fields.text };
  for (const field of ["title", "rating", "review_date", "external_id"] as const) {
    if (fields[field]) mapping[field] = fields[field];
  }
  if (metadataColumns.length) mapping.metadata_columns = metadataColumns;
  if (fields.review_date && dateFormat.trim()) mapping.date_format = dateFormat.trim();
  return mapping;
}
