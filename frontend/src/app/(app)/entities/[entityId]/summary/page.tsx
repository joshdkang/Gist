"use client";

import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useMemo } from "react";

import { PeriodPicker, readPeriodChoice, SUMMARY_CHANGES_PREFIX, usePeriodChoice } from "@/components/PeriodPicker";
import { formatAverage, formatEffect } from "@/components/RatingDrivers";
import { SentimentBar, SentimentLegend } from "@/components/SentimentBar";
import { periodName } from "@/components/TrendChart";
import { BackLink, buttonPrimary, card, ErrorNote, Loading } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { formatChange, formatDate, formatNumber, formatPercent, formatShare, negativeShare } from "@/lib/format";
import { useEntity, useRatings, useThemeChanges, useThemeRatings, useThemes } from "@/lib/queries";
import { describeFilters, filterQuery, readPeriodsFilter, readSearch } from "@/lib/periodFilter";
import { readRatingFilter } from "@/lib/ratingFilter";
import type { RatingScale, ThemeChange, ThemeChanges, ThemeList, ThemeRatingImpact, ThemeRatings } from "@/lib/types";

/*
 * A one- to two-page report on an entity, to print or save as a PDF for someone who won't open the app.
 * It shows the same numbers as the entity page, the period comparison and ratings by theme (it calls the
 * same endpoints), laid out for paper: everything expanded, no controls. The period picker and the print
 * button are on screen only. Sentiment colours follow the palette the user has chosen.
 */

/** Below this many reviews in a period, only large changes can be told from chance (as on the comparison page). */
const FEW_REVIEWS = 20;

const cell = "py-1.5 pr-3 align-top";
const headCell = "pb-2 pr-3 text-left text-xs font-semibold text-muted";
const number = "text-right tabular-nums";

function SectionTitle({ children, note }: { children: React.ReactNode; note?: React.ReactNode }) {
  return (
    <div className="break-after-avoid">
      <h2 className="font-display text-xl font-semibold">{children}</h2>
      {note && <p className="mt-0.5 text-sm text-muted">{note}</p>}
    </div>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-lg border border-line px-4 py-3">
      <p className="text-xs font-semibold text-muted">{label}</p>
      <p className="font-display text-2xl font-semibold tabular-nums">{value}</p>
      {note && <p className="text-xs text-muted">{note}</p>}
    </div>
  );
}

