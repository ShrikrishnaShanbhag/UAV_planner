import type { KonvaEventObject } from "konva/lib/Node";
import type { Stage as StageType } from "konva/lib/Stage";
import { useEffect, useRef, useState } from "react";
import { Circle, Layer, Line, Rect, Stage } from "react-konva";
import type { Cell, Route, Scenario, UavFrame } from "@/api/client";
import { MAP_COLORS } from "@/lib/colors";
import { useUi } from "@/store/ui";
import { GridLayer } from "./GridLayer";
import { ObstacleLayer } from "./ObstacleLayer";
import { RouteLayer } from "./RouteLayer";
import { StationMarker } from "./StationMarker";
import { TaskMarker } from "./TaskMarker";
import { toPixels } from "./layers";
import type { LayerProps } from "./types";
import { UAVSprite } from "./UAVSprite";

export interface MapDraft {
  rect?: [number, number, number, number];
  polygon?: Cell[];
}

interface MapCanvasProps {
  scenario: Scenario;
  routes?: Route[];
  oldRoutes?: Route[];
  liveUavs?: UavFrame[];
  highlightTask?: string | null;
  highlightUav?: string | null;
  selectedRegion?: string | null;
  draft?: MapDraft | null;
  pan?: boolean;
  label?: string;
  onCellDown?: (cell: Cell) => void;
  onCellMove?: (cell: Cell) => void;
  onCellUp?: (cell: Cell) => void;
  onDoubleClick?: () => void;
  onTask?: (id: string) => void;
}

export function MapCanvas({
  scenario,
  routes = [],
  oldRoutes = [],
  liveUavs,
  highlightTask,
  highlightUav,
  selectedRegion,
  draft,
  pan = false,
  label = "Mission map",
  onCellDown,
  onCellMove,
  onCellUp,
  onDoubleClick,
  onTask,
}: MapCanvasProps) {
  const holder = useRef<HTMLDivElement>(null);
  const stage = useRef<StageType>(null);
  const [width, setWidth] = useState(0);
  const theme = useUi((s) => s.theme);
  const { grid } = scenario;

  useEffect(() => {
    const element = holder.current;
    if (!element) return;
    const measure = () => setWidth(element.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const cell = width ? width / grid.width : 0;
  const height = cell * grid.height;
  const layer: LayerProps = { grid, cell, colors: MAP_COLORS[theme], theme };
  const colors = layer.colors;

  const toCell = (): Cell | null => {
    const point = stage.current?.getRelativePointerPosition();
    if (!point || !cell) return null;
    const x = Math.floor(point.x / cell);
    const y = grid.height - 1 - Math.floor(point.y / cell);
    return x >= 0 && y >= 0 && x < grid.width && y < grid.height ? [x, y] : null;
  };
  const relay = (handler?: (c: Cell) => void) => () => {
    const c = toCell();
    if (c && handler) handler(c);
  };

  const onWheel = (event: KonvaEventObject<WheelEvent>) => {
    const node = stage.current;
    if (!node || !pan) return;
    event.evt.preventDefault();
    const scale = node.scaleX();
    const pointer = node.getPointerPosition();
    if (!pointer) return;
    const next = Math.min(8, Math.max(1, event.evt.deltaY < 0 ? scale * 1.15 : scale / 1.15));
    const anchor = { x: (pointer.x - node.x()) / scale, y: (pointer.y - node.y()) / scale };
    node.scale({ x: next, y: next });
    node.position({ x: pointer.x - anchor.x * next, y: pointer.y - anchor.y * next });
  };

  const placed = new Map<string, { order: number }>();
  routes.forEach((route) => route.task_ids.forEach((t, i) => placed.set(t, { order: i + 1 })));
  const uavIndex = new Map(scenario.uavs.map((u, i) => [u.id, i]));
  const sprites = liveUavs
    ? liveUavs.map((u) => {
        const uav = scenario.uavs.find((item) => item.id === u.id);
        return { ...u, fraction: u.battery / (uav?.battery_max ?? 100), unavailable: u.status === "unavailable" };
      })
    : scenario.uavs.map((u) => ({
        id: u.id,
        x: u.start[0],
        y: u.start[1],
        fraction: u.battery / u.battery_max,
        unavailable: false,
      }));

  return (
    <div ref={holder} className="w-full overflow-hidden rounded-md border border-stone-200 dark:border-stone-800" role="img" aria-label={label} data-testid="map-canvas">
      {cell > 0 && (
        <Stage
          ref={stage}
          width={width}
          height={height}
          draggable={pan}
          onWheel={onWheel}
          onMouseDown={relay(onCellDown)}
          onMouseMove={relay(onCellMove)}
          onMouseUp={relay(onCellUp)}
          onTouchStart={relay(onCellDown)}
          onTouchEnd={relay(onCellUp)}
          onDblClick={onDoubleClick}
        >
          <Layer listening={false}>
            <GridLayer {...layer} />
            <ObstacleLayer {...layer} regions={scenario.obstacles} selectedId={selectedRegion} />
          </Layer>
          <Layer listening={false}>
            {oldRoutes.length > 0 && <RouteLayer {...layer} routes={oldRoutes} scenario={scenario} faded />}
            <RouteLayer {...layer} routes={routes} scenario={scenario} highlightUav={highlightUav} />
            {draft?.rect && (
              <Rect
                x={draft.rect[0] * cell}
                y={(grid.height - 1 - draft.rect[3]) * cell}
                width={(draft.rect[2] - draft.rect[0] + 1) * cell}
                height={(draft.rect[3] - draft.rect[1] + 1) * cell}
                fill={colors.highlight}
                opacity={0.35}
              />
            )}
            {draft?.polygon && (
              <>
                <Line points={draft.polygon.flatMap(([x, y]) => toPixels(grid, cell, x, y))} stroke={colors.highlight} strokeWidth={2} />
                {draft.polygon.map(([x, y], i) => {
                  const [px, py] = toPixels(grid, cell, x, y);
                  return <Circle key={i} x={px} y={py} radius={3} fill={colors.highlight} />;
                })}
              </>
            )}
          </Layer>
          <Layer>
            {scenario.stations.map((s) => (
              <StationMarker key={s.id} {...layer} station={s} />
            ))}
            {scenario.tasks.map((task) => (
              <TaskMarker
                key={task.id}
                {...layer}
                task={task}
                order={placed.get(task.id)?.order}
                assigned={placed.has(task.id)}
                highlighted={task.id === highlightTask}
                onSelect={onTask}
              />
            ))}
            {sprites.map((u, spriteIndex) => (
              <UAVSprite
                labelRow={sprites.slice(0, spriteIndex).filter((o) => Math.round(o.x) === Math.round(u.x) && Math.round(o.y) === Math.round(u.y)).length}
                key={u.id}
                {...layer}
                id={u.id}
                x={u.x}
                y={u.y}
                index={uavIndex.get(u.id) ?? 99}
                batteryFraction={u.fraction}
                unavailable={u.unavailable}
              />
            ))}
          </Layer>
        </Stage>
      )}
    </div>
  );
}
