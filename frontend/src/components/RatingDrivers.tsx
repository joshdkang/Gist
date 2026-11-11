"use client";

import Link from "next/link";
import { useState } from "react";

import { card } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import type { RatingScale, ThemeRatingImpact, ThemeRatings } from "@/lib/types";

/** An average rating as the scale shows it: "4.1" on numbers, "78% positive" on binary, "2.4" (a position) on labels. */
function formatAverage(value: number, scale: RatingScale): string {
  if (scale.type === "binary") return `${Math.round(value * 100)}% positive`;
  if (scale.type === "labels") return (value + 1).toFixed(1); // label index 0 is position 1
  return value.toFixed(2);
}

/** An effect on the overall average, signed: "−0.13" on numbers, "−4 pts" on binary, "−0.10" on labels. */
function formatEffect(value: number, scale: RatingScale): string {
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

/**
 * Which themes go with lower or higher ratings: each theme's average rating against the reviews that
 * don't raise it, ranked by its effect on the overall average. Significant differences are split into
 * those that lower and those that raise the average; the rest are collapsed.
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
  if (!data.scale || data.themes.length === 0 || data.average === null) return null;
  const scale = data.scale;
  const largest = Math.max(0, ...data.themes.map((impact) => Math.abs(impact.effect)));
  const lowering = data.themes.filter((impact) => impact.significant && impact.effect < 0);
  const raising = data.themes.filter((impact) => impact.significant && impact.effect > 0).reverse();
  const rest = data.themes.filter((impact) => !lowering.includes(impact) && !raising.includes(impact));
  const shared = { entityId, scale, largest };

  return (
    <section className={`${card} overflow-hidden`}>
      <div className="px-5 py-4">
        <h2 className="text-lg font-semibold">Ratings by theme</h2>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          The average rating of reviews that mention each theme, against reviews that don&apos;t, across{" "}
          {formatNumber(data.rated_reviews)} rated reviews averaging {formatAverage(data.average, scale)}
          {several ? ` (${describeUnits(scale)})` : ""}. The effect is how much higher or lower the overall average
          would be if those reviews were rated like the rest, so it weighs both how different a theme&apos;s ratings
          are and how often it comes up. It shows an association, not proof that the theme causes the rating.
        </p>
      </div>
      {lowering.length + raising.length === 0 && (
        <p className="border-t border-line px-5 py-4 text-sm">No theme&apos;s ratings differ significantly from the rest.</p>
      )}
      <Group title="Lower the average" impacts={lowering} {...shared} />
      <Group title="Raise the average" impacts={raising} {...shared} />
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
