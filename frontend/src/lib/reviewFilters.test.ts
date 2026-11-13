import { describe, expect, it } from "vitest";

import { searchTerms } from "@/components/SearchBox";
import { formatChange, formatShare } from "@/lib/format";
import { readRatingFilter } from "@/lib/ratingFilter";
import { appendThemeChoices, readMatch, readSort, readThemeChoices } from "@/lib/reviewFilters";

describe("theme choices in a URL", () => {
  it("writes a theme with its sentiments, and reads it back", () => {
    const choices = new Map([
      [12, ["negative" as const, "mixed" as const]],
      [30, []],
    ]);
    const params = appendThemeChoices(new URLSearchParams(), choices, "all");
    expect(params.getAll("theme")).toEqual(["12:negative,mixed", "30"]);
    expect(params.get("match")).toBe("all");
    expect(readThemeChoices(params)).toEqual(choices);
    expect(readMatch(params)).toBe("all");
  });

  it("leaves match out with one theme, since any and all are the same then", () => {
    const params = appendThemeChoices(new URLSearchParams(), new Map([[12, []]]), "all");
    expect(params.has("match")).toBe(false);
  });

  it("skips what it can't read", () => {
    const params = new URLSearchParams("theme=abc&theme=7:furious,negative&theme=-3&match=most&sort=sideways");
    expect(readThemeChoices(params)).toEqual(new Map([[7, ["negative"]]]));
    expect(readMatch(params)).toBe("any");
    expect(readSort(params)).toBe("newest");
  });
});

describe("the rating filter in a URL", () => {
  it("reads the scale and the chosen buckets, or nothing when either is missing or broken", () => {
    const scale = JSON.stringify({ type: "numeric", min: 1, max: 5 });
    expect(readRatingFilter(new URLSearchParams({ rating_scale: scale, rating: "1" }))).toEqual({
      scale: { type: "numeric", min: 1, max: 5 },
      labels: ["1"],
    });
    expect(readRatingFilter(new URLSearchParams({ rating: "1" }))).toBeNull();
    expect(readRatingFilter(new URLSearchParams({ rating_scale: "{not json", rating: "1" }))).toBeNull();
  });
});

describe("search terms for highlighting", () => {
  it("drops quotes, or and excluded words, and shortens long words to a stem-like prefix", () => {
    // Words over five letters lose up to three ("birthday" to "birth", "dinner" to "dinn").
    expect(searchTerms('"birthday dinner" or valet -parking')).toEqual(["birth", "dinn", "valet"]);
    expect(searchTerms("waiting")).toEqual(["wait"]);
    expect(searchTerms(null)).toEqual([]);
  });
});

describe("shares and changes in words", () => {
  it("rounds to whole percents and points, keeping tiny values visible", () => {
    expect(formatShare(0.2825)).toBe("28%");
    expect(formatShare(0.003)).toBe("<1%");
    expect(formatChange(-0.1006)).toBe("−10 pts");
    expect(formatChange(0.01)).toBe("+1 pt");
    expect(formatChange(0.002)).toBe("+<1 pt");
    expect(formatChange(0)).toBe("0 pts");
  });
});
