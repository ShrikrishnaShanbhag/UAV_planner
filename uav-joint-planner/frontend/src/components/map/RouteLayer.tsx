import { Line } from "react-konva";
import type { Route, Scenario } from "@/api/client";
import { seriesColor } from "@/lib/colors";
import { toPixels } from "./layers";
import type { LayerProps } from "./types";

export function RouteLayer({ routes, scenario, faded, highlightUav, ...props }: LayerProps & {
  routes: Route[];
  scenario: Scenario;
  faded?: boolean;
  highlightUav?: string | null;
}) {
  const { grid, cell, theme } = props;
  const uavIndex = new Map(scenario.uavs.map((u, i) => [u.id, i]));
  return (
    <>
      {routes.flatMap((route) =>
        route.legs.map((leg, index) => (
          <Line
            key={`${route.uav_id}-${index}`}
            points={leg.path.flatMap(([x, y]) => toPixels(grid, cell, x, y))}
            stroke={seriesColor(uavIndex.get(route.uav_id) ?? 99, theme)}
            strokeWidth={route.uav_id === highlightUav ? 3.5 : faded ? 1.5 : 2.5}
            opacity={faded ? 0.3 : leg.kind === "return" ? 0.5 : 1}
            lineJoin="round"
            lineCap="round"
            listening={false}
          />
        )),
      )}
    </>
  );
}
