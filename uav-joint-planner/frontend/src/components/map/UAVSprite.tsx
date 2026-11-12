import { Arc, Circle, Group, Text } from "react-konva";
import { seriesColor } from "@/lib/colors";
import { toPixels } from "./layers";
import type { LayerProps } from "./types";

const LABEL_WIDTH = 64;

export function UAVSprite({ id, x, y, index, batteryFraction, unavailable, labelRow = 0, ...props }: LayerProps & {
  id: string;
  x: number;
  y: number;
  index: number;
  batteryFraction: number;
  unavailable?: boolean;
  labelRow?: number;
}) {
  const { grid, cell, colors, theme } = props;
  const [px, py] = toPixels(grid, cell, x, y);
  const radius = Math.max(6, cell * 0.55);
  const color = unavailable ? colors.inkSoft : seriesColor(index, theme);
  const flip = px + radius + 7 + LABEL_WIDTH > grid.width * cell;
  return (
    <Group x={px} y={py} listening={false}>
      <Circle radius={radius + 4} stroke={colors.grid} strokeWidth={3} />
      <Arc
        innerRadius={radius + 2.5}
        outerRadius={radius + 5.5}
        angle={Math.max(0, Math.min(1, batteryFraction)) * 360}
        rotation={-90}
        fill={color}
      />
      <Circle radius={radius} fill={color} stroke={colors.surface} strokeWidth={2} />
      {cell >= 9 && (
        <Text
          x={flip ? -(radius + 7 + LABEL_WIDTH) : radius + 7}
          y={-5 + labelRow * 12}
          width={LABEL_WIDTH}
          align={flip ? "right" : "left"}
          text={id}
          fontSize={10}
          fontStyle="bold"
          fill={colors.ink}
        />
      )}
    </Group>
  );
}
