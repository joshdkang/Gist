"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { EntityActions } from "@/components/EntityActions";
import { RunBanner } from "@/components/RunBanner";
import { SentimentBar, SentimentLegend } from "@/components/SentimentBar";
import { BackLink, buttonPrimary, card, ErrorNote, Loading, Page } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { formatDate, formatNumber, formatPercent } from "@/lib/format";
import { isActive, useEntity, useRefreshWhenRunEnds, useThemes } from "@/lib/queries";
import type { EntityDetail, ThemeList, ThemeSummary } from "@/lib/types";

type Sort = "total" | "recent";

/** The API ranks by recent mentions, then total; "Most reviews" re-sorts the same list here. */
function sortThemes(themes: ThemeSummary[], sort: Sort): ThemeSummary[] {
  if (sort === "recent") return themes;
  return [...themes].sort((a, b) => b.review_count - a.review_count || b.recent_count - a.recent_count);
}

function ThemeRow({ entityId, theme }: { entityId: string; theme: ThemeSummary }) {
  const { sentiment } = theme;
  return (
    <Link
      href={`/entities/${entityId}/themes/${theme.id}`}
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

function ThemesSection({ entityId, list }: { entityId: string; list: ThemeList }) {
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
          <ThemeRow key={theme.id} entityId={entityId} theme={theme} />
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
            quiet.map((theme) => <ThemeRow key={theme.id} entityId={entityId} theme={theme} />)}
        </div>
      )}
    </section>
  );
}

function EmergingTopics({ entityId, topics }: { entityId: string; topics: ThemeSummary[] }) {
  if (topics.length === 0) return null;
  const byCount = [...topics].sort((a, b) => b.review_count - a.review_count);
  return (
    <section className={`${card} flex flex-col gap-3 px-5 py-4`}>
      <div>
        <h2 className="text-lg font-semibold">Emerging topics</h2>
        <p className="text-sm text-muted">
          Specific points no theme covers yet. A topic becomes a theme once 5% of reviews raise it.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {byCount.map((topic) => (
          <Link
            key={topic.id}
            href={`/entities/${entityId}/themes/${topic.id}`}
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

export default function EntityPage() {
  const { entityId } = useParams<{ entityId: string }>();
  const entity = useEntity(entityId);
  const themes = useThemes(entityId);
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
  const latest = list?.as_of ? ` · latest review ${formatDate(list.as_of)}` : "";

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
            {detail.is_owner && <EntityActions entity={detail} />}
          </div>
        </div>

        <RunBanner run={detail.latest_run} />
        <ErrorNote error={themes.error} />
        {themes.isPending && <Loading label="Loading themes…" />}
        {list && list.themes.length === 0 && <NoResults entity={detail} />}
        {list && list.themes.length > 0 && <ThemesSection entityId={entityId} list={list} />}
        {list?.candidates && <EmergingTopics entityId={entityId} topics={list.candidates} />}
        {list && list.classified_reviews > 0 && (
          <p className="text-sm text-muted">
            {formatPercent(list.general.review_count, list.classified_reviews)} of reviews (
            {formatNumber(list.general.review_count)}) raise no specific topic and are not ranked.
          </p>
        )}
      </div>
    </Page>
  );
}
