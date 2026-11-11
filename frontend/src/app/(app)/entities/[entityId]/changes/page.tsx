"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { periodName } from "@/components/TrendChart";
import { BackLink, card, ErrorNote, INTERVALS, inputBase, Loading, Page, PageTitle, Toggle } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { useEntity, useThemeChanges } from "@/lib/queries";
import type { ThemeChange, ThemeChanges, TrendInterval } from "@/lib/types";

// Below this many reviews in a period, the page warns that only large changes can be told from chance.
const FEW_REVIEWS = 20;

/**
 * Sort by:
 * - share: the change in the share of the period's reviews that raise the theme.
 * - negative: the change in the share of the theme's own reviews that are negative.
 */
type Sort = "share" | "negative";
const SORTS: { key: Sort; label: string }[] = [
  { key: "share", label: "Mentions" },
  { key: "negative", label: "Negative" },
];

const percent = (value: number) => {
  const rounded = Math.round(value * 100);
  return value > 0 && rounded === 0 ? "<1%" : `${rounded}%`;
};

/** The difference between two shares in percentage points, signed: "+6 pts", "−3 pts", "0 pts". */
function formatChange(change: number): string {
  const rounded = Math.round(change * 100);
  if (rounded === 0) return change === 0 ? "0 pts" : `${change > 0 ? "+" : "−"}<1 pt`;
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded)} ${Math.abs(rounded) === 1 ? "pt" : "pts"}`;
}

/** The share of a theme's reviews that are negative, or null when it has none in the period. */
const negativeShare = (counts: { negative: number; total: number }) =>
  counts.total ? counts.negative / counts.total : null;

interface Row {
  change: ThemeChange;
  /** The change this sort ranks by, or null when there is nothing to compare. */
  delta: number | null;
  clear: boolean;
}

function rowsFor(themes: ThemeChange[], sort: Sort): Row[] {
  const rows = themes.map((change): Row => {
    if (sort === "share") {
      return { change, delta: change.current_share - change.previous_share, clear: change.share_change_clear };
    }
    const before = negativeShare(change.previous);
    const now = negativeShare(change.current);
    return {
      change,
      delta: before === null || now === null ? null : now - before,
      clear: change.negative_change_clear,
    };
  });
  return rows.sort((a, b) => Math.abs(b.delta ?? -1) - Math.abs(a.delta ?? -1) || a.change.name.localeCompare(b.change.name));
}

/** A bar from the middle: right for a rise, left for a fall, its length relative to the largest change. */
function ChangeBar({ delta, largest, tone }: { delta: number; largest: number; tone: string }) {
  const width = largest ? `${(Math.abs(delta) / largest) * 50}%` : "0";
  return (
    <div className="relative h-2 w-full rounded-full bg-subtle" aria-hidden="true">
      <span className="absolute top-[-2px] left-1/2 h-3 w-px bg-line-strong" />
      <span
        className={`absolute top-0 h-2 rounded-full ${tone}`}
        style={delta >= 0 ? { left: "50%", width } : { right: "50%", width }}
      />
    </div>
  );
}

function ChangeRow({
  entityId,
  row,
  sort,
  largest,
}: {
  entityId: string;
  row: Row;
  sort: Sort;
  largest: number;
}) {
  const { change, delta, clear } = row;
  const isNew = change.previous.total === 0;
  const gone = change.current.total === 0;
  const before = negativeShare(change.previous);
  const now = negativeShare(change.current);
  // More mentions is neither good nor bad; more negative is bad, so it takes the negative colour.
  const tone =
    sort === "negative" && clear && delta !== null ? (delta > 0 ? "bg-neg" : "bg-pos") : clear ? "bg-fg" : "bg-line-strong";
  const deltaText =
    delta === null ? "—" : `${formatChange(delta)}${clear ? "" : " (not significant)"}`;

  return (
    <li className="grid grid-cols-1 gap-x-6 gap-y-2 border-t border-line px-5 py-4 sm:grid-cols-[minmax(0,1fr)_200px_200px]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Link href={`/entities/${entityId}/themes/${change.id}`} className="font-semibold hover:underline">
            {change.name}
          </Link>
          {change.kind === "candidate" && (
            <span className="rounded-full bg-subtle px-2 py-0.5 text-xs font-semibold text-muted">Emerging</span>
          )}
          {isNew && <span className="rounded-full bg-subtle px-2 py-0.5 text-xs font-semibold text-muted">New</span>}
          {gone && (
            <span className="rounded-full bg-subtle px-2 py-0.5 text-xs font-semibold text-muted">Not raised</span>
          )}
        </div>
        <p className="mt-1 line-clamp-2 text-sm text-muted">{change.description}</p>
      </div>
      <div className="flex flex-col gap-1.5 text-sm">
        <span className="text-xs text-muted sm:hidden">Mentions</span>
        <span>
          {percent(change.previous_share)} → <strong>{percent(change.current_share)}</strong>
          <span className="text-muted">
            {" "}
            · {formatNumber(change.previous.total)} → {formatNumber(change.current.total)}
          </span>
        </span>
        {sort === "share" && delta !== null && <ChangeBar delta={delta} largest={largest} tone={tone} />}
        {sort === "share" && <span className={clear ? "font-semibold" : "text-muted"}>{deltaText}</span>}
      </div>
      <div className="flex flex-col gap-1.5 text-sm">
        <span className="text-xs text-muted sm:hidden">Negative</span>
        <span>
          {before === null ? "—" : percent(before)} → <strong>{now === null ? "—" : percent(now)}</strong>
          <span className="text-muted"> negative</span>
        </span>
        {sort === "negative" && delta !== null && <ChangeBar delta={delta} largest={largest} tone={tone} />}
        {sort === "negative" && (
          <span
            className={
              !clear || delta === null ? "text-muted" : delta > 0 ? "font-semibold text-neg-fg" : "font-semibold text-pos-fg"
            }
          >
            {deltaText}
          </span>
        )}
      </div>
    </li>
  );
}

function ChangesList({ entityId, data, sort }: { entityId: string; data: ThemeChanges; sort: Sort }) {
  const [showUnclear, setShowUnclear] = useState(false);
  const rows = rowsFor(data.themes, sort);
  const clear = rows.filter((row) => row.clear);
  const unclear = rows.filter((row) => !row.clear);
  const largest = Math.max(0, ...rows.map((row) => Math.abs(row.delta ?? 0)));
  const what = sort === "share" ? "share of mentions" : "negative share";

  return (
    <section className={`${card} overflow-hidden`}>
      <div className="hidden grid-cols-[minmax(0,1fr)_200px_200px] gap-x-6 px-5 py-3 text-xs font-semibold text-muted sm:grid">
        <span>Theme</span>
        <span>Mentions: share of the period&apos;s reviews</span>
        <span>Negative: share of the theme&apos;s reviews</span>
      </div>
      {clear.length === 0 ? (
        <p className="border-t border-line px-5 py-4 text-sm">
          No theme&apos;s {what} changed significantly.
        </p>
      ) : (
        <ul>
          {clear.map((row) => (
            <ChangeRow key={row.change.id} entityId={entityId} row={row} sort={sort} largest={largest} />
          ))}
        </ul>
      )}
      {unclear.length > 0 && (
        <div className="border-t border-line">
          <button
            type="button"
            aria-expanded={showUnclear}
            onClick={() => setShowUnclear(!showUnclear)}
            className="w-full px-5 py-3 text-left text-sm text-link hover:underline"
          >
            {showUnclear ? "▾" : "▸"} {unclear.length} {unclear.length === 1 ? "theme" : "themes"} with no
            significant change in {what}
          </button>
          {showUnclear && (
            <ul>
              {unclear.map((row) => (
                <ChangeRow key={row.change.id} entityId={entityId} row={row} sort={sort} largest={largest} />
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

export default function ChangesPage() {
  const { entityId } = useParams<{ entityId: string }>();
  const entity = useEntity(entityId);
  const [interval, setChangeInterval] = useState<TrendInterval>("quarter");
  const [period, setPeriod] = useState<string | null>(null);
  // null compares with the period before the chosen one.
  const [compareTo, setCompareTo] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>("share");
  const changes = useThemeChanges(entityId, interval, period, compareTo);

  if (entity.error instanceof ApiError && entity.error.status === 404) {
    return (
      <Page>
        <BackLink href="/">Entities</BackLink>
        <p className="mt-4">This entity doesn&apos;t exist, or you don&apos;t have access to it.</p>
      </Page>
    );
  }

  function changeInterval(next: TrendInterval) {
    setChangeInterval(next);
    // A quarter's start is not a month's; let the API pick both again.
    setPeriod(null);
    setCompareTo(null);
  }

  function changePeriod(next: string) {
    setPeriod(next);
    if (next === compareTo) setCompareTo(null); // a period can't be compared with itself
  }

  const data = changes.data;
  const updating = changes.isPlaceholderData;
  const name = (start: string | null) => (start ? periodName(start, data?.interval ?? interval) : "");
  const few = data && data.period_start && Math.min(data.current_reviews, data.previous_reviews) < FEW_REVIEWS;
  // The periods to compare with: every other period with reviews, newest first, plus the default
  // (the period before) when it has none, so the selector can show what is being compared.
  const compareOptions = data
    ? [
        ...data.periods.filter((option) => option.period_start !== data.period_start),
        ...(data.previous_start && !data.periods.some((option) => option.period_start === data.previous_start)
          ? [{ period_start: data.previous_start, reviews: 0 }]
          : []),
      ].sort((a, b) => b.period_start.localeCompare(a.period_start))
    : [];

  return (
    <Page>
      <div className="flex flex-col gap-6">
        <div>
          <BackLink href={`/entities/${entityId}`}>{entity.data?.name ?? "Entity"}</BackLink>
          <PageTitle>Period comparison</PageTitle>
          <p className="mt-1 max-w-2xl text-muted">
            Each theme&apos;s share of one period&apos;s reviews against another period (by default, the one before). Shares, not counts, so a
            busier period doesn&apos;t make everything look like it grew. Significant changes are those unlikely to be
            explained by random variation in which customers left reviews. Because every theme is tested at once,
            the bar is set so that no more than about 1 in 20 changes marked significant should be a fluke.
          </p>
        </div>

        <div className={`${card} flex flex-wrap items-end gap-4 px-5 py-4`}>
          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">Interval</span>
            <Toggle label="Interval" options={INTERVALS} value={interval} onChange={changeInterval} />
          </div>
          {data && data.periods.length > 0 && (
            <>
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium">Period</span>
                <select
                  className={`${inputBase} pr-8`}
                  value={data.period_start ?? ""}
                  onChange={(event) => changePeriod(event.target.value)}
                >
                  {[...data.periods].reverse().map((option) => (
                    <option key={option.period_start} value={option.period_start}>
                      {periodName(option.period_start, data.interval)} · {formatNumber(option.reviews)} reviews
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium">Compared with</span>
                <select
                  className={`${inputBase} pr-8`}
                  value={data.previous_start ?? ""}
                  onChange={(event) => setCompareTo(event.target.value)}
                >
                  {compareOptions.map((option) => (
                    <option key={option.period_start} value={option.period_start} disabled={option.reviews === 0}>
                      {periodName(option.period_start, data.interval)} ·{" "}
                      {option.reviews ? `${formatNumber(option.reviews)} reviews` : "no reviews"}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">Biggest change in</span>
            <Toggle label="Sort by" options={SORTS} value={sort} onChange={setSort} />
          </div>
        </div>

        <ErrorNote error={changes.error} />
        {changes.isPending && <Loading label="Comparing periods…" />}
        {data && !data.period_start && <p className={`${card} p-6`}>None of this entity&apos;s reviews has a date.</p>}
        {data && data.period_start && (
          <div aria-busy={updating} className={`flex flex-col gap-4 transition-opacity ${updating ? "opacity-50" : ""}`}>
            <p>
              <strong>{name(data.period_start)}</strong> ({formatNumber(data.current_reviews)} reviews) compared with{" "}
              <strong>{name(data.previous_start)}</strong> ({formatNumber(data.previous_reviews)} reviews).
            </p>
            {few && (
              <p className="rounded-lg border border-warn-line bg-warn-bg px-4 py-3 text-sm">
                {data.previous_reviews === 0
                  ? `${name(data.previous_start)} has no reviews, so there is nothing to compare with. Pick another period to compare with.`
                  : `One of these periods has fewer than ${FEW_REVIEWS} reviews, so only large changes can be confirmed as significant. A longer interval may help.`}
              </p>
            )}
            {data.themes.length === 0 ? (
              <p className={`${card} p-6`}>No theme was raised in either period.</p>
            ) : (
              <ChangesList entityId={entityId} data={data} sort={sort} />
            )}
          </div>
        )}
      </div>
    </Page>
  );
}
