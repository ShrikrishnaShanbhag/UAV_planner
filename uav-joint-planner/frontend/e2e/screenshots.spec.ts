import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

// Produces the images in docs/screenshots. Run with SCREENSHOTS=1 npx playwright test screenshots
const folder = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../docs/screenshots");
test.skip(!process.env.SCREENSHOTS, "only runs when regenerating the documentation images");

test("capture every page", async ({ page }) => {
  test.setTimeout(120_000);
  const shot = (name: string) => page.screenshot({ path: path.join(folder, `${name}.png`), fullPage: true });

  await page.goto("/");
  await expect(page.getByTestId("load-demo")).toBeVisible();
  await shot("dashboard");

  await page.getByTestId("load-demo").click();
  await expect(page).toHaveURL(/planner/);
  await page.getByTestId("run-plan").click();
  await expect(page.getByTestId("route-list")).toContainText("→");
  await page.waitForTimeout(500);
  await shot("planner");

  await page.getByRole("link", { name: "Scenario Builder" }).click();
  await page.waitForTimeout(800);
  await shot("builder");

  await page.getByRole("link", { name: "Compare" }).click();
  await page.getByTestId("run-compare").click();
  await expect(page.getByTestId("compare-table")).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(800);
  await shot("compare");

  await page.getByRole("link", { name: "Simulation" }).click();
  await page.getByTestId("start-sim").click();
  await expect(page.getByTestId("play")).toBeEnabled({ timeout: 20_000 });
  await page.getByTestId("play").click();
  await expect(page.getByTestId("sim-time")).not.toHaveText(/^0 \//);
  await page.getByTestId("fail-uav").click();
  await expect(page.getByTestId("replan-diff")).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(1200);
  await shot("simulation");

  await page.getByRole("link", { name: "Model" }).click();
  await page.waitForTimeout(500);
  await shot("model");

  await page.getByRole("link", { name: "Experiments" }).click();
  await page.getByRole("checkbox", { name: "B", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "C", exact: true }).uncheck();
  await page.getByRole("button", { name: "Launch" }).click();
  await expect(page.getByTestId("summary-table")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId("box-plot")).toBeVisible();
  await shot("experiments");
  await page.getByRole("tab", { name: "Significance" }).click();
  await expect(page.getByTestId("stats-table")).toBeVisible();
  await page.getByRole("tab", { name: "Pareto" }).click();
  await page.getByRole("tab", { name: "Figures" }).click();
  await expect(page.getByRole("img", { name: "gantt demo" })).toBeVisible();
  await page.getByRole("tab", { name: "Report" }).click();
  await expect(page.getByText("Where the integrated method does not help").first()).toBeVisible();

  await page.getByRole("button", { name: "Dark mode" }).click();
  await page.getByRole("link", { name: "Planner" }).click();
  await page.waitForTimeout(800);
  await shot("planner-dark");
});
