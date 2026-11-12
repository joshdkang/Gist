"use client";

import { useState } from "react";

/** Shared by the desktop tables: a column heading that sorts, and the sort state behind it. */

export type Direction = "asc" | "desc";

export interface Sorting<Key extends string> {
  sortKey: Key;
  direction: Direction;
  onSort: (column: Key) => void;
}

/**
 * The column a table is sorted by and its direction. Choosing the sorted column again reverses it;
 * choosing another sorts by it in its first direction (`ascendingFirst` for text, largest first otherwise).
 */
export function useSorting<Key extends string>(
  initialKey: Key,
  initialDirection: Direction = "desc",
  ascendingFirst: Key[] = [],
): Sorting<Key> {
  const [sortKey, setSortKey] = useState<Key>(initialKey);
  const [direction, setDirection] = useState<Direction>(initialDirection);
  return {
    sortKey,
    direction,
    onSort: (column) => {
      if (column === sortKey) setDirection(direction === "desc" ? "asc" : "desc");
      else {
        setSortKey(column);
        setDirection(ascendingFirst.includes(column) ? "asc" : "desc");
      }
    },
  };
}

/** Sorts rows by a value per column, ties broken by `tieBreak` (which keeps the order stable). */
export function sortRows<Row, Key extends string>(
  rows: Row[],
  values: Record<Key, (row: Row) => number | string>,
  { sortKey, direction }: Pick<Sorting<Key>, "sortKey" | "direction">,
  tieBreak: (a: Row, b: Row) => number,
): Row[] {
  return [...rows].sort((a, b) => {
    const x = values[sortKey](a);
    const y = values[sortKey](b);
    const order = typeof x === "string" ? x.localeCompare(y as string) : x - (y as number);
    return (direction === "asc" ? order : -order) || tieBreak(a, b);
  });
}

/** A column heading that sorts by its column, showing the direction when it is the one sorted by. */
export function SortHeading<Key extends string>({
  column,
  label,
  className = "",
  sortKey,
  direction,
  onSort,
}: {
  column: Key;
  label: string;
  className?: string;
} & Sorting<Key>) {
  const active = column === sortKey;
  // Only the sorted heading has an arrow, after its label; the others take no room for one, so every
  // heading, arrow and all, ends where its column's numbers do.
  const arrow = active ? (
    <span aria-hidden="true" className="ml-1">
      {direction === "desc" ? "↓" : "↑"}
    </span>
  ) : null;
  return (
    <th
      scope="col"
      aria-sort={active ? (direction === "desc" ? "descending" : "ascending") : "none"}
      className={`border-y border-line px-3 py-1.5 text-[13px] font-semibold whitespace-nowrap ${className}`}
    >
      <button type="button" onClick={() => onSort(column)} className={`py-1 hover:text-fg ${active ? "text-fg" : "text-muted"}`}>
        {label}
        {arrow}
      </button>
    </th>
  );
}

/** A heading for a column that doesn't sort. */
export function PlainHeading({ label, className = "" }: { label: string; className?: string }) {
  return (
    <th scope="col" className={`border-y border-line px-3 py-1.5 text-[13px] font-semibold whitespace-nowrap text-muted ${className}`}>
      {label}
    </th>
  );
}

/** Table cells: a text cell, and a right-aligned number cell. */
export const cell = "border-b border-subtle px-3 py-3 align-top";
export const numberCell = `${cell} text-right tabular-nums whitespace-nowrap`;
