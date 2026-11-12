"use client";

import Link from "next/link";
import { useState } from "react";

import { ExportButton } from "@/components/ExportButton";
import { cell, numberCell, SortHeading, sortRows, useSorting } from "@/components/SortableTable";
import { card } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import { useLayout } from "@/lib/layout";
import type { RatingScale, ThemeRatingImpact, ThemeRatings } from "@/lib/types";

/** An average rating as the scale shows it: "4.1" on numbers, "78% positive" on binary, "2.4" (a position) on labels. */
export function formatAverage(value: number, scale: RatingScale): string {
  if (scale.type === "binary") return `${Math.round(value * 100)}% positive`;
  if (scale.type === "labels") return (value + 1).toFixed(1); // label index 0 is position 1
  return value.toFixed(2);
}

/** An effect on the overall average, signed: "−0.13" on numbers, "−4 pts" on binary, "−0.10" on labels. */
export function formatEffect(value: number, scale: RatingScale): string {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  if (scale.type === "binary") {
    const points = Math.round(Math.abs(value) * 100);
    return points === 0 ? (value === 0 ? "0 pts" : `${sign}<1 pt`) : `${sign}${points} ${points === 1 ? "pt" : "pts"}`;
  }
  const rounded = Math.abs(value).toFixed(2);
  if (rounded === "0.00") return value === 0 ? "0.00" : `${sign}<0.01`;
  return `${sign}${rounded}`;
}

function describeUnits(scale: RatingScale): string {
  if (scale.type === "numeric") return `ratings from ${scale.min} to ${scale.max}`;
  if (scale.type === "binary") return "the share of positive ratings";
  return `positions from 1 (${scale.labels[0]}) to ${scale.labels.length} (${scale.labels.at(-1)})`;
}

/** A bar from the middle: left for a theme that lowers the average, right for one that raises it. */
function EffectBar({ effect, largest, significant }: { effect: number; largest: number; significant: boolean }) {
  const width = largest ? `${(Math.abs(effect) / largest) * 50}%` : "0";
  const tone = !significant ? "bg-line-strong" : effect < 0 ? "bg-neg" : "bg-pos";
  return (
    <div className="relative h-2 w-full rounded-full bg-subtle" aria-hidden="true">
      <span className="absolute top-[-2px] left-1/2 h-3 w-px bg-line-strong" />
      <span
        className={`absolute top-0 h-2 rounded-full ${tone}`}
        style={effect >= 0 ? { left: "50%", width } : { right: "50%", width }}
      />
    </div>
  );
}

function ImpactRow({
  entityId,
  impact,
  scale,
  largest,
}: {
  entityId: string;
  impact: ThemeRatingImpact;
  scale: RatingScale;
  largest: number;
}) {
  return (
    <li className="grid grid-cols-1 gap-x-6 gap-y-2 border-t border-line px-5 py-3.5 sm:grid-cols-[minmax(0,1fr)_220px_200px] sm:items-center">
      <div className="min-w-0">
        <Link href={`/entities/${entityId}/themes/${impact.id}`} className="font-semibold hover:underline">
          {impact.name}
        </Link>
        {impact.kind === "candidate" && (
          <span className="ml-2 rounded-full bg-subtle px-2 py-0.5 text-xs font-semibold text-muted">Emerging</span>
        )}
        <p className="text-sm text-muted">{formatNumber(impact.rated_reviews)} rated reviews</p>
      </div>
      <p className="text-sm">
        <strong>{formatAverage(impact.average_with, scale)}</strong>
        <span className="text-muted">
          {" "}
          with · {impact.average_without === null ? "—" : formatAverage(impact.average_without, scale)} without
        </span>
      </p>
      <div className="flex flex-col gap-1.5 text-sm">
        <EffectBar effect={impact.effect} largest={largest} significant={impact.significant} />
        <span
          className={
            !impact.significant
              ? "text-muted"
              : impact.effect < 0
                ? "font-semibold text-neg-fg"
                : "font-semibold text-pos-fg"
          }
        >
          {formatEffect(impact.effect, scale)} on the average
        </span>
      </div>
    </li>
  );
}

