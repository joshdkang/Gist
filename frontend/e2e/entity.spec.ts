import { expect, test } from "@playwright/test";

import { needsAccount, numberIn, sampleEntityPath, useLayout } from "./helpers";

needsAccount();

test.beforeEach(async ({ page }) => {
  await useLayout(page, "desktop");
});

test("the entity page lists its themes in a sortable table", async ({ page }) => {
  await sampleEntityPath(page);
  const rows = page.locator("tbody tr");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThan(3);
  // Sorting by name puts the themes in alphabetical order.
  await page.getByRole("button", { name: /^Theme/ }).click();
  const names = await rows.locator("a").allInnerTexts();
  expect(names).toEqual([...names].sort((a, b) => a.localeCompare(b)));
});

test("a rating bar filters the themes, shows a chip, and Clear all removes it", async ({ page }) => {
  const path = await sampleEntityPath(page);
  const all = await numberIn(page.getByText(/^All [\d,]+ reviews$/));
  await page.locator("button[aria-label^='1:']").click();
  const showing = page.getByText(/^Showing [\d,]+ of [\d,]+ reviews$/);
  await expect(showing).toBeVisible();
  expect(await numberIn(showing)).toBeLessThan(all);
  await expect(page.getByRole("button", { name: "Remove filter: Rated 1" })).toBeVisible();
  expect(page.url()).toContain("rating=1");

  await page.getByRole("button", { name: "Clear all" }).click();
  await expect(page.getByText(/^All [\d,]+ reviews$/)).toBeVisible();
  expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe(path);
});

test("a search narrows the themes and carries into All reviews", async ({ page }) => {
  await sampleEntityPath(page);
  const box = page.getByRole("searchbox", { name: "Search reviews" });
  await box.fill("valet");
  await box.press("Enter");
  await expect(page.getByRole("button", { name: /^Remove filter: Mentioning/ })).toBeVisible();
  const shown = await numberIn(page.getByText(/^Showing [\d,]+ of/));

  await page.getByRole("link", { name: "View reviews" }).click();
  await expect(page.getByRole("heading", { name: "All reviews" })).toBeVisible();
  expect(page.url()).toContain("q=valet");
  await expect(page.getByRole("heading", { name: new RegExp(`^${shown.toLocaleString("en-US")} reviews?`) })).toBeVisible();
  await expect(page.locator("article mark").first()).toBeVisible();
});

test("the period comparison lists significant changes, and the rest on request", async ({ page }) => {
  const path = await sampleEntityPath(page);
  await page.goto(`${path}/changes`);
  await page.getByRole("button", { name: "Year" }).click();
  const heading = page.getByRole("heading", { name: /compared with/ });
  await expect(heading).toBeVisible();
  const box = page.getByRole("checkbox", { name: /with no significant change/ });
  if (await box.count()) {
    const before = await page.locator("tbody tr").count();
    await box.check();
    expect(await page.locator("tbody tr").count()).toBeGreaterThan(before);
  }
});
