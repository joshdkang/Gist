import fs from "node:fs";

import { expect, test as setup } from "@playwright/test";

import { signedInFile } from "./helpers";

/** Signs in once and saves the session, which every other test starts from. */
setup("sign in", async ({ page }) => {
  const email = process.env.E2E_EMAIL;
  const password = process.env.E2E_PASSWORD;
  if (!email || !password) {
    // An empty session, so the tests can start and skip themselves.
    fs.mkdirSync("e2e/.auth", { recursive: true });
    fs.writeFileSync(signedInFile, JSON.stringify({ cookies: [], origins: [] }));
    setup.skip(true, "Set E2E_EMAIL and E2E_PASSWORD to run the end-to-end tests.");
    return;
  }
  await page.goto("/login");
  await page.fill("#email", email);
  await page.fill("#password", password);
  await page.click("button[type=submit]");
  await page.waitForURL((url) => url.pathname === "/");
  await expect(page.getByRole("heading", { name: "Entities" })).toBeVisible();
  await page.context().storageState({ path: signedInFile });
});
