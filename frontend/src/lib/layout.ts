"use client";

import { useSyncExternalStore } from "react";

/**
 * Which page layout to show:
 * - desktop: wide pages, side-by-side cards, a filter bar and a sortable theme table, for analysis on
 *   a laptop or monitor.
 * - mobile: the narrow stacked pages with theme cards, for a phone or a small window.
 *
 * Until the user chooses, the screen width decides (desktop from 1024px). A choice is kept in this
 * browser only, unlike the palette, which follows the account: a layout suits a device, and a choice
 * made on a laptop shouldn't put the wide table on the same person's phone.
 */
export type Layout = "desktop" | "mobile";

const STORAGE_KEY = "gist-layout";
const DESKTOP_QUERY = "(min-width: 1024px)";

const isLayout = (value: unknown): value is Layout => value === "desktop" || value === "mobile";

let chosen: Layout | null | undefined; // undefined: not read from storage yet
const listeners = new Set<() => void>();

function readChosen(): Layout | null {
  if (chosen === undefined) {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      chosen = isLayout(stored) ? stored : null;
    } catch {
      chosen = null; // storage can be blocked; the screen width decides
    }
  }
  return chosen;
}

function snapshot(): Layout {
  return readChosen() ?? (window.matchMedia(DESKTOP_QUERY).matches ? "desktop" : "mobile");
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const media = window.matchMedia(DESKTOP_QUERY);
  media.addEventListener("change", listener);
  return () => {
    listeners.delete(listener);
    media.removeEventListener("change", listener);
  };
}

function setChosen(layout: Layout) {
  chosen = layout;
  try {
    localStorage.setItem(STORAGE_KEY, layout);
  } catch {
    // Storage can be blocked; the layout still applies until the page is reloaded.
  }
  for (const listener of listeners) listener();
}

/** The layout in force. The server renders the mobile layout, which the browser then corrects. */
export function useLayout(): Layout {
  return useSyncExternalStore(subscribe, snapshot, () => "mobile");
}

/** Shows the layout now and remembers it in this browser. */
export function chooseLayout(layout: Layout) {
  setChosen(layout);
}
