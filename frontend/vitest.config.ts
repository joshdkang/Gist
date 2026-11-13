import path from "node:path";

import { defineConfig } from "vitest/config";

// Unit tests of the frontend's plain logic (filters, URLs, wording): `npm test`. The end-to-end tests
// in e2e/ run separately with Playwright (`npm run test:e2e`).
export default defineConfig({
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  test: { include: ["src/**/*.test.ts"], environment: "node" },
});
