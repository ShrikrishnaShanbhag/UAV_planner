import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell as BarCell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { UavFrame } from "@/api/client";
import { Legend } from "@/components/ui";
import { algorithmColor, seriesColor } from "@/lib/colors";
import { fmt } from "@/lib/format";
import type { energyBreakdown } from "@/lib/scenario";
import { useUi } from "@/store/ui";

const AXIS = { stroke: "#898781", fontSize: 11 };
const tooltipStyle = {
  contentStyle: { fontSize: 12, borderRadius: 6, border: "1px solid #c3c2b7" },
  labelStyle: { color: "#0b0b0b" },
  itemStyle: { color: "#0b0b0b" },
};

function useGrid() {
  return useUi((s) => (s.theme === "dark" ? "#2c2c2a" : "#e1e0d9"));
}

export function MetricBar({ data, unit, height = 180 }: {
  data: { algorithm: string; label: string; value: number | null }[];
  unit: string;
  height?: number;
}) {
  const theme = useUi((s) => s.theme);
  const grid = useGrid();
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke={grid} />
        <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={{ stroke: "#c3c2b7" }} interval={0} />
        <YAxis tick={AXIS} tickLine={false} axisLine={false} width={44} />
        <Tooltip {...tooltipStyle} formatter={(value: number) => [fmt(value, 2), unit]} />
        <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={36}>
          {data.map((d) => (
            <BarCell key={d.algorithm} fill={algorithmColor(d.algorithm, theme)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function BatteryTimeline({ frames, uavIds, t, reserve }: {
  frames: { t: number; uavs: UavFrame[] }[];
  uavIds: string[];
  t?: number;
  reserve?: number;
}) {
  const theme = useUi((s) => s.theme);
  const grid = useGrid();
  const data = frames.map((f) => ({
    t: Math.round(f.t),
    ...Object.fromEntries(f.uavs.map((u) => [u.id, Number(u.battery.toFixed(2))])),
  }));
  return (
    <div data-testid="battery-timeline">
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke={grid} />
          <XAxis dataKey="t" type="number" domain={[0, "dataMax"]} tick={AXIS} tickLine={false} unit="s" />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} width={36} domain={[0, "auto"]} />
          <Tooltip {...tooltipStyle} formatter={(value: number) => `${fmt(value)} EU`} labelFormatter={(v) => `${v} s`} />
          {reserve !== undefined && <ReferenceLine y={reserve} stroke="#898781" />}
          {t !== undefined && <ReferenceLine x={Math.round(t)} stroke="#52514e" />}
          {uavIds.map((id, i) => (
            <Line key={id} dataKey={id} dot={false} strokeWidth={2} stroke={seriesColor(i, theme)} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <Legend items={uavIds.map((id, i) => ({ label: id, color: seriesColor(i, theme) }))} />
    </div>
  );
}

const STATUS_COLOR: Record<string, number> = { en_route: 0, working: 1, returning: 2 };

/** Schedule view built from sampled statuses, so it stays valid after any replan. */
export function GanttChart({ frames, uavIds }: { frames: { t: number; uavs: UavFrame[] }[]; uavIds: string[] }) {
  const theme = useUi((s) => s.theme);
  const end = frames.at(-1)?.t ?? 0;
  if (!end) return null;
  const rows = uavIds.map((id) => {
    const bars: { status: string; start: number; stop: number }[] = [];
    frames.forEach((frame, i) => {
      const status = frame.uavs.find((u) => u.id === id)?.status ?? "idle";
      const stop = frames[i + 1]?.t ?? frame.t;
      const last = bars.at(-1);
      if (last && last.status === status) last.stop = stop;
      else bars.push({ status, start: frame.t, stop });
    });
    return { id, bars: bars.filter((b) => b.status in STATUS_COLOR) };
  });
  return (
    <div data-testid="gantt">
      <div className="space-y-1.5">
        {rows.map((row) => (
          <div key={row.id} className="flex items-center gap-2">
            <span className="w-20 shrink-0 truncate text-xs">{row.id}</span>
            <div className="relative h-4 flex-1 rounded bg-stone-100 dark:bg-stone-800">
              {row.bars.map((bar, i) => (
                <div
                  key={i}
                  title={`${bar.status.replace("_", " ")}: ${fmt(bar.start, 0)}–${fmt(bar.stop, 0)} s`}
                  className="absolute inset-y-0 rounded-sm"
                  style={{
                    left: `${(bar.start / end) * 100}%`,
                    width: `calc(${((bar.stop - bar.start) / end) * 100}% - 1px)`,
                    background: seriesColor(STATUS_COLOR[bar.status], theme),
                  }}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-2">
        <Legend
          items={[
            { label: "Flying to task", color: seriesColor(0, theme) },
            { label: "Working", color: seriesColor(1, theme) },
            { label: "Returning", color: seriesColor(2, theme) },
          ]}
        />
      </div>
    </div>
  );
}

/** Stacked energy per UAV. The hues match the battery bars. */
export function EnergyBreakdownChart({ rows }: { rows: ReturnType<typeof energyBreakdown>[] }) {
  const theme = useUi((s) => s.theme);
  const grid = useGrid();
  const parts = [
    { key: "travel", label: "Travel", color: seriesColor(0, theme) },
    { key: "task", label: "Hover and task", color: seriesColor(1, theme) },
    { key: "ret", label: "Return", color: seriesColor(2, theme) },
    { key: "takeoffLanding", label: "Takeoff and landing", color: seriesColor(3, theme) },
    { key: "reserve", label: "Reserve", color: "#898781" },
  ];
  return (
    <div data-testid="energy-breakdown">
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke={grid} />
          <XAxis dataKey="uav" tick={AXIS} tickLine={false} axisLine={{ stroke: "#c3c2b7" }} interval={0} />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} width={36} unit=" EU" />
          <Tooltip {...tooltipStyle} formatter={(value: number) => `${fmt(value)} EU`} />
          {parts.map((part) => (
            <Bar key={part.key} dataKey={part.key} name={part.label} stackId="energy" fill={part.color} stroke={theme === "dark" ? "#1c1917" : "#ffffff"} maxBarSize={44} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
      <Legend items={parts} />
    </div>
  );
}

export function ParetoScatter({ points }: { points: { energy: number; makespan: number; share: number }[] }) {
  const theme = useUi((s) => s.theme);
  const grid = useGrid();
  return (
    <ResponsiveContainer width="100%" height={240}>
      <ScatterChart margin={{ top: 8, right: 8, left: 0, bottom: 16 }}>
        <CartesianGrid stroke={grid} />
        <XAxis dataKey="energy" type="number" name="Energy" unit=" EU" domain={["auto", "auto"]} tick={AXIS} tickLine={false} />
        <YAxis dataKey="makespan" type="number" name="Mission time" unit=" s" domain={["auto", "auto"]} tick={AXIS} tickLine={false} axisLine={false} width={60} />
        <Tooltip {...tooltipStyle} formatter={(value: number) => fmt(value)} />
        <Scatter data={points} fill={seriesColor(0, theme)} isAnimationActive={false} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export interface BoxStat {
  label: string;
  algorithm: string;
  q1: number;
  median: number;
  q3: number;
  low: number;
  high: number;
}

/** Horizontal summary: IQR box, median tick and a whisker for the bootstrap CI of the mean. */
export function BoxPlot({ stats, unit }: { stats: BoxStat[]; unit: string }) {
  const theme = useUi((s) => s.theme);
  if (!stats.length) return null;
  const low = Math.min(...stats.map((s) => Math.min(s.q1, s.low)));
  const high = Math.max(...stats.map((s) => Math.max(s.q3, s.high)));
  const span = high - low || 1;
  const x = (value: number) => `${((value - low) / span) * 100}%`;
  return (
    <div className="space-y-2" data-testid="box-plot">
      {stats.map((s) => (
        <div key={s.algorithm} className="flex items-center gap-2 text-xs" title={`Q1 ${fmt(s.q1)} · median ${fmt(s.median)} · Q3 ${fmt(s.q3)} ${unit}`}>
          <span className="w-40 shrink-0 truncate">{s.label}</span>
          <div className="relative h-4 flex-1">
            <div className="absolute top-1/2 h-px bg-stone-400" style={{ left: x(s.low), width: `${((s.high - s.low) / span) * 100}%` }} />
            <div
              className="absolute inset-y-0 rounded-sm opacity-70"
              style={{ left: x(s.q1), width: `${Math.max(((s.q3 - s.q1) / span) * 100, 0.5)}%`, background: algorithmColor(s.algorithm, theme) }}
            />
            <div className="absolute inset-y-0 w-0.5 bg-stone-900 dark:bg-stone-100" style={{ left: x(s.median) }} />
          </div>
          <span className="w-14 text-right tabular-nums">{fmt(s.median)}</span>
        </div>
      ))}
      <p className="text-xs text-stone-500 dark:text-stone-400">
        Box: interquartile range. Tick: median. Line: 95% CI of the mean. Range {fmt(low)}–{fmt(high)} {unit}.
      </p>
    </div>
  );
}
