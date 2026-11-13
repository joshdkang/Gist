import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

/**
 * Whether two query strings hold the same parameters, compared decoded and in order, so the same
 * filter written two ways ("%20" or "+", "%3A" or ":") counts as one. Order is kept: it is how the
 * page wrote them, and a reordering would change which theme a page lists first.
 */
export function sameParams(a: string, b: string): boolean {
  const left = [...new URLSearchParams(a)];
  const right = [...new URLSearchParams(b)];
  return left.length === right.length && left.every(([key, value], i) => right[i][0] === key && right[i][1] === value);
}

/**
 * The page's query parameters, for filters that live in the URL, and a function that replaces them.
 *
 * Reading `useSearchParams` directly races: `router.replace` updates the URL in a transition, so for a
 * moment after a click the parameters are still the old ones, and a second quick click (ticking two
 * filters) builds on them and loses the first. Here the parameters are React state that changes at
 * once, and the URL follows. A change that comes from the URL itself (the back button, a link) is
 * taken over, but while a replace is on its way, the URLs before it are ignored, so an earlier
 * replace landing late doesn't undo a later one.
 */
export function useUrlParams() {
  const router = useRouter();
  const pathname = usePathname();
  const current = useSearchParams().toString();
  const [query, setQuery] = useState(current);
  // The URL as last seen, and the query of a replace that hasn't reached the URL yet.
  const [seen, setSeen] = useState(current);
  const [pending, setPending] = useState<string | null>(null);
  if (current !== seen) {
    // Adjusting state while rendering, as React recommends over an effect: the URL changed.
    setSeen(current);
    if (pending === null || sameParams(pending, current)) {
      setPending(null);
      setQuery(current);
    }
  }

  const params = useMemo(() => new URLSearchParams(query), [query]);

  /** Replaces the parameters: `next` is "" or "?a=1&b=2", as `filterQuery` writes it, or the params. */
  const replace = useCallback(
    (next: string | URLSearchParams) => {
      const nextQuery = (typeof next === "string" ? next : next.toString()).replace(/^\?/, "");
      setQuery(nextQuery);
      // A replace to the URL already there changes nothing, so there is nothing to wait for.
      setPending(sameParams(nextQuery, seen) ? null : nextQuery);
      router.replace(`${pathname}${nextQuery ? `?${nextQuery}` : ""}`, { scroll: false });
    },
    [router, pathname, seen],
  );

  return [params, replace] as const;
}
