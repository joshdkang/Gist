import { describe, expect, it } from "vitest";

import {
  appendPeriodsFilter,
  describeFilters,
  describePeriodsFilter,
  filterQuery,
  readPeriodsFilter,
  readSearch,
} from "@/lib/periodFilter";
import type { RatingFilter } from "@/lib/types";

const start = (year: number, month = 1) => `${year}-${String(month).padStart(2, "0")}-01T00:00:00Z`;

describe("describePeriodsFilter", () => {
  it("names one period, and a run of them as a range", () => {
    expect(describePeriodsFilter({ interval: "year", starts: [start(2019)] })).toBe("from 2019");
    expect(describePeriodsFilter({ interval: "year", starts: [start(2018), start(2019)] })).toBe("from 2018–2019");
    expect(describePeriodsFilter({ interval: "quarter", starts: [start(2019, 4)] })).toBe("from 2019 Q2");
  });

  it("says the year once when a run stays in it", () => {
    expect(describePeriodsFilter({ interval: "quarter", starts: [start(2020, 1), start(2020, 4), start(2020, 7)] })).toBe(
      "from 2020 Q1–Q3",
    );
    expect(describePeriodsFilter({ interval: "month", starts: [start(2019, 1), start(2019, 2), start(2019, 3)] })).toBe(
      "from Jan–Mar 2019",
    );
    expect(describePeriodsFilter({ interval: "month", starts: [start(2019, 11), start(2019, 12), start(2020, 1)] })).toBe(
      "from Nov 2019–Jan 2020",
    );
  });

  it("names whole years by their year", () => {
    const year2020 = [1, 4, 7, 10].map((month) => start(2020, month));
    expect(describePeriodsFilter({ interval: "quarter", starts: [...year2020, start(2021, 4)] })).toBe("from 2020 and 2021 Q2");
    const months = Array.from({ length: 24 }, (_, index) => start(2018 + Math.floor(index / 12), (index % 12) + 1));
    expect(describePeriodsFilter({ interval: "month", starts: months })).toBe("from 2018–2019");
  });

  it("lists separate runs, and counts them when there are many", () => {
    const starts = [start(2012), start(2013), start(2019), start(2020)];
    expect(describePeriodsFilter({ interval: "year", starts })).toBe("from 2012–2013 and 2019–2020");
    const scattered = [2010, 2012, 2014, 2016, 2018].map((year) => start(year));
    expect(describePeriodsFilter({ interval: "year", starts: scattered })).toBe("from 5 ranges, 2010 to 2018");
  });

  it("doesn't depend on the order or spelling of the period starts", () => {
    const starts = ["2019-01-01T00:00:00.000Z", start(2015), start(2017)];
    expect(describePeriodsFilter({ interval: "year", starts })).toBe("from 2015, 2017 and 2019");
    // Consecutive periods join into a run whatever their spelling.
    expect(describePeriodsFilter({ interval: "year", starts: ["2019-01-01T00:00:00.000Z", start(2018)] })).toBe(
      "from 2018–2019",
    );
  });
});

describe("filters in a URL", () => {
  const rating: RatingFilter = { scale: { type: "numeric", min: 1, max: 5 }, labels: ["1", "2"] };

  it("round-trips the period filter, the rating filter and the search", () => {
    const periods = { interval: "quarter" as const, starts: [start(2019, 4), start(2019, 7)] };
    const query = filterQuery(rating, periods, "valet");
    const params = new URLSearchParams(query.slice(1));
    expect(readPeriodsFilter(params)).toEqual(periods);
    expect(readSearch(params)).toBe("valet");
    expect(params.getAll("rating")).toEqual(["1", "2"]);
  });

  it("is empty with no filter, and ignores an unknown interval or a blank search", () => {
    expect(filterQuery(null, null, null)).toBe("");
    expect(readPeriodsFilter(new URLSearchParams("interval=week&period=2019-01-01"))).toBeNull();
    expect(readPeriodsFilter(new URLSearchParams("interval=year"))).toBeNull();
    expect(readSearch(new URLSearchParams("q=%20%20"))).toBeNull();
  });

  it("leaves an empty period choice out of the URL", () => {
    expect(appendPeriodsFilter(new URLSearchParams(), { interval: "year", starts: [] }).size).toBe(0);
  });

  it("describes every filter in force in one phrase", () => {
    expect(describeFilters(rating, { interval: "year", starts: [start(2019)] }, "valet")).toBe(
      "from 2019 rated 1 or 2 mentioning “valet”",
    );
    expect(describeFilters(null, null, null)).toBe("");
  });
});
