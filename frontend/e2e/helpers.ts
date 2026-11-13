import { expect, type Locator, type Page, test } from "@playwright/test";

export const signedInFile = "e2e/.auth/user.json";

/** Skips the calling tests when no test account is configured. */
export function needsAccount() {
  test.skip(!process.env.E2E_EMAIL || !process.env.E2E_PASSWORD, "Set E2E_EMAIL and E2E_PASSWORD.");
}

/** The sample entity the tests read, found by name in the Samples tab, so no id is hard-coded. */
export async function sampleEntityPath(page: Page): Promise<string> {
  const name = process.env.E2E_ENTITY ?? "Cochon";
  await page.goto("/");
  await page.getByRole("tab", { name: "Samples" }).click();
  // A table row's link is the name; a mobile card's link is the whole card, so match the start.
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  await page.getByRole("link", { name: new RegExp(`^${escaped}(\\s|$)`) }).first().click();
  await page.waitForURL(/\/entities\/\d+$/);
  return new URL(page.url()).pathname;
}

/** Chooses a layout for this browser (the account menu's setting), before the page loads. */
export async function useLayout(page: Page, layout: "desktop" | "mobile") {
  await page.addInitScript((value) => localStorage.setItem("gist-layout", value), layout);
}

/** A number shown as text ("4,480 reviews", "Export 506 reviews"). */
export async function numberIn(locator: Locator): Promise<number> {
  const text = await locator.innerText();
  const match = /([\d,]+)/.exec(text);
  expect(match, `a number in "${text}"`).not.toBeNull();
  return Number(match![1].replace(/,/g, ""));
}
