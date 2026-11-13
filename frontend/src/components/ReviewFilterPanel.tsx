"use client";

import Link from "next/link";

import { PeriodList } from "@/components/PeriodList";
import { SearchBox } from "@/components/SearchBox";
import { formatNumber, SENTIMENTS } from "@/lib/format";
import type { PeriodsFilter } from "@/lib/periodFilter";
import type { Match, ThemeChoices } from "@/lib/reviewFilters";
import type { RatingFilter, RatingScale, ReviewFacets, Sentiment, ThemeFacet, TrendInterval } from "@/lib/types";

const HEADINGS: Record<ThemeFacet["kind"], string> = {
  discovered: "Themes",
  candidate: "Emerging topics",
  general: "No specific topic",
};

const sameScale = (a: RatingScale, b: RatingScale) => JSON.stringify(a) === JSON.stringify(b);

function ThemeRow({
  facet,
  sentiments,
  checked,
  href,
  onToggle,
  onSentiments,
}: {
  facet: ThemeFacet;
  /** The chosen sentiments of a ticked theme (none: any). */
  sentiments: Sentiment[];
  checked: boolean;
  href: string;
  onToggle: () => void;
  onSentiments: (sentiments: Sentiment[]) => void;
}) {
  const name = facet.kind === "general" ? "General (no specific topic)" : facet.name;
  return (
    <div className="group">
      <label
        className={`flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-subtle ${
          facet.count === 0 && !checked ? "opacity-45" : ""
        }`}
      >
        <input type="checkbox" checked={checked} onChange={onToggle} className="size-4 shrink-0" />
        <span className="flex min-w-0 flex-1 items-center gap-1">
          <span className="truncate" title={name}>
            {name}
          </span>
          {/* Opens the theme's page; shown on hover (or keyboard focus) so the list stays quiet. Hidden
              by opacity rather than visibility, which would also take it out of the Tab order. */}
          <Link
            href={href}
            aria-label={`Open the ${name} page`}
            title={`Open the ${name} page`}
            className="shrink-0 px-1 text-[13px] text-link opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
          >
            ↗
          </Link>
        </span>
        <span className="text-muted tabular-nums">{formatNumber(facet.count)}</span>
      </label>
      {checked && (
        <div role="group" aria-label={`Sentiment of ${name} mentions`} className="flex flex-wrap gap-1 pt-0.5 pr-2 pb-2 pl-8">
          {SENTIMENTS.map(({ key, label, bar }) => {
            const on = sentiments.includes(key);
            return (
              <button
                key={key}
                type="button"
                aria-pressed={on}
                title={`${label} mentions of ${name}`}
                onClick={() => onSentiments(on ? sentiments.filter((item) => item !== key) : [...sentiments, key])}
                className={`inline-flex h-6 items-center gap-1.5 rounded-full border px-2 text-xs ${
                  on ? "border-info-line bg-info-bg" : "border-line bg-surface hover:border-line-strong"
                }`}
              >
                <span className={`size-[7px] rounded-full ${bar}`} aria-hidden="true" />
                {label} <span className="text-muted">{formatNumber(facet.sentiments?.[key] ?? 0)}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/**
 * The All reviews page's filter column: a search, the periods (a checklist by year, quarter or month),
 * the themes with their counts (a ticked theme reveals
 * the sentiments of its mentions to choose), whether several themes match any or all, and the ratings.
 * The counts come from the facets endpoint, so they always describe the current results.
 */
export function ReviewFilterPanel({
  facets,
  themes,
  match,
  ratingFilter,
  search,
  periodsFilter,
  periodInterval,
  themeHref,
  onThemes,
  onMatch,
  onRatingFilter,
  onSearch,
  onPeriodsFilter,
  onPeriodInterval,
}: {
  facets: ReviewFacets;
  periodsFilter: PeriodsFilter | null;
  periodInterval: TrendInterval;
  onPeriodsFilter: (filter: PeriodsFilter | null) => void;
  onPeriodInterval: (interval: TrendInterval) => void;
  themes: ThemeChoices;
  match: Match;
  ratingFilter: RatingFilter | null;
  search: string | null;
  themeHref: (themeId: number) => string;
  onThemes: (themes: ThemeChoices) => void;
  onMatch: (match: Match) => void;
  onRatingFilter: (filter: RatingFilter | null) => void;
  onSearch: (search: string | null) => void;
}) {
  const scale = facets.ratings.scales[0];
  const chosenRatings = scale && ratingFilter && sameScale(ratingFilter.scale, scale.scale) ? ratingFilter.labels : [];
  const ratingMax = Math.max(1, ...(scale?.buckets.map((bucket) => bucket.count) ?? []));

  function toggleTheme(themeId: number) {
    const next = new Map(themes);
    if (next.has(themeId)) next.delete(themeId);
    else next.set(themeId, []);
    onThemes(next);
  }

  function setSentiments(themeId: number, sentiments: Sentiment[]) {
    onThemes(new Map(themes).set(themeId, sentiments));
  }

  function toggleRating(label: string) {
    if (!scale) return;
    const labels = chosenRatings.includes(label) ? chosenRatings.filter((item) => item !== label) : [...chosenRatings, label];
    onRatingFilter(labels.length ? { scale: scale.scale as RatingScale, labels } : null);
  }

  let previousKind: ThemeFacet["kind"] | null = null;
  return (
    <div className="flex flex-col">
      <div className="border-b border-line p-4">
        <SearchBox value={search} onSearch={onSearch} className="w-full" />
      </div>

      <div className="border-b border-line">
        <PeriodList
          periods={facets.periods}
          interval={periodInterval}
          onIntervalChange={onPeriodInterval}
          filter={periodsFilter}
          onFilter={onPeriodsFilter}
        />
      </div>

      <div className="flex items-center justify-between gap-2 px-4 pt-3 pb-1.5">
        <h2 className="text-sm font-semibold">Themes</h2>
        <div className="flex items-center gap-1.5 text-xs text-muted">
          Match
          <div role="group" aria-label="Match" className="flex overflow-hidden rounded-md border border-line-strong">
            {(["any", "all"] as const).map((key) => (
              <button
                key={key}
                type="button"
                aria-pressed={match === key}
                onClick={() => onMatch(key)}
                title={key === "any" ? "Reviews raising any of the ticked themes" : "Reviews raising every ticked theme"}
                className={`px-2 py-0.5 text-xs ${match === key ? "bg-primary text-on-primary" : "bg-surface text-fg"}`}
              >
                {key === "any" ? "Any" : "All"}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="px-2 pb-2">
        {facets.themes.map((facet) => {
          const heading = facet.kind !== previousKind && facet.kind !== "discovered" ? HEADINGS[facet.kind] : null;
          previousKind = facet.kind;
          return (
            <div key={facet.id}>
              {heading && <p className="mx-2 mt-3 mb-1 text-[11px] font-semibold tracking-wide text-muted uppercase">{heading}</p>}
              <ThemeRow
                facet={facet}
                checked={themes.has(facet.id)}
                sentiments={themes.get(facet.id) ?? []}
                href={themeHref(facet.id)}
                onToggle={() => toggleTheme(facet.id)}
                onSentiments={(sentiments) => setSentiments(facet.id, sentiments)}
              />
            </div>
          );
        })}
        <p className="mx-2 mt-1.5 text-xs text-muted">Tick a theme to choose the sentiment of its mentions.</p>
      </div>

      {scale && (
        <>
          <h2 className="border-t border-line px-4 pt-3 pb-1.5 text-sm font-semibold">Rating</h2>
          <div className="px-2 pb-3">
            {scale.buckets.map((bucket) => (
              <label
                key={bucket.label}
                className="grid cursor-pointer grid-cols-[16px_minmax(1rem,max-content)_minmax(0,1fr)_3.25rem] items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-subtle"
              >
                <input type="checkbox" checked={chosenRatings.includes(bucket.label)} onChange={() => toggleRating(bucket.label)} className="size-4" />
                <span className="max-w-28 truncate" title={bucket.label}>
                  {bucket.label}
                </span>
                <span className="h-1.5 overflow-hidden rounded-full bg-subtle" aria-hidden="true">
                  <span className="block h-full rounded-full bg-primary" style={{ width: `${(bucket.count / ratingMax) * 100}%` }} />
                </span>
                <span className="text-right text-muted tabular-nums">{formatNumber(bucket.count)}</span>
              </label>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
