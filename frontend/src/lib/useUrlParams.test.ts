import { describe, expect, it } from "vitest";

import { sameParams } from "@/lib/useUrlParams";

describe("comparing query strings", () => {
  it("treats the same parameters written two ways as the same", () => {
    expect(sameParams("q=valet%20parking&theme=12%3Anegative", "q=valet+parking&theme=12:negative")).toBe(true);
    expect(sameParams("", "")).toBe(true);
  });

  it("tells apart different values, a missing parameter, and a different order", () => {
    expect(sameParams("period=2020&period=2021", "period=2020")).toBe(false);
    expect(sameParams("rating=1", "rating=2")).toBe(false);
    expect(sameParams("theme=1&theme=2", "theme=2&theme=1")).toBe(false);
  });
});
