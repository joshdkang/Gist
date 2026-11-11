"use client";

import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { EntityActions } from "@/components/EntityActions";
import { ExportButton } from "@/components/ExportButton";
import { RatingDrivers } from "@/components/RatingDrivers";
import { RatingsCard } from "@/components/RatingsCard";
import { RunBanner } from "@/components/RunBanner";
import { SentimentBar, SentimentLegend } from "@/components/SentimentBar";
import { BackLink, buttonPrimary, buttonSecondary, card, ErrorNote, Loading, Page, Toggle } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { formatDate, formatNumber, formatPercent } from "@/lib/format";
import { isActive, useEntity, useRatings, useRefreshWhenRunEnds, useThemeRatings, useThemes } from "@/lib/queries";
import { describeRatingFilter, ratingQuery, readRatingFilter } from "@/lib/ratingFilter";
import type { EntityDetail, RatingFilter, ThemeList, ThemeSummary } from "@/lib/types";

type Sort = "total" | "recent";

/** The API ranks by recent mentions, then total; "Most reviews" re-sorts the same list here. */
function sortThemes(themes: ThemeSummary[], sort: Sort): ThemeSummary[] {
  if (sort === "recent") return themes;
  return [...themes].sort((a, b) => b.review_count - a.review_count || b.recent_count - a.recent_count);
}

/** `query` keeps the page's rating filter on the theme's page ("" for none). */
function ThemeRow({ entityId, theme, query }: { entityId: string; theme: ThemeSummary; query: string }) {
  const { sentiment } = theme;
  return (
    <Link
      href={`/entities/${entityId}/themes/${theme.id}${query}`}
      className="grid items-center gap-3 border-b border-subtle px-5 py-4 last:border-b-0 hover:bg-subtle md:grid-cols-[minmax(0,1fr)_300px] md:gap-6"
    >
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="text-base font-semibold">{theme.name}</span>
        <span className="line-clamp-2 text-sm text-muted">{theme.description}</span>
      </span>
      <span className="flex flex-col gap-1.5">
        <span className="flex justify-between gap-2 text-sm">
          <span className="font-semibold">{formatNumber(theme.review_count)} reviews</span>
          <span className="text-muted">{formatPercent(theme.share, 1)} of reviews</span>
        </span>
        <SentimentBar counts={sentiment} />
        <span className="flex justify-between gap-2 text-[13px] text-muted">
          <span>
            {formatPercent(sentiment.positive, sentiment.total)} positive ·{" "}
            {formatPercent(sentiment.negative, sentiment.total)} negative
          </span>
          <span>{formatNumber(theme.recent_count)} recent</span>
        </span>
      </span>
    </Link>
  );
}

