import type { Sentiment } from "@/lib/types";

/**
 * The All reviews page's theme filters, as the URL and the API carry them: a repeated `theme`, each a
 * theme id with the sentiments of its mentions to keep (`12` for any, `12:negative,mixed`), and `match`
 * (any or all) for how several combine. The page's other filters are the shared ones (rating, period,
 * search), so a link from the entity page carries them as they are.
 */
export type ThemeChoices = Map<number, Sentiment[]>;
export type Match = "any" | "all";
export type ReviewSort = "newest" | "oldest" | "highest" | "lowest";

const SENTIMENT_VALUES: Sentiment[] = ["positive", "mixed", "neutral", "negative"];

export function readThemeChoices(params: { getAll(name: string): string[] }): ThemeChoices {
  const choices: ThemeChoices = new Map();
  for (const item of params.getAll("theme")) {
    const [id, sentiments = ""] = item.split(":");
    const themeId = Number(id);
    if (!Number.isInteger(themeId) || themeId <= 0) continue;
    choices.set(
      themeId,
      sentiments.split(",").filter((value): value is Sentiment => SENTIMENT_VALUES.includes(value as Sentiment)),
    );
  }
  return choices;
}

export function appendThemeChoices(params: URLSearchParams, choices: ThemeChoices, match: Match): URLSearchParams {
  for (const [id, sentiments] of choices) params.append("theme", sentiments.length ? `${id}:${sentiments.join(",")}` : String(id));
  if (choices.size > 1 && match === "all") params.set("match", "all");
  return params;
}

export const readMatch = (params: { get(name: string): string | null }): Match =>
  params.get("match") === "all" ? "all" : "any";

export function readSort(params: { get(name: string): string | null }): ReviewSort {
  const sort = params.get("sort");
  return sort === "oldest" || sort === "highest" || sort === "lowest" ? sort : "newest";
}
