import { expect, test } from "@playwright/test";

import { needsAccount, sampleEntityPath, useLayout } from "./helpers";

needsAccount();

test.beforeEach(async ({ page }) => {
  await useLayout(page, "mobile");
});

/** True when the page is wider than the window, so a phone would scroll sideways. */
const scrollsSideways = (page: import("@playwright/test").Page) =>
  page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);

test("every page fits a phone's width", async ({ page }) => {
  const path = await sampleEntityPath(page);
  for (const url of [path, `${path}/changes`, `${path}/reviews`, `${path}/summary`, "/entities/new"]) {
    await page.goto(url);
    await expect(page.locator("main h1, main h2").first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(await scrollsSideways(page), `${url} scrolls sideways`).toBe(false);
  }
});

test("the mobile layout keeps theme cards, and All reviews opens its filters from a button", async ({ page }) => {
  const path = await sampleEntityPath(page);
  await expect(page.getByRole("button", { name: "Most reviews" })).toBeVisible();
  await expect(page.locator("table")).toHaveCount(0);

  await page.goto(`${path}/reviews`);
  const filters = page.getByRole("button", { name: /^Filters/ });
  await expect(filters).toBeVisible();
  await expect(page.getByRole("group", { name: "Match" })).toHaveCount(0);
  await filters.click();
  await expect(page.getByRole("group", { name: "Match" })).toBeVisible();
});
