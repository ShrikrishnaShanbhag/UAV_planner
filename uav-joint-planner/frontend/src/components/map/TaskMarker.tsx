import { Circle, Group, Text } from "react-konva";
import type { Task } from "@/api/client";
import { toPixels } from "./layers";
import type { LayerProps } from "./types";

export function TaskMarker({ task, order, assigned, highlighted, onSelect, ...props }: LayerProps & {
  task: Task;
  order?: number;
  assigned: boolean;
  highlighted: boolean;
  onSelect?: (id: string) => void;
}) {
  const { grid, cell, colors } = props;
  const [x, y] = toPixels(grid, cell, task.cell[0], task.cell[1]);
  const radius = Math.max(5, cell * 0.42) + task.priority * 0.6;
  return (
    <Group
      x={x}
      y={y}
      onClick={(event) => {
        event.cancelBubble = true;
        onSelect?.(task.id);
      }}
      onTap={() => onSelect?.(task.id)}
    >
      {highlighted && <Circle radius={radius + 4} stroke={colors.highlight} strokeWidth={2} />}
      <Circle radius={radius} fill={assigned ? colors.ink : colors.surface} stroke={colors.ink} strokeWidth={1.5} />
      {order !== undefined && (
        <Text
          text={String(order)}
          fontSize={Math.max(9, radius)}
          fontStyle="bold"
          fill={colors.surface}
          width={radius * 2}
          height={radius * 2}
          offsetX={radius}
          offsetY={radius}
          align="center"
          verticalAlign="middle"
          listening={false}
        />
      )}
    </Group>
  );
}
