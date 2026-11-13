import { expect, type Page, test } from "@playwright/test";

import { needsAccount, numberIn, sampleEntityPath, useLayout } from "./helpers";

needsAccount();

test.beforeEach(async ({ page }) => {
  await useLayout(page, "desktop");
});

const total = (page: Page) => page.getByRole("heading", { name: /^[\d,]+ reviews?/ });

test("ticking a theme lists its reviews, as many as its count says", async ({ page }) => {
  const path = await sampleEntityPath(page);
  await page.goto(`${path}/reviews`);
  await expect(total(page)).toBeVisible();
  const all = await numberIn(total(page));

  // The period list comes first; theme rows are the ones with a sentiment group (div.group).
  const themeRow = page.locator("aside").locator("div.group label").first();
  const count = await numberIn(themeRow.locator("span.tabular-nums"));
  await themeRow.locator("input").click();
  await expect.poll(() => numberIn(total(page))).toBe(count);
  expect(count).toBeLessThan(all);

  // Its sentiment choices appear; choosing one narrows the list to it.
  const sentiments = page.locator("aside").getByRole("group", { name: /^Sentiment of / });
  const negative = sentiments.getByRole("button", { name: /^Negative/ });
  const negativeCount = await numberIn(negative.locator("span.text-muted"));
  await negative.click();
  await expect.poll(() => numberIn(total(page))).toBe(negativeCount);
  await expect(page.getByRole("button", { name: /^Remove filter: .+: Negative$/ })).toBeVisible();
});

test("a review's theme tag adds that theme, and all narrows to reviews with every one", async ({ page }) => {
  const path = await sampleEntityPath(page);
  await page.goto(`${path}/reviews`);
  await expect(page.locator("article").first()).toBeVisible();
  const article = page.locator("article").filter({ has: page.locator("button[aria-label^='Filter by ']").nth(1) }).first();
  await expect(total(page)).toBeVisible();
  const all = await numberIn(total(page));
  const tags = article.locator("button[aria-label^='Filter by ']");
  await tags.nth(0).click();
  await expect.poll(() => numberIn(total(page))).toBeLessThan(all);
  const one = await numberIn(total(page));

  // The same review still lists, so add its second theme from it.
  await page.locator("article").first().locator("button[aria-label^='Filter by ']").first().click();
  await expect(total(page)).toContainText("(any of the ticked themes)");
  await expect.poll(() => numberIn(total(page))).toBeGreaterThan(one);
  const any = await numberIn(total(page));
  expect(any).toBeGreaterThanOrEqual(one);

  await page.getByRole("group", { name: "Match" }).getByRole("button", { name: "All" }).click();
  await expect(total(page)).toContainText("(all of the ticked themes)");
  await expect.poll(() => numberIn(total(page))).toBeLessThan(any);
  expect(await numberIn(total(page))).toBeLessThanOrEqual(one);
  // Every review listed carries both chosen themes.
  for (const review of await page.locator("article").all()) {
    expect(await review.locator("button[aria-pressed=true]").count()).toBe(2);
  }
});

test("periods tick by year, quarter or month, and sorting by rating puts the lowest first", async ({ page }) => {
  const path = await sampleEntityPath(page);
  await page.goto(`${path}/reviews`);
  await expect(total(page)).toBeVisible();
  const all = await numberIn(total(page));
  const aside = page.locator("aside");

  // Two separate years.
  const years = aside.locator("label").filter({ hasText: /^20\d\d/ });
  // Ticked straight after each other: the second tick must build on the first, not on the old URL.
  await years.nth(0).locator("input").click();
  await years.nth(2).locator("input").click();
  await expect(page.getByRole("button", { name: /^Remove filter: From .+ and / })).toBeVisible();
  await expect.poll(() => new URL(page.url()).searchParams.getAll("period").length).toBe(2);
  await expect.poll(() => numberIn(total(page))).toBeLessThan(all);

  // Switching to quarters clears the choice and shows each year's quarters as a row of four.
  await aside.getByRole("group", { name: "Interval" }).getByRole("button", { name: "Quarter" }).click();
  await expect.poll(() => numberIn(total(page))).toBe(all);
  const quarters = aside.getByRole("group", { name: /^Quarters of / }).first();
  await expect(quarters.getByRole("button")).toHaveCount(4);

  await page.getByRole("combobox").last().selectOption("lowest");
  // "Rating 1" and not "Rating 10" (the date follows with no space in the element's text).
  await expect(page.locator("article").first()).toContainText(/Rating 1(?!\d)/);
});

test("the reviews export downloads with the page's filters in its name", async ({ page }) => {
  const path = await sampleEntityPath(page);
  await page.goto(`${path}/reviews`);
  await page.locator("aside").locator("div.group label").first().locator("input").click();
  await expect(page.getByRole("button", { name: /^Remove filter:/ })).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: /^Export/ }).click()]);
  expect(download.suggestedFilename()).toMatch(/-reviews-.+\.csv$/);
});
