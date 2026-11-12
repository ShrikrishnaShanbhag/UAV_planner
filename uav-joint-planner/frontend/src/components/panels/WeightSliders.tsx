import type { CostWeights } from "@/api/client";

const SLIDERS: { key: "w_d" | "w_e" | "w_t" | "w_l"; label: string; hint: string }[] = [
  { key: "w_d", label: "Distance w_d", hint: "Weight of incremental distance" },
  { key: "w_e", label: "Energy w_e", hint: "Weight of incremental energy" },
  { key: "w_t", label: "Time w_t", hint: "Weight of the increase in completion time" },
  { key: "w_l", label: "Workload w_l", hint: "Weight of the increase in workload imbalance" },
];

function Slider({ label, hint, value, max, step, onChange, unit = "" }: {
  label: string;
  hint: string;
  value: number;
  max: number;
  step: number;
  unit?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block text-xs" title={hint}>
      <span className="flex justify-between">
        <span>{label}</span>
        <span className="tabular-nums text-stone-500 dark:text-stone-400">
          {value}
          {unit}
        </span>
      </span>
      <input
        type="range"
        min={0}
        max={max}
        step={step}
        value={value}
        aria-label={label}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-blue-600"
      />
    </label>
  );
}

export function WeightSliders({ weights, reserve, onWeights, onReserve, energyOnly = false }: {
  weights: CostWeights;
  reserve: number;
  energyOnly?: boolean;
  onWeights: (weights: CostWeights) => void;
  onReserve: (percent: number) => void;
}) {
  return (
    <div className="space-y-2">
      {SLIDERS.filter((s) => !energyOnly || s.key === "w_e").map((s) => (
        <Slider
          key={s.key}
          label={s.label}
          hint={s.hint}
          value={weights[s.key]}
          max={1}
          step={0.05}
          onChange={(value) => onWeights({ ...weights, [s.key]: value })}
        />
      ))}
      <Slider label="Reserve" hint="Battery kept in reserve" value={reserve} max={40} step={1} unit="%" onChange={onReserve} />
    </div>
  );
}
