import { formatNumber, formatPercent, SENTIMENTS } from "@/lib/format";
import type { SentimentCounts } from "@/lib/types";

/**
 * A theme's reviews split by sentiment, always across the full width. The bar shows only the split, not
 * the theme's size (the review count beside it does that): a shorter bar's empty track would read as neutral.
 * Reviews with no sentiment (code fallbacks) are left out, so the four parts fill the bar.
 */
export function SentimentBar({ counts, height = "h-2.5" }: { counts: SentimentCounts; height?: string }) {
  const withSentiment = SENTIMENTS.reduce((sum, { key }) => sum + counts[key], 0);
  return (
    <div className={`${height} flex overflow-hidden rounded-[3px] bg-subtle`} aria-hidden="true">
      {SENTIMENTS.map(({ key, bar }) =>
        counts[key] ? (
          <span key={key} className={`${bar} h-full`} style={{ width: `${(counts[key] / withSentiment) * 100}%` }} />
        ) : null,
      )}
    </div>
  );
}

export function SentimentLegend() {
  return (
    <div className="flex flex-wrap gap-3 text-[13px] text-muted">
      {SENTIMENTS.map(({ key, label, bar }) => (
        <span key={key} className="flex items-center gap-1.5">
          <span className={`size-2.5 rounded-[2px] ${bar}`} aria-hidden="true" />
          {label}
        </span>
      ))}
    </div>
  );
}

/** Counts and percentages per sentiment, for a theme's detail page. */
export function SentimentBreakdown({ counts }: { counts: SentimentCounts }) {
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
      {SENTIMENTS.map(({ key, label, bar, text }) => (
        <span key={key} className="flex items-center gap-1.5">
          <span className={`size-2.5 rounded-[2px] ${bar}`} aria-hidden="true" />
          <span className={`font-semibold ${text}`}>{label}</span>
          <span>
            {formatNumber(counts[key])} ({formatPercent(counts[key], counts.total)})
          </span>
        </span>
      ))}
    </div>
  );
}
