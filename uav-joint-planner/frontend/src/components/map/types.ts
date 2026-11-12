import type { Scenario } from "@/api/client";
import type { MapColors, Theme } from "@/lib/colors";

export type GridSpecLike = Scenario["grid"];

export interface LayerProps {
  grid: GridSpecLike;
  cell: number;
  colors: MapColors;
  theme: Theme;
}
