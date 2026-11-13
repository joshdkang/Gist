"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ChangesTable } from "@/components/ChangesTable";
import { ExportButton, exportButtonClass } from "@/components/ExportButton";
import { NotAnalyzed } from "@/components/NotAnalyzed";
import { PeriodPicker, periodQuery, SUMMARY_CHANGES_PREFIX, usePeriodChoice } from "@/components/PeriodPicker";
import { periodName } from "@/components/TrendChart";
import { BackLink, card, ErrorNote, Loading, Page, PageTitle, Toggle } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { formatChange, formatNumber, formatShare as percent, negativeShare } from "@/lib/format";
import { useLayout } from "@/lib/layout";
import { useEntity, useThemeChanges } from "@/lib/queries";
import type { ThemeChange, ThemeChanges } from "@/lib/types";

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
  href,
  row,
  sort,
  largest,
}: {
  /** The theme's page, opened on the two periods compared. */
  href: string;
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
          <Link href={href} className="font-semibold hover:underline">
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
        {/* A period with none of the theme's reviews has no negative share, so it says so instead. */}
        <span>
          {before === null ? <span className="text-muted">no reviews</span> : percent(before)} →{" "}
          {now === null ? <span className="text-muted">no reviews this period</span> : <strong>{percent(now)}</strong>}
          {now !== null && <span className="text-muted"> negative</span>}
        </span>
        {sort === "negative" && delta !== null && <ChangeBar delta={delta} largest={largest} tone={tone} />}
        {sort === "negative" && delta !== null && (
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
  // The theme's page opens on this interval with both periods chosen in its trend, so its reviews
  // are the ones behind the comparison.
  const periodParams = new URLSearchParams({ interval: data.interval });
  for (const start of [data.previous_start, data.period_start]) if (start) periodParams.append("period", start);
  const themeHref = (themeId: number) => `/entities/${entityId}/themes/${themeId}?${periodParams}`;

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
            <ChangeRow key={row.change.id} href={themeHref(row.change.id)} row={row} sort={sort} largest={largest} />
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
                <ChangeRow key={row.change.id} href={themeHref(row.change.id)} row={row} sort={sort} largest={largest} />
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
  const choice = usePeriodChoice();
  const { interval, period, compareTo } = choice;
  const [sort, setSort] = useState<Sort>("share");
  const changes = useThemeChanges(entityId, interval, period, compareTo);
  // Desktop shows both changes in one sortable table; mobile keeps the list with its sort switch.
  const desktop = useLayout() === "desktop";

  if (entity.error instanceof ApiError && entity.error.status === 404) {
    return (
      <Page>
        <BackLink href="/">Entities</BackLink>
        <p className="mt-4">This entity doesn&apos;t exist, or you don&apos;t have access to it.</p>
      </Page>
    );
  }

  // The periods as chosen; an unchosen comparison is left to the API's default, since the default
  // period before may have no reviews, which compare_to refuses.
  const chosenQuery = (chosen: string, prefix = "") =>
    periodQuery({ interval, period: period ?? chosen, compareTo }, prefix);

  const data = changes.data;
  const updating = changes.isPlaceholderData;
  const name = (start: string | null) => (start ? periodName(start, data?.interval ?? interval) : "");
  const few = data && data.period_start && Math.min(data.current_reviews, data.previous_reviews) < FEW_REVIEWS;

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
          <PeriodPicker choice={choice} data={data} />
          {!desktop && (
            <div className="flex flex-col gap-1.5">
              <span className="text-sm font-medium">Biggest change in</span>
              <Toggle label="Sort by" options={SORTS} value={sort} onChange={setSort} />
            </div>
          )}
          {/* Two equal columns, so the two buttons are the same width. */}
          {data?.period_start && (
            <div className="ml-auto grid grid-cols-2 gap-2 self-end">
              <Link
                href={`/entities/${entityId}/summary?${chosenQuery(data.period_start, SUMMARY_CHANGES_PREFIX)}`}
                className={exportButtonClass}
              >
                <span aria-hidden="true">⎙</span>
                Printable summary
              </Link>
              <ExportButton
                path={`/entities/${entityId}/exports/changes.csv?${chosenQuery(data.period_start)}`}
                className="w-full"
              />
            </div>
          )}
        </div>

        <ErrorNote error={changes.error} />
        {changes.isPending && <Loading label="Comparing periods…" />}
        {entity.data?.theme_count === 0 && <NotAnalyzed entity={entity.data} what="The comparison" />}
        {data && !data.period_start && entity.data?.theme_count !== 0 && (
          <p className={`${card} p-6`}>None of this entity&apos;s reviews has a date.</p>
        )}
        {data && data.period_start && (
          <div aria-busy={updating} className={`flex flex-col gap-4 transition-opacity ${updating ? "opacity-50" : ""}`}>
            {!desktop && (
              <p>
                <strong>{name(data.period_start)}</strong> ({formatNumber(data.current_reviews)} reviews) compared with{" "}
                <strong>{name(data.previous_start)}</strong> ({formatNumber(data.previous_reviews)} reviews).
              </p>
            )}
            {few && (
              <p className="rounded-lg border border-warn-line bg-warn-bg px-4 py-3 text-sm">
                {data.previous_reviews === 0
                  ? `${name(data.previous_start)} has no reviews, so there is nothing to compare with. Pick another period to compare with.`
                  : `One of these periods has fewer than ${FEW_REVIEWS} reviews, so only large changes can be confirmed as significant. A longer interval may help.`}
              </p>
            )}
            {data.themes.length === 0 ? (
              <p className={`${card} p-6`}>No theme was raised in either period.</p>
            ) : desktop ? (
              <ChangesTable entityId={entityId} data={data} />
            ) : (
              <ChangesList entityId={entityId} data={data} sort={sort} />
            )}
          </div>
        )}
      </div>
    </Page>
  );
}