function Group({
  title,
  impacts,
  ...shared
}: {
  title: string;
  impacts: ThemeRatingImpact[];
  entityId: string;
  scale: RatingScale;
  largest: number;
}) {
  if (impacts.length === 0) return null;
  return (
    <div>
      <h3 className="border-t border-line bg-subtle px-5 py-2 text-xs font-semibold tracking-wide text-muted uppercase">
        {title}
      </h3>
      <ul>
        {impacts.map((impact) => (
          <ImpactRow key={impact.id} impact={impact} {...shared} />
        ))}
      </ul>
    </div>
  );
}

type SortKey = "name" | "rated" | "with" | "without" | "effect";

const VALUE: Record<SortKey, (impact: ThemeRatingImpact) => number | string> = {
  name: (impact) => impact.name,
  rated: (impact) => impact.rated_reviews,
  with: (impact) => impact.average_with,
  // A theme every rated review raises has no "without"; it sorts below every real one.
  without: (impact) => impact.average_without ?? Number.NEGATIVE_INFINITY,
  effect: (impact) => impact.effect,
};

/** What the ratings comparison says, above both the card and the table. */
function Explanation({ data, scale, several }: { data: ThemeRatings; scale: RatingScale; several: boolean }) {
  return (
    <p className="mt-1 max-w-3xl text-sm text-muted">
      The average rating of reviews that mention each theme, against reviews that don&apos;t, across{" "}
      {formatNumber(data.rated_reviews)} rated reviews averaging {formatAverage(data.average!, scale)}
      {several ? ` (${describeUnits(scale)})` : ""}. The effect is how much higher or lower the overall average
      would be if those reviews were rated like the rest, so it weighs both how different a theme&apos;s ratings
      are and how often it comes up. It shows an association, not proof that the theme causes the rating.
    </p>
  );
}

/**
 * The desktop layout's ratings by theme: one sortable table, by default the themes that raise the
 * average most first and those that lower it most last. Themes whose ratings don't differ
 * significantly are left out until asked for, and then shown faded. The exports are in the filter bar.
 */
