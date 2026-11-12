import { expect, test } from "@playwright/test";

test("load demo, plan with the proposed method, see routes and battery bars", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems",
  );
  await page.getByTestId("load-demo").click();
  await expect(page).toHaveURL(/planner/);
  await expect(page.getByTestId("current-scenario")).toHaveText("Earthquake response demo");
  await expect(page.getByTestId("algorithm-select")).toHaveCount(0);
  await expect(page.getByText("Explain this assignment")).toHaveCount(0);
  await expect(page.getByLabel("Distance w_d")).toHaveCount(0);
  await expect(page.getByLabel("Energy w_e")).toBeVisible();

  await page.getByTestId("run-plan").click();

  await expect(page.getByTestId("route-list")).toContainText("→");
  await expect(page.getByTestId("battery-bars")).toBeVisible();
  await expect(page.getByTestId("battery-Scout-1")).toBeVisible();
  await expect(page.getByTestId("feasibility-ok")).toBeVisible();
  await expect(page.getByTestId("energy-breakdown")).toBeVisible();
  await page.getByRole("tab", { name: "Shortest-distance paths" }).click();
  await expect(page.getByTestId("path-summary")).toContainText("EU");
  await expect(page.getByTestId("map-canvas").locator("canvas").first()).toBeVisible();
});

test("builder tools place a task, draw an obstacle and report a bad placement", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Scenario Builder" }).click();
  const canvas = page.getByTestId("map-canvas").locator("canvas").first();
  await expect(canvas).toBeVisible();
  const box = (await canvas.boundingBox())!;
  const at = (fx: number, fy: number) => [box.x + box.width * fx, box.y + box.height * fy] as const;

  await expect(page.getByTestId("validation-issues")).toContainText("at least one station");
  await page.getByRole("button", { name: "Station", exact: true }).click();
  await page.mouse.click(...at(0.1, 0.9));
  await page.getByRole("button", { name: "UAV", exact: true }).click();
  await page.mouse.click(...at(0.1, 0.9));
  await page.getByRole("button", { name: "Task", exact: true }).click();
  await page.mouse.click(...at(0.8, 0.2));
  await expect(page.getByText("Tasks (1)")).toBeVisible();
  await expect(page.getByTestId("validation-issues")).toHaveCount(0);

  await page.getByRole("button", { name: "Obstacle", exact: true }).click();
  await page.mouse.move(...at(0.4, 0.4));
  await page.mouse.down();
  await page.mouse.move(...at(0.6, 0.6), { steps: 4 });
  await page.mouse.up();

  await page.getByRole("button", { name: "Task", exact: true }).click();
  await page.mouse.click(...at(0.5, 0.5));
  await expect(page.getByRole("alert")).toContainText("inside an obstacle");
  await expect(page.getByText("Tasks (1)")).toBeVisible();
});

test("start a simulation, inject a UAV failure, see the replan in the event log", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("load-demo").click();
  await expect(page).toHaveURL(/planner/);
  await page.getByRole("link", { name: "Simulation" }).click();

  await page.getByTestId("start-sim").click();
  const play = page.getByTestId("play");
  await expect(play).toBeEnabled({ timeout: 20_000 });
  await play.click();
  await expect(page.getByTestId("sim-time")).not.toHaveText(/^0 \//, { timeout: 15_000 });

  await page.getByTestId("inject-uav").selectOption("Scout-1");
  await page.getByTestId("fail-uav").click();

  await expect(page.getByTestId("event-log")).toContainText("REPLAN", { timeout: 15_000 });
  await expect(page.getByTestId("event-log")).toContainText("UAV UNAVAILABLE");
  await expect(page.getByTestId("replan-diff")).toContainText("UAV UNAVAILABLE");
  await expect(page.getByTestId("energy-table")).toContainText("Scout-1");
});
