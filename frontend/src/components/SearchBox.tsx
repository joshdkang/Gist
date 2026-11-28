"use client";

import { useState } from "react";

import type { Search, SearchMode } from "@/lib/periodFilter";

/**
 * A box that searches review text. The search runs when the user presses Enter (or the search
 * button), not on every keystroke, since each search recounts every theme. The × clears it.
 * `value` is the search in force (from the page's URL); the box keeps its own draft while typing.
 * The mode chooses words (keyword search) or meaning (the words, or reviews that say it otherwise);
 * changing it reruns a search in force.
 */
export function SearchBox({
  value,
  onSearch,
  placeholder = "Search reviews",
  className = "",
}: {
  value: Search | null;
  onSearch: (search: Search | null) => void;
  placeholder?: string;
  className?: string;
}) {
  const [draft, setDraft] = useState(value?.text ?? "");
  const [mode, setMode] = useState<SearchMode>(value?.mode ?? "words");
  // A search changed elsewhere (a chip removed, Clear all, a link) replaces the draft and the mode.
  // Compared as a string: the page may hand over a new object for the same search.
  const valueKey = value ? `${value.mode}:${value.text}` : "";
  const [shown, setShown] = useState(valueKey);
  if (valueKey !== shown) {
    setShown(valueKey);
    setDraft(value?.text ?? "");
    if (value) setMode(value.mode);
  }
  const submit = (text: string, searchMode: SearchMode) =>
    onSearch(text.trim() ? { text: text.trim(), mode: searchMode } : null);

  return (
    <form
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        submit(draft, mode);
      }}
      className={`flex h-9 items-center rounded-lg border border-line-strong bg-surface focus-within:border-link ${className}`}
    >
      <button type="submit" aria-label="Search" className="flex h-full items-center pr-1.5 pl-2.5 text-muted hover:text-fg">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <circle cx="7" cy="7" r="4.75" stroke="currentColor" strokeWidth="1.5" />
          <path d="m10.5 10.5 3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
      <input
        type="search"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        maxLength={200}
        className="h-full min-w-0 flex-1 bg-transparent pr-2 text-sm outline-none placeholder:text-muted [&::-webkit-search-cancel-button]:hidden"
      />
      {draft && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => {
            setDraft("");
            if (value) onSearch(null);
          }}
          className="flex h-full items-center px-2 text-muted hover:text-fg"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>
      )}
      <select
        value={mode}
        onChange={(event) => {
          const next = event.target.value as SearchMode;
          setMode(next);
          if (value) submit(value.text, next);
        }}
        aria-label="Match the search by"
        title="Words: reviews with these words. Meaning: also reviews that say it in other words."
        className="h-full shrink-0 rounded-r-lg border-l border-line-strong bg-transparent pr-1 pl-2 text-xs text-muted outline-none hover:text-fg"
      >
        <option value="words">Words</option>
        <option value="meaning">Meaning</option>
      </select>
    </form>
  );
}

/**
 * The words of a search, for highlighting matches in a review (in either mode: a search by meaning
 * matches the words too, and a review found by meaning alone just has nothing marked): quotes, `or` and excluded (`-word`)
 * terms are dropped. Postgres matched on word stems ("waited" for "waiting"), which plain text can't
 * reproduce, so each word is shortened to a stem-like prefix and any word starting with it is marked.
 */
export function searchTerms(search: Search | null): string[] {
  if (!search) return [];
  return search.text
    .replace(/"/g, " ")
    .split(/\s+/)
    .filter((word) => word && word.toLowerCase() !== "or" && !word.startsWith("-"))
    .map((word) => word.toLowerCase().replace(/[^\p{L}\p{N}']/gu, ""))
    .filter((word) => word.length >= 2)
    .map((word) => (word.length > 5 ? word.slice(0, Math.max(4, word.length - 3)) : word));
}

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** `text` with the words that start with a search term marked. */
export function Highlighted({ text, terms }: { text: string; terms: string[] }) {
  if (terms.length === 0) return <>{text}</>;
  const pattern = new RegExp(`(\\b(?:${terms.map(escape).join("|")})[\\p{L}\\p{N}']*)`, "giu");
  const parts = text.split(pattern);
  return (
    <>
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <mark key={index} className="rounded-sm bg-warn-line px-0.5 text-fg">
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  );
}
