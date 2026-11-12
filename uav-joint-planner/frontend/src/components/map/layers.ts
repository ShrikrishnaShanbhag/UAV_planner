import type { GridSpecLike } from "./types";

/** Convert grid coordinates to canvas pixels. Grid y points up; canvas y points down. */
export function toPixels(grid: GridSpecLike, cell: number, x: number, y: number): [number, number] {
  return [(x + 0.5) * cell, (grid.height - 1 - y + 0.5) * cell];
}
