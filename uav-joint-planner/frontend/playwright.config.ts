import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const runtime = path.join(os.tmpdir(), "uavplan-e2e");
const python = process.env.UAVPLAN_API_CMD ?? "uv run --no-sync --python 3.12 uvicorn uavplan.api.main:app --port 8000";

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  workers: 1,
  use: { baseURL: "http://localhost:5173", viewport: { width: 1440, height: 1000 } },
  webServer: [
    {
      command: python,
      cwd: path.resolve(here, "../backend"),
      url: "http://localhost:8000/api/health",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        UAVPLAN_DB: path.join(runtime, `e2e-${Date.now()}.db`),
        UAVPLAN_RESULTS: path.join(runtime, "results"),
      },
    },
    {
      command: "npm run dev",
      url: "http://localhost:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