function RatingTable({ entityId, data, scale, several }: { entityId: string; data: ThemeRatings; scale: RatingScale; several: boolean }) {
  const [showAll, setShowAll] = useState(false);
  const sorting = useSorting<SortKey>("effect", "desc", ["name"]);
  const significant = data.themes.filter((impact) => impact.significant);
  const shown = showAll ? data.themes : significant;
  const rows = sortRows(shown, VALUE, sorting, (a, b) => b.rated_reviews - a.rated_reviews);
  const largest = Math.max(0, ...data.themes.map((impact) => Math.abs(impact.effect)));
  const others = data.themes.length - significant.length;

  return (
    <section className={`${card} overflow-hidden`}>
      <div className="flex flex-wrap items-end justify-between gap-3 px-5 pt-4 pb-3">
        <div>
          <h2 className="text-lg font-semibold">Ratings by theme</h2>
          <Explanation data={data} scale={scale} several={several} />
        </div>
        {others > 0 && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={showAll} onChange={(event) => setShowAll(event.target.checked)} className="size-4" />
            Show the {others} {others === 1 ? "theme" : "themes"} with no significant difference
          </label>
        )}
      </div>
      {rows.length === 0 ? (
        <p className="border-t border-line px-5 py-4 text-sm">No theme&apos;s ratings differ significantly from the rest.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[880px] border-collapse text-[15px]">
            <thead>
              <tr>
                <SortHeading {...sorting} column="name" label="Theme" className="pl-5 text-left" />
                <SortHeading {...sorting} column="rated" label="Rated reviews" className="w-[130px] text-right" />
                <SortHeading {...sorting} column="with" label="Average with" className="w-[130px] text-right" />
                <SortHeading {...sorting} column="without" label="Average without" className="w-[150px] text-right" />
                <SortHeading {...sorting} column="effect" label="Effect on the average" className="w-[320px] pr-5 text-right" />
              </tr>
            </thead>
            <tbody>
              {rows.map((impact) => {
                const tone = !impact.significant ? "text-muted" : impact.effect < 0 ? "font-semibold text-neg-fg" : "font-semibold text-pos-fg";
                return (
                  <tr key={impact.id} className={`hover:bg-bg ${impact.significant ? "" : "opacity-60"}`}>
                    <td className={`${cell} pl-5`}>
                      <Link href={`/entities/${entityId}/themes/${impact.id}`} className="font-semibold hover:underline">
                        {impact.name}
                      </Link>
                      {impact.kind === "candidate" && (
                        <span className="ml-2 rounded-full bg-subtle px-2 py-0.5 text-[11px] font-semibold text-muted">Emerging</span>
                      )}
                    </td>
                    <td className={numberCell}>{formatNumber(impact.rated_reviews)}</td>
                    <td className={numberCell}>{formatAverage(impact.average_with, scale)}</td>
                    <td className={numberCell}>
                      {impact.average_without === null ? "—" : formatAverage(impact.average_without, scale)}
                    </td>
                    <td className={`${cell} pr-5`}>
                      <span className="flex items-center gap-4">
                        <span className="min-w-0 flex-1">
                          <EffectBar effect={impact.effect} largest={largest} significant={impact.significant} />
                        </span>
                        <span className={`w-[72px] text-right tabular-nums whitespace-nowrap ${tone}`}>
                          {formatEffect(impact.effect, scale)}
                        </span>
                      </span>
                      {!impact.significant && <span className="mt-0.5 block text-right text-[13px] text-muted">Not significant</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/**
 * Which themes go with lower or higher ratings: each theme's average rating against the reviews that
 * don't raise it, ranked by its effect on the overall average. Significant differences are split into
 * those that raise and those that lower the average; the rest are collapsed.
 */
export function RatingDrivers({
  entityId,
  data,
  several,
}: {
  entityId: string;
  data: ThemeRatings;
  /** The entity has more than one rating scale, so say which one this is. */
  several: boolean;
}) {
  const [showRest, setShowRest] = useState(false);
  const desktop = useLayout() === "desktop";
  if (!data.scale || data.themes.length === 0 || data.average === null) return null;
  const scale = data.scale;
  if (desktop) return <RatingTable entityId={entityId} data={data} scale={scale} several={several} />;
  const largest = Math.max(0, ...data.themes.map((impact) => Math.abs(impact.effect)));
  const lowering = data.themes.filter((impact) => impact.significant && impact.effect < 0);
  const raising = data.themes.filter((impact) => impact.significant && impact.effect > 0).reverse();
  const rest = data.themes.filter((impact) => !lowering.includes(impact) && !raising.includes(impact));
  const shared = { entityId, scale, largest };

  return (
    <section className={`${card} overflow-hidden`}>
      <div className="px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h2 className="text-lg font-semibold">Ratings by theme</h2>
          <ExportButton path={`/entities/${entityId}/exports/themes.csv`} />
        </div>
        <Explanation data={data} scale={scale} several={several} />
      </div>
      {lowering.length + raising.length === 0 && (
        <p className="border-t border-line px-5 py-4 text-sm">No theme&apos;s ratings differ significantly from the rest.</p>
      )}
      <Group title="Raise the average" impacts={raising} {...shared} />
      <Group title="Lower the average" impacts={lowering} {...shared} />
      {rest.length > 0 && (
        <div className="border-t border-line">
          <button
            type="button"
            aria-expanded={showRest}
            onClick={() => setShowRest(!showRest)}
            className="w-full px-5 py-3 text-left text-sm text-link hover:underline"
          >
            {showRest ? "▾" : "▸"} {rest.length} {rest.length === 1 ? "theme" : "themes"} with no significant difference
          </button>
          {showRest && (
            <ul>
              {rest.map((impact) => (
                <ImpactRow key={impact.id} impact={impact} {...shared} />
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
