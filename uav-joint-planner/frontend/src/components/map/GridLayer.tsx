import { Line, Rect } from "react-konva";
import type { LayerProps } from "./types";

export function GridLayer({ grid, cell, colors }: LayerProps) {
  const width = grid.width * cell;
  const height = grid.height * cell;
  const lines = [];
  if (cell >= 8) {
    for (let x = 1; x < grid.width; x++) {
      lines.push(<Line key={`x${x}`} points={[x * cell, 0, x * cell, height]} stroke={colors.grid} strokeWidth={0.5} listening={false} />);
    }
    for (let y = 1; y < grid.height; y++) {
      lines.push(<Line key={`y${y}`} points={[0, y * cell, width, y * cell]} stroke={colors.grid} strokeWidth={0.5} listening={false} />);
    }
  }
  return (
    <>
      <Rect width={width} height={height} fill={colors.surface} listening={false} />
      {lines}
    </>
  );
}
