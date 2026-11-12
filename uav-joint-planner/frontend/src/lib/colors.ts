/**
 * Validated categorical palette (colour-blind safe in this order). A hue follows its
 * entity, never its rank. Series past the eighth fold to a neutral and rely on text labels.
 */
export type Theme = "light" | "dark";

const SERIES: Record<Theme, string[]> = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
};
const OVERFLOW = "#898781";

export const ALGORITHM_ORDER = [
  "nearest",
  "greedy_energy",
  "independent",
  "integrated",
  "ortools_vrp",
  "genetic",
  "exact_cpsat",
];

export function seriesColor(index: number, theme: Theme): string {
  return SERIES[theme][index] ?? OVERFLOW;
}

export function algorithmColor(name: string, theme: Theme): string {
  return seriesColor(ALGORITHM_ORDER.indexOf(name), theme);
}

export interface MapColors {
  surface: string;
  grid: string;
  obstacle: string;
  noFly: string;
  zone: string;
  ink: string;
  inkSoft: string;
  highlight: string;
}

export const MAP_COLORS: Record<Theme, MapColors> = {
  light: {
    surface: "#fcfcfb",
    grid: "#ecebe5",
    obstacle: "#b3b1a8",
    noFly: "#d9b8b5",
    zone: "#f0ecdc",
    ink: "#0b0b0b",
    inkSoft: "#52514e",
    highlight: "#d03b3b",
  },
  dark: {
    surface: "#1a1a19",
    grid: "#2c2c2a",
    obstacle: "#5c5b55",
    noFly: "#6b4542",
    zone: "#33322a",
    ink: "#f0efec",
    inkSoft: "#c3c2b7",
    highlight: "#ec835a",
  },
};

export const BATTERY_PARTS = {
  travel: 0,
  task: 1,
  return: 2,
} as const;
