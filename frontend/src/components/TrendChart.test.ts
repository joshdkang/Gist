import { describe, expect, it } from "vitest";

import { periodTicks } from "@/components/TrendChart";

describe("labels under a chart's bars", () => {
  it("names quarters, with the year under each year's first one", () => {
    const starts = ["2019-10-01T00:00:00Z", "2020-01-01T00:00:00Z", "2020-04-01T00:00:00Z"];
    expect(periodTicks(starts, "quarter")).toEqual([
      { tick: "Q4", year: "2019" },
      { tick: "Q1", year: "2020" },
      { tick: "Q2", year: "" },
    ]);
  });

  it("names the months that start a quarter, and every year at the year interval", () => {
    const months = ["2020-02-01T00:00:00Z", "2020-03-01T00:00:00Z", "2020-04-01T00:00:00Z"];
    expect(periodTicks(months, "month")).toEqual([
      { tick: "", year: "2020" },
      { tick: "", year: "" },
      { tick: "Apr", year: "" },
    ]);
    expect(periodTicks(["2019-01-01T00:00:00Z", "2020-01-01T00:00:00Z"], "year")).toEqual([
      { tick: "2019", year: "" },
      { tick: "2020", year: "" },
    ]);
  });
});

describe("month labels when the bars are wide enough", () => {
  it("names every month", () => {
    const months = ["2020-02-01T00:00:00Z", "2020-03-01T00:00:00Z", "2020-04-01T00:00:00Z"];
    expect(periodTicks(months, "month", true).map((tick) => tick.tick)).toEqual(["Feb", "Mar", "Apr"]);
  });
});
