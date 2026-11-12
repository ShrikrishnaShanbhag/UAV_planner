import { Line, Rect } from "react-konva";
import type { Obstacle } from "@/api/client";
import type { LayerProps } from "./types";

function fillOf(region: Obstacle, props: LayerProps): string {
  if (region.kind === "energy_zone") return props.colors.zone;
  return region.kind === "no_fly" ? props.colors.noFly : props.colors.obstacle;
}

export function ObstacleLayer({ regions, selectedId, ...props }: LayerProps & { regions: Obstacle[]; selectedId?: string | null }) {
  const { grid, cell, colors } = props;
  const ordered = [...regions].sort((a, b) => Number(b.kind === "energy_zone") - Number(a.kind === "energy_zone"));
  const px = (x: number, y: number) => [(x + 0.5) * cell, (grid.height - 1 - y + 0.5) * cell];
  return (
    <>
      {ordered.map((region) => {
        const fill = fillOf(region, props);
        const stroke = region.id === selectedId ? colors.highlight : undefined;
        const shapes = [];
        if (region.rect) {
          const [x0, y0, x1, y1] = region.rect;
          shapes.push(
            <Rect
              key="r"
              x={x0 * cell}
              y={(grid.height - 1 - y1) * cell}
              width={(x1 - x0 + 1) * cell}
              height={(y1 - y0 + 1) * cell}
              fill={fill}
              stroke={stroke}
              strokeWidth={2}
              listening={false}
            />,
          );
        }
        if (region.polygon) {
          shapes.push(
            <Line key="p" points={region.polygon.flatMap(([x, y]) => px(x, y))} closed fill={fill} stroke={stroke} strokeWidth={2} listening={false} />,
          );
        }
        region.cells?.forEach(([x, y], index) => {
          shapes.push(
            <Rect key={`c${index}`} x={x * cell} y={(grid.height - 1 - y) * cell} width={cell} height={cell} fill={fill} listening={false} />,
          );
        });
        return shapes;
      })}
    </>
  );
}