function ThemesTable({ list }: { list: ThemeList }) {
  const themes = [...list.themes].sort((a, b) => b.review_count - a.review_count || b.recent_count - a.recent_count);
  return (
    <div className="overflow-x-auto print:overflow-visible">
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr className="border-b border-line-strong">
          <th className={headCell}>Theme</th>
          <th className={`${headCell} ${number}`}>Reviews</th>
          <th className={`${headCell} ${number}`}>Share</th>
          <th className={`${headCell} w-[180px]`}>Sentiment</th>
          <th className={`${headCell} ${number} pr-0`}>Recent</th>
        </tr>
      </thead>
      <tbody>
        {themes.map((theme) => (
          <tr key={theme.id} className="break-inside-avoid border-b border-line">
            <td className={cell}>
              <span className="font-semibold">{theme.name}</span>
              {theme.quiet && <span className="ml-1.5 text-xs text-muted">(quiet)</span>}
              <span className="line-clamp-1 text-xs text-muted">{theme.description}</span>
            </td>
            <td className={`${cell} ${number}`}>{formatNumber(theme.review_count)}</td>
            <td className={`${cell} ${number}`}>{formatPercent(theme.share, 1)}</td>
            <td className={cell}>
              <SentimentBar counts={theme.sentiment} height="h-2" />
              <span className="mt-1 block text-[11px] whitespace-nowrap text-muted">
                {formatPercent(theme.sentiment.positive, theme.sentiment.total)} positive ·{" "}
                {formatPercent(theme.sentiment.negative, theme.sentiment.total)} negative
              </span>
            </td>
            <td className={`${cell} ${number} pr-0`}>{formatNumber(theme.recent_count)}</td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

interface ChangeLine {
  change: ThemeChange;
  before: number;
  now: number;
}

/** The significant changes of one kind, largest first. */
function ChangeTable({
  title,
  lines,
  others,
  negative,
  previousName,
  currentName,
}: {
  title: string;
  lines: ChangeLine[];
  /** Themes compared whose change was not significant. */
  others: number;
  /** A rise in the negative share is bad, so it takes the negative colour. */
  negative: boolean;
  previousName: string;
  currentName: string;
}) {
  const what = negative ? "negative share" : "share of mentions";
  const othersNote =
    others > 0 ? `${others} ${others === 1 ? "theme" : "themes"} with no significant change in ${what}.` : "";
  return (
    <div className="break-inside-avoid">
      <h3 className="text-sm font-semibold">{title}</h3>
      {lines.length === 0 ? (
        <p className="mt-1 text-sm">No theme&apos;s {what} changed significantly.</p>
      ) : (
        <div className="overflow-x-auto print:overflow-visible">
        <table className="mt-1 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line-strong">
              <th className={headCell}>Theme</th>
              <th className={`${headCell} ${number} w-[110px]`}>{previousName}</th>
              <th className={`${headCell} ${number} w-[110px]`}>{currentName}</th>
              <th className={`${headCell} ${number} w-[90px] pr-0`}>Change</th>
            </tr>
          </thead>
          <tbody>
            {lines.map(({ change, before, now }) => {
              const delta = now - before;
              const tone = !negative ? "" : delta > 0 ? "text-neg-fg" : "text-pos-fg";
              return (
                <tr key={change.id} className="break-inside-avoid border-b border-line">
                  <td className={cell}>
                    {change.name}
                    {change.kind === "candidate" && <span className="ml-1.5 text-xs text-muted">(emerging)</span>}
                  </td>
                  <td className={`${cell} ${number}`}>{formatShare(before)}</td>
                  <td className={`${cell} ${number}`}>{formatShare(now)}</td>
                  <td className={`${cell} ${number} pr-0 font-semibold ${tone}`}>{formatChange(delta)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
      {othersNote && <p className="mt-1.5 text-xs text-muted">{othersNote}</p>}
    </div>
  );
}

function ChangesSection({ data }: { data: ThemeChanges }) {
  if (!data.period_start) return <p className="text-sm">None of this entity&apos;s reviews has a date.</p>;
  const currentName = periodName(data.period_start, data.interval);
  const previousName = data.previous_start ? periodName(data.previous_start, data.interval) : "";
  const largestFirst = (a: ChangeLine, b: ChangeLine) =>
    Math.abs(b.now - b.before) - Math.abs(a.now - a.before) || a.change.name.localeCompare(b.change.name);

  const share = data.themes
    .filter((change) => change.share_change_clear)
    .map((change) => ({ change, before: change.previous_share, now: change.current_share }))
    .sort(largestFirst);
  // A negative share needs reviews of the theme in both periods.
  const comparable = data.themes.filter(
    (change) => negativeShare(change.previous) !== null && negativeShare(change.current) !== null,
  );
  const negative = comparable
    .filter((change) => change.negative_change_clear)
    .map((change) => ({ change, before: negativeShare(change.previous)!, now: negativeShare(change.current)! }))
    .sort(largestFirst);

  return (
    <section className="space-y-4">
      <SectionTitle
        note={
          <>
            {formatNumber(data.current_reviews)} reviews in {currentName} against {formatNumber(data.previous_reviews)} in{" "}
            {previousName}. Only significant changes are listed.
          </>
        }
      >
        What changed: {currentName} compared with {previousName}
      </SectionTitle>
      {data.previous_reviews === 0 ? (
        <p className="text-sm">{previousName} has no reviews, so there is nothing to compare with.</p>
      ) : (
        <>
          {Math.min(data.current_reviews, data.previous_reviews) < FEW_REVIEWS && (
            <p className="rounded-lg border border-warn-line bg-warn-bg px-4 py-2 text-sm">
              One of these periods has fewer than {FEW_REVIEWS} reviews, so only large changes can be confirmed as
              significant.
            </p>
          )}
          <ChangeTable
            title="Share of the period's reviews that mention each theme"
            lines={share}
            others={data.themes.length - share.length}
            negative={false}
            previousName={previousName}
            currentName={currentName}
          />
          <ChangeTable
            title="Share of each theme's reviews that are negative"
            lines={negative}
            others={comparable.length - negative.length}
            negative
            previousName={previousName}
            currentName={currentName}
          />
        </>
      )}
    </section>
  );
}

function ImpactRows({ title, impacts, scale }: { title: string; impacts: ThemeRatingImpact[]; scale: RatingScale }) {
  if (impacts.length === 0) return null;
  return (
    <>
      <tr className="break-after-avoid">
        <th colSpan={5} className="pt-3 pb-1 text-left text-sm font-semibold">
          {title}
        </th>
      </tr>
      {impacts.map((impact) => (
        <tr key={impact.id} className="break-inside-avoid border-b border-line">
          <td className={cell}>
            {impact.name}
            {impact.kind === "candidate" && <span className="ml-1.5 text-xs text-muted">(emerging)</span>}
          </td>
          <td className={`${cell} ${number}`}>{formatNumber(impact.rated_reviews)}</td>
          <td className={`${cell} ${number}`}>{formatAverage(impact.average_with, scale)}</td>
          <td className={`${cell} ${number}`}>
            {impact.average_without === null ? "—" : formatAverage(impact.average_without, scale)}
          </td>
          <td className={`${cell} ${number} pr-0 font-semibold ${impact.effect < 0 ? "text-neg-fg" : "text-pos-fg"}`}>
            {formatEffect(impact.effect, scale)}
          </td>
        </tr>
      ))}
    </>
  );
}

function RatingsSection({ data }: { data: ThemeRatings }) {
  if (!data.scale || data.average === null || data.themes.length === 0) return null;
  const scale = data.scale;
  const raising = data.themes.filter((impact) => impact.significant && impact.effect > 0).reverse();
  const lowering = data.themes.filter((impact) => impact.significant && impact.effect < 0);
  const others = data.themes.length - raising.length - lowering.length;
  return (
    <section className="space-y-4">
      <SectionTitle
        note={
          <>
            The average rating of reviews that mention each theme, against reviews that don&apos;t, across{" "}
            {formatNumber(data.rated_reviews)} rated reviews averaging {formatAverage(data.average, scale)}. The effect is
            how much higher or lower the overall average would be if those reviews were rated like the rest. Only
            significant differences are listed.
          </>
        }
      >
        Ratings by theme
      </SectionTitle>
      {raising.length + lowering.length === 0 && (
        <p className="text-sm">No theme&apos;s ratings differ significantly from the rest.</p>
      )}
      {raising.length + lowering.length > 0 && (
        <div className="overflow-x-auto print:overflow-visible">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line-strong">
              <th className={headCell}>Theme</th>
              <th className={`${headCell} ${number} w-[110px]`}>Rated reviews</th>
              <th className={`${headCell} ${number} w-[110px]`}>Average with</th>
              <th className={`${headCell} ${number} w-[110px]`}>Without</th>
              <th className={`${headCell} ${number} w-[90px] pr-0`}>Effect</th>
            </tr>
          </thead>
          <tbody>
            <ImpactRows title="Raise the average" impacts={raising} scale={scale} />
            <ImpactRows title="Lower the average" impacts={lowering} scale={scale} />
          </tbody>
        </table>
        </div>
      )}
      {others > 0 && (
        <p className="text-xs text-muted">
          {others} {others === 1 ? "theme" : "themes"} with no significant difference.
        </p>
      )}
    </section>
  );
}

export default function SummaryPage() {
  const { entityId } = useParams<{ entityId: string }>();
  const searchParams = useSearchParams();
  // The entity page's filters (`rating_scale` and `rating`, `interval` and `period`) narrow the themes.
  const ratingFilter = useMemo(() => readRatingFilter(searchParams), [searchParams]);
  const periodsFilter = useMemo(() => readPeriodsFilter(searchParams), [searchParams]);
  const search = useMemo(() => readSearch(searchParams), [searchParams]);
  const filtered = describeFilters(ratingFilter, periodsFilter, search);
  // The compared periods start as the link chose them (the period comparison passes its own, prefixed
  // so they don't clash with the period filter); a filter on one period compares that period with the
  // one before; changing them here only changes this page.
  const linked = readPeriodChoice(searchParams, SUMMARY_CHANGES_PREFIX);
  const choice = usePeriodChoice(
    !linked.interval && periodsFilter?.starts.length === 1
      ? { interval: periodsFilter.interval, period: periodsFilter.starts[0] }
      : linked,
  );

  const entity = useEntity(entityId);
  const themes = useThemes(entityId, ratingFilter, periodsFilter, search);
  const ratings = useRatings(entityId);
  const ratingScale = ratings.data?.scales[0];
  const themeRatings = useThemeRatings(entityId, ratingScale?.scale);
  const changes = useThemeChanges(entityId, choice.interval, choice.period, choice.compareTo);
  // Save as PDF names the file after the page's title.
  const name = entity.data?.name;
  useEffect(() => {
    if (!name) return;
    const previous = document.title;
    document.title = `${name} review summary`;
    return () => {
      document.title = previous;
    };
  }, [name]);

  if (entity.error instanceof ApiError && entity.error.status === 404) {
    return (
      <main className="mx-auto max-w-[880px] px-4 pt-6">
        <BackLink href="/">Entities</BackLink>
        <p className="mt-4">This entity doesn&apos;t exist, or you don&apos;t have access to it.</p>
      </main>
    );
  }

  const detail = entity.data;
  const list = themes.data;
  const pending = entity.isPending || themes.isPending || changes.isPending || ratings.isPending;
  const generated = new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });

  return (
    <main className="mx-auto w-full max-w-[880px] px-4 pt-6 pb-16 sm:px-6 print:max-w-none print:p-0">
      <div className="flex flex-col gap-4 print:hidden">
        <BackLink href={`/entities/${entityId}${filterQuery(ratingFilter, periodsFilter, search)}`}>{detail?.name ?? "Entity"}</BackLink>
        <div className={`${card} flex flex-wrap items-end gap-4 px-5 py-4`}>
          <PeriodPicker choice={choice} data={changes.data} />
          <button
            type="button"
            onClick={() => window.print()}
            disabled={pending}
            className={`${buttonPrimary} ml-auto h-9 px-3 text-sm`}
          >
            Print / Save as PDF
          </button>
        </div>
        <p className="text-sm text-muted">
          Choose the periods for &ldquo;What changed&rdquo;, then print. To save a PDF, pick &ldquo;Save as PDF&rdquo; as
          the printer.
        </p>
        <ErrorNote error={entity.error ?? themes.error ?? changes.error ?? ratings.error} />
      </div>

      {pending || !detail || !list ? (
        <Loading label="Preparing the summary…" />
      ) : (
        <article
          aria-busy={changes.isPlaceholderData}
          className={`print-report mt-6 space-y-8 transition-opacity print:mt-0 ${changes.isPlaceholderData ? "opacity-50" : ""}`}
        >
          <header className="flex flex-col gap-1 border-b border-line-strong pb-4">
            <p className="text-xs font-semibold tracking-wide text-muted uppercase">Review summary</p>
            <h1 className="font-display text-[28px] leading-tight font-semibold">{detail.name}</h1>
            {detail.description && <p className="text-muted">{detail.description}</p>}
            <p className="text-sm text-muted">
              {formatNumber(detail.review_count)} reviews
              {/* With a period filter, the API's as_of is the latest review in those periods. */}
              {list.as_of && !periodsFilter ? ` · latest review ${formatDate(list.as_of)}` : ""} ·{" "}
              <span suppressHydrationWarning>generated {generated}</span>
            </p>
            {filtered && (
              <p className="mt-1 text-sm font-semibold">
                The themes cover only the {formatNumber(list.total_reviews)} reviews {filtered}. What changed and
                ratings by theme cover every review.
              </p>
            )}
          </header>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Reviews analyzed" value={formatNumber(list.classified_reviews)} />
            <Stat label="Themes" value={formatNumber(list.themes.length)} />
            <Stat label="Emerging topics" value={formatNumber(list.candidates?.length ?? 0)} />
            {ratingScale ? (
              <Stat
                label="Average rating"
                value={formatAverage(ratingScale.average, ratingScale.scale)}
                note={`${formatNumber(ratingScale.count)} rated reviews`}
              />
            ) : (
              <Stat
                label="No specific topic"
                value={formatPercent(list.general.review_count, list.classified_reviews)}
                note="of reviews"
              />
            )}
          </div>

          {list.themes.length === 0 ? (
            <p className="text-sm">
              {filtered ? `No theme is raised by reviews ${filtered}.` : "This entity hasn't been analyzed yet."}
            </p>
          ) : (
            <section className="space-y-3">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <SectionTitle
                  note={`Ranked by the number of reviews that mention each theme. Recent counts the mentions in the ${
                    list.recent_days
                  } days to ${list.as_of ? formatDate(list.as_of) : "the latest review"}; quiet themes have none.`}
                >
                  Themes
                </SectionTitle>
                <SentimentLegend />
              </div>
              <ThemesTable list={list} />
              {list.candidates && list.candidates.length > 0 && (
                <p className="break-inside-avoid text-sm">
                  <span className="font-semibold">Emerging topics</span>{" "}
                  <span className="text-muted">(specific points no theme covers yet): </span>
                  {[...list.candidates]
                    .sort((a, b) => b.review_count - a.review_count)
                    .map((topic) => `${topic.name} (${formatNumber(topic.review_count)})`)
                    .join(", ")}
                  .
                </p>
              )}
              {list.classified_reviews > 0 && (
                <p className="text-sm text-muted">
                  {formatPercent(list.general.review_count, list.classified_reviews)} of reviews (
                  {formatNumber(list.general.review_count)}) raise no specific topic and are not ranked.
                </p>
              )}
            </section>
          )}

          {changes.data && list.themes.length > 0 && <ChangesSection data={changes.data} />}
          {themeRatings.data && list.themes.length > 0 && <RatingsSection data={themeRatings.data} />}

          <footer className="break-inside-avoid border-t border-line-strong pt-4 text-xs text-muted">
            <p className="font-semibold text-fg">How to read this summary</p>
            <ul className="mt-1 flex list-disc flex-col gap-1 pl-4">
              <li>
                Themes are recurring topics found in the reviews by a language model, then every review is tagged with the
                themes it mentions and a sentiment for each. A review can mention several themes, so shares add up to more
                than 100%.
              </li>
              <li>
                Significant changes and differences are those unlikely to be explained by random variation in which
                customers left reviews. Because every theme is tested at once, the bar is set so that no more than about 1
                in 20 results marked significant should be a fluke.
              </li>
              <li>Ratings by theme show an association, not proof that a theme causes the rating.</li>
            </ul>
          </footer>
        </article>
      )}
    </main>
  );
}
