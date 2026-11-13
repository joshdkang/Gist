import { expect, test } from "@playwright/test";

import { needsAccount, numberIn, sampleEntityPath, useLayout } from "./helpers";

needsAccount();

test.beforeEach(async ({ page }) => {
  await useLayout(page, "desktop");
});

test("a theme's page filters its reviews by sentiment and period, and opens them in All reviews", async ({ page }) => {
  await sampleEntityPath(page);
  await page.locator("tbody tr").first().locator("a").click();
  const exportButton = page.getByRole("button", { name: /^Export [\d,]+ reviews?$/ });
  await expect(exportButton).toBeVisible();
  const all = await numberIn(exportButton);

  // A sentiment: fewer reviews, and every one listed has it.
  await page.getByRole("group", { name: "Filter by sentiment" }).getByRole("button", { name: /^Negative/ }).click();
  await expect.poll(() => numberIn(exportButton)).toBeLessThan(all);
  const negative = await numberIn(exportButton);
  // Each review's sentiment label (the first bold span in its header) says Negative.
  const labels = () =>
    page.locator("article").evaluateAll((articles) =>
      articles.map((article) => article.querySelector("span.font-semibold")?.textContent?.trim()),
    );
  await expect.poll(async () => [...new Set(await labels())]).toEqual(["Negative"]);

  // A trend bar: fewer still, as a chip, and the link to All reviews carries all of it.
  await page.getByRole("button", { name: /^20\d\d: / }).last().click();
  await expect(page.getByRole("button", { name: /^Remove filter: From/ })).toBeVisible();
  expect(await numberIn(exportButton)).toBeLessThanOrEqual(negative);
  const link = page.getByRole("link", { name: "Open in All reviews" });
  const href = (await link.getAttribute("href")) ?? "";
  expect(decodeURIComponent(href)).toMatch(/theme=\d+:negative/);
  expect(href).toContain("period=");

  // Clear all brings every review back.
  await page.getByRole("button", { name: "Clear all" }).click();
  await expect.poll(() => numberIn(exportButton)).toBe(all);
});