function ThemesSection({
  entityId,
  list,
  ratingFilter,
  updating,
}: {
  entityId: string;
  list: ThemeList;
  ratingFilter: RatingFilter | null;
  /** The list shown is the previous filter's while this one loads. */
  updating: boolean;
}) {
  const [sort, setSort] = useState<Sort>("total");
  const [showQuiet, setShowQuiet] = useState(false);
  const sorted = sortThemes(list.themes, sort);
  const active = sorted.filter((theme) => !theme.quiet);
  const quiet = sorted.filter((theme) => theme.quiet);
  const windowEnd = list.as_of ? formatDate(list.as_of) : "the latest review";

  return (
    <section className={`${card} overflow-hidden`}>
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line px-5 py-4">
        <div>
          <h2 className="text-lg font-semibold">Themes</h2>
          <p className="text-sm text-muted">
            {sort === "total"
              ? "Ranked by the number of reviews that mention each theme"
              : `Ranked by mentions in the ${list.recent_days} days to ${windowEnd}, then by total`}
          </p>
          {ratingFilter && (
            <p className="mt-1 text-sm font-semibold">
              {updating
                ? `Counting the reviews ${describeRatingFilter(ratingFilter)}…`
                : `Only the ${formatNumber(list.total_reviews)} reviews ${describeRatingFilter(ratingFilter)}`}
            </p>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <ExportButton
              path={`/entities/${entityId}/exports/themes.csv${ratingQuery(ratingFilter)}`}
              label="Export themes"
            />
            <ExportButton
              path={`/entities/${entityId}/exports/reviews.csv${ratingQuery(ratingFilter)}`}
              label="Export reviews"
            />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <SentimentLegend />
          <div role="group" aria-label="Sort themes" className="flex overflow-hidden rounded-lg border border-line-strong">
            {(
              [
                ["total", "Most reviews"],
                ["recent", "Most recent"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                aria-pressed={sort === key}
                onClick={() => setSort(key)}
                className={`px-3.5 py-2 text-sm ${sort === key ? "bg-primary text-on-primary" : "bg-surface text-fg"}`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="flex flex-col">
        {active.map((theme) => (
          <ThemeRow key={theme.id} entityId={entityId} theme={theme} query={ratingQuery(ratingFilter)} />
        ))}
      </div>
      {quiet.length > 0 && (
        <div className="border-t border-line">
          <button
            type="button"
            aria-expanded={showQuiet}
            onClick={() => setShowQuiet(!showQuiet)}
            className="w-full px-5 py-3 text-left text-sm text-link hover:underline"
          >
            {showQuiet ? "▾" : "▸"} {quiet.length} quiet {quiet.length === 1 ? "theme" : "themes"} (no mentions in the
            last {list.recent_days} days)
          </button>
          {showQuiet &&
            quiet.map((theme) => (
              <ThemeRow key={theme.id} entityId={entityId} theme={theme} query={ratingQuery(ratingFilter)} />
            ))}
        </div>
      )}
    </section>
  );
}

function EmergingTopics({ entityId, topics, query }: { entityId: string; topics: ThemeSummary[]; query: string }) {
  if (topics.length === 0) return null;
  const byCount = [...topics].sort((a, b) => b.review_count - a.review_count);
  return (
    <section className={`${card} flex flex-col gap-3 px-5 py-4`}>
      <div>
        <h2 className="text-lg font-semibold">Emerging topics</h2>
        <p className="text-sm text-muted">
          Specific points no theme covers yet. A topic becomes a theme once enough reviews raise it.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {byCount.map((topic) => (
          <Link
            key={topic.id}
            href={`/entities/${entityId}/themes/${topic.id}${query}`}
            className="rounded-full border border-line bg-bg px-3 py-1.5 text-sm hover:border-line-strong"
          >
            {topic.name} <span className="text-muted">{formatNumber(topic.review_count)}</span>
          </Link>
        ))}
      </div>
    </section>
  );
}

function NoResults({ entity }: { entity: EntityDetail }) {
  if (isActive(entity.latest_run)) return null;
  const message =
    entity.review_count === 0
      ? entity.is_owner
        ? "Upload a CSV of reviews to get started."
        : "This entity has no reviews yet."
      : entity.review_count < 50
        ? `Theme discovery needs at least 50 reviews; this entity has ${entity.review_count}.`
        : "This entity hasn't been analyzed yet.";
  return (
    <div className={`${card} flex flex-col items-start gap-3 p-6`}>
      <p>{message}</p>
      {entity.is_owner && (
        <Link href={`/entities/${entity.id}/upload`} className={buttonPrimary}>
          Upload reviews
        </Link>
      )}
    </div>
  );
}

/** What the results area shows: the theme list, or each theme's ratings against the rest. */
type View = "themes" | "ratings";
const VIEWS: { key: View; label: string }[] = [
  { key: "themes", label: "Themes" },
  { key: "ratings", label: "Ratings by theme" },
];

export default function EntityPage() {
  const { entityId } = useParams<{ entityId: string }>();
  const entity = useEntity(entityId);
  // The rating filter lives in the URL, so opening a theme, its back link, the browser's back
  // button and a reload all keep it.
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const ratingFilter = useMemo(() => readRatingFilter(searchParams), [searchParams]);
  const [view, setView] = useState<View>("themes");
  const themes = useThemes(entityId, ratingFilter);
  const ratings = useRatings(entityId);
  // Ratings by theme are compared on the scale with the most ratings (the card lists it first).
  const themeRatings = useThemeRatings(entityId, ratings.data?.scales[0]?.scale);
  useRefreshWhenRunEnds(entityId, entity.data?.latest_run);

  if (entity.error instanceof ApiError && entity.error.status === 404) {
    return (
      <Page>
        <BackLink href="/">Entities</BackLink>
        <p className="mt-4">This entity doesn&apos;t exist, or you don&apos;t have access to it.</p>
      </Page>
    );
  }
  if (entity.isPending) return <Page><Loading /></Page>;
  if (!entity.data) return <Page><ErrorNote error={entity.error} /></Page>;

  const detail = entity.data;
  const list = themes.data;
  // While a new rating filter loads, the previous list stays up (dimmed) instead of a spinner.
  const updating = themes.isPlaceholderData;
  const latest = list?.as_of ? ` · latest review ${formatDate(list.as_of)}` : "";
  // The ratings view needs analyzed themes and rated reviews; without them there is no switch.
  const canRate = Boolean(list && list.themes.length > 0 && themeRatings.data?.themes.length);
  const showRatings = canRate && view === "ratings";

  function filterRatings(filter: RatingFilter | null) {
    router.replace(`${pathname}${ratingQuery(filter)}`, { scroll: false });
    // The rating bars filter the theme list, so choosing one shows it.
    if (filter) setView("themes");
  }

  return (
    <Page>
      <div className="flex flex-col gap-6">
        <div>
          <BackLink href="/">Entities</BackLink>
          <div className="mt-1 flex flex-wrap items-start justify-between gap-4">
            <div className="flex min-w-0 flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2.5">
                <h1 className="font-display text-[30px] leading-tight font-semibold">{detail.name}</h1>
                {detail.is_public && (
                  <span className="rounded-full bg-subtle px-2.5 py-0.5 text-xs font-semibold text-muted">
                    Sample · read only
                  </span>
                )}
              </div>
              {detail.description && <p className="text-muted">{detail.description}</p>}
              <p className="text-sm text-muted">
                {formatNumber(detail.review_count)} reviews{latest}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {list?.as_of && list.themes.length > 0 && (
                <Link href={`/entities/${entityId}/changes`} className={buttonSecondary}>
                  Period comparison
                </Link>
              )}
              {detail.is_owner && <EntityActions entity={detail} />}
            </div>
          </div>
        </div>

        <RunBanner run={detail.latest_run} />
        {ratings.data && (
          <RatingsCard ratings={ratings.data} filter={ratingFilter} onFilterChange={filterRatings} />
        )}
        {canRate && (
          <div className="self-start">
            <Toggle label="View" options={VIEWS} value={view} onChange={setView} />
          </div>
        )}
        <ErrorNote error={themes.error} />
        {themes.isPending && <Loading label="Loading themes…" />}
        {showRatings && themeRatings.data && (
          <RatingDrivers
            entityId={entityId}
            data={themeRatings.data}
            several={(ratings.data?.scales.length ?? 0) > 1}
          />
        )}
        {!showRatings && (
          <>
            {updating && list?.themes.length === 0 && <Loading label="Loading themes…" />}
            {list && !updating && list.themes.length === 0 && !ratingFilter && <NoResults entity={detail} />}
            {list && !updating && list.themes.length === 0 && ratingFilter && (
              <p className={`${card} p-6`}>No theme is raised by reviews {describeRatingFilter(ratingFilter)}.</p>
            )}
            <div
              aria-busy={updating}
              className={`flex flex-col gap-6 transition-opacity ${updating ? "opacity-50" : ""}`}
            >
              {list && list.themes.length > 0 && (
                <ThemesSection entityId={entityId} list={list} ratingFilter={ratingFilter} updating={updating} />
              )}
              {list?.candidates && (
                <EmergingTopics entityId={entityId} topics={list.candidates} query={ratingQuery(ratingFilter)} />
              )}
              {list && list.classified_reviews > 0 && (
                <p className="text-sm text-muted">
                  {formatPercent(list.general.review_count, list.classified_reviews)} of reviews (
                  {formatNumber(list.general.review_count)}) raise no specific topic and are not ranked.
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </Page>
  );
}
