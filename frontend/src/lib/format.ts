import type { Sentiment } from "@/lib/types";

export const formatNumber = (value: number) => value.toLocaleString("en-US");

export function formatPercent(part: number, whole: number): string {
  if (!whole) return "0%";
  const percent = (part * 100) / whole;
  return percent > 0 && percent < 1 ? "<1%" : `${Math.round(percent)}%`;
}

/** Review dates are stored in UTC; show them as the calendar date they were given. */
export const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

/** Tailwind classes per sentiment, in the order bars stack: positive, mixed, neutral, negative. */
export const SENTIMENTS: { key: Sentiment; label: string; bar: string; text: string }[] = [
  { key: "positive", label: "Positive", bar: "bg-pos", text: "text-pos-fg" },
  { key: "mixed", label: "Mixed", bar: "bg-mixed", text: "text-mixed-fg" },
  { key: "neutral", label: "Neutral", bar: "bg-neutral", text: "text-neutral-fg" },
  { key: "negative", label: "Negative", bar: "bg-neg", text: "text-neg-fg" },
];

export const sentimentStyle = (key: Sentiment) => SENTIMENTS.find((sentiment) => sentiment.key === key)!;
