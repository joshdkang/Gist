import { defineConfig } from "@playwright/test";

/**
 * End-to-end tests against a running app (`npm run dev` here and the API from src/), signed in as a
 * test account: `npm run test:e2e`. They need, in the environment:
 *
 * - E2E_EMAIL and E2E_PASSWORD: an account to sign in as (src/create_user.py makes one). Without them
 *   every test is skipped.
 * - E2E_BASE_URL (optional): the frontend's address, by default http://localhost:3000.
 * - E2E_CHANNEL (optional): an installed browser to use (msedge, chrome). By default Edge on Windows,
 *   which is always installed there, and Playwright's own Chromium elsewhere (`npx playwright install chromium`).
 *
 * The tests read the public sample entity named in E2E_ENTITY (default Cochon), which must be analyzed.
 * They check behaviour (a filter narrows the counts, a link carries its filters) rather than exact
 * counts, so they hold as the data changes.
 */
const channel = process.env.E2E_CHANNEL ?? (process.platform === "win32" ? "msedge" : undefined);

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "list" : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    channel,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "desktop",
      testIgnore: /mobile\.spec\.ts/,
      dependencies: ["setup"],
      use: { viewport: { width: 1600, height: 1000 }, storageState: "e2e/.auth/user.json" },
    },
    {
      name: "mobile",
      testMatch: /mobile\.spec\.ts/,
      dependencies: ["setup"],
      use: { viewport: { width: 390, height: 844 }, storageState: "e2e/.auth/user.json" },
    },
  ],
});
