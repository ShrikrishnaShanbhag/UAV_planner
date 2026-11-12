import { Rect, Text } from "react-konva";
import type { Station } from "@/api/client";
import { toPixels } from "./layers";
import type { LayerProps } from "./types";

export function StationMarker({ station, ...props }: LayerProps & { station: Station }) {
  const { grid, cell, colors } = props;
  const [x, y] = toPixels(grid, cell, station.cell[0], station.cell[1]);
  const size = Math.max(10, cell * 1.1);
  return (
    <>
      <Rect x={x - size / 2} y={y - size / 2} width={size} height={size} cornerRadius={2} fill={colors.ink} listening={false} />
      {cell >= 9 && (
        <Text
          x={Math.min(x - size / 2, grid.width * cell - 56)}
          y={Math.max(y - size / 2 - 13, 0)}
          width={56}
          text={station.id}
          fontSize={10}
          fill={colors.inkSoft}
          listening={false}
        />
      )}
    </>
  );
}
