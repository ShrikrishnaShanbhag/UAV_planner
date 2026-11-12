import { NavLink, Outlet } from "react-router-dom";
import { Button, Toasts } from "@/components/ui";
import { useMission } from "@/store/mission";
import { useUi } from "@/store/ui";

const NAV = [
  { to: "/", label: "Dashboard" },
  { to: "/builder", label: "Scenario Builder" },
  { to: "/planner", label: "Planner" },
  { to: "/simulation", label: "Simulation" },
  { to: "/compare", label: "Compare" },
  { to: "/experiments", label: "Experiments" },
  { to: "/model", label: "Model" },
];

export default function App() {
  const theme = useUi((s) => s.theme);
  const toggleTheme = useUi((s) => s.toggleTheme);
  const scenarioName = useMission((s) => s.scenario.name);
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2">
          <span className="font-semibold" title="Optimizing Routing, Task Allocation, and Energy Management in Collaborative UAV Systems">UAV Energy Planner</span>
          <nav className="flex flex-1 flex-wrap gap-1" aria-label="Main">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  `rounded-md px-2.5 py-1 text-sm ${
                    isActive
                      ? "bg-stone-200 font-medium dark:bg-stone-700"
                      : "text-stone-600 hover:bg-stone-100 dark:text-stone-300 dark:hover:bg-stone-800"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <span className="truncate text-xs text-stone-500 dark:text-stone-400" data-testid="current-scenario">
            {scenarioName}
          </span>
          <Button variant="ghost" onClick={toggleTheme} title="Switch between light and dark mode">
            {theme === "light" ? "Dark" : "Light"} mode
          </Button>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-4">
        <Outlet />
      </main>
      <footer className="border-t border-stone-200 px-4 py-2 text-center text-xs text-stone-500 dark:border-stone-800 dark:text-stone-400">
        All values are simulated and illustrative, in normalized energy units (EU) unless stated. They are not measured drone performance.
      </footer>
      <Toasts />
    </div>
  );
}
