"use client";

import Link from "next/link";

import { cell, numberCell, PlainHeading, SortHeading, sortRows, useSorting } from "@/components/SortableTable";
import { card } from "@/components/ui";
import { formatDate, formatNumber } from "@/lib/format";
import type { EntityListItem } from "@/lib/types";

type SortKey = "name" | "reviews" | "themes" | "latest" | "analyzed" | "created";

const time = (value: string | null) => (value ? new Date(value).getTime() : -Infinity);

const VALUE: Record<SortKey, (entity: EntityListItem) => number | string> = {
  name: (entity) => entity.name.toLowerCase(),
  reviews: (entity) => entity.review_count,
  themes: (entity) => entity.theme_count,
  latest: (entity) => time(entity.latest_review_at),
  analyzed: (entity) => time(entity.last_analyzed_at),
  created: (entity) => time(entity.created_at),
};

/** Where the entity's analysis stands, from its latest run and whether it has themes. */
function Status({ entity }: { entity: EntityListItem }) {
  const run = entity.latest_run_status;
  const [label, dot] =
    run === "running"
      ? ["Analyzing", "bg-link"]
      : run === "queued"
        ? ["Queued", "bg-link"]
        : run === "failed"
          ? ["Last run failed", "bg-neg"]
          : entity.theme_count > 0
            ? ["Analyzed", "bg-pos"]
            : ["Not analyzed", "bg-line-strong"];
  return (
    <span className="flex items-center gap-2 whitespace-nowrap">
      <span className={`size-2 rounded-full ${dot}`} aria-hidden="true" />
      {label}
    </span>
  );
}

const date = (value: string | null) => (value ? formatDate(value) : <span className="text-muted">—</span>);

/**
 * The entity list as a sortable table, for the desktop layout: each entity's reviews, themes, newest
 * review, last analysis, when it was created (which tells apart two entities of the same name) and
 * where its analysis stands. By default the newest entities come first, as the API lists them.
 */
export function EntityTable({ entities }: { entities: EntityListItem[] }) {
  const sorting = useSorting<SortKey>("created", "desc", ["name"]);
  const rows = sortRows(entities, VALUE, sorting, (a, b) => b.id - a.id);
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full min-w-[960px] border-collapse text-[15px]">
        <thead>
          <tr>
            <SortHeading {...sorting} column="name" label="Entity" className="pl-5 text-left" />
            <SortHeading {...sorting} column="reviews" label="Reviews" className="w-[110px] text-right" />
            <SortHeading {...sorting} column="themes" label="Themes" className="w-[100px] text-right" />
            <SortHeading {...sorting} column="latest" label="Newest review" className="w-[150px] text-right" />
            <SortHeading {...sorting} column="analyzed" label="Last analyzed" className="w-[150px] text-right" />
            <SortHeading {...sorting} column="created" label="Created" className="w-[140px] text-right" />
            <PlainHeading label="Status" className="w-[170px] pr-5 text-left" />
          </tr>
        </thead>
        <tbody>
          {rows.map((entity) => (
            <tr key={entity.id} className="hover:bg-bg">
              <td className={`${cell} max-w-0 pr-6 pl-5`}>
                <Link href={`/entities/${entity.id}`} className="font-semibold hover:underline">
                  {entity.name}
                </Link>
                {entity.description && (
                  <p className="mt-0.5 truncate text-sm text-muted" title={entity.description}>
                    {entity.description}
                  </p>
                )}
              </td>
              <td className={numberCell}>{formatNumber(entity.review_count)}</td>
              <td className={numberCell}>{entity.theme_count ? formatNumber(entity.theme_count) : <span className="text-muted">—</span>}</td>
              <td className={numberCell}>{date(entity.latest_review_at)}</td>
              <td className={numberCell}>{date(entity.last_analyzed_at)}</td>
              <td className={numberCell}>{date(entity.created_at)}</td>
              <td className={`${cell} pr-5 text-sm`}>
                <Status entity={entity} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
