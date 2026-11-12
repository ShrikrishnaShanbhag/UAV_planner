import katex from "katex";
import { useState } from "react";
import { Card } from "@/components/ui";
import { explainEnergy, fmt } from "@/lib/format";

function Equation({ tex }: { tex: string }) {
  const html = katex.renderToString(tex, { displayMode: true, throwOnError: false });
  return <div className="overflow-x-auto" dangerouslySetInnerHTML={{ __html: html }} />;
}

function Slider({ label, value, max, onChange }: {
  label: string;
  value: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block text-xs">
      <span className="flex justify-between">
        <span>{label}</span>
        <span className="tabular-nums">{value} EU</span>
      </span>
      <input type="range" min={0} max={max} value={value} aria-label={label} onChange={(e) => onChange(Number(e.target.value))} className="w-full accent-blue-600" />
    </label>
  );
}

function FeasibilityExample() {
  const [available, setAvailable] = useState(25);
  const [task, setTask] = useState(20);
  const [ret, setRet] = useState(10);
  const [reserve, setReserve] = useState(0);
  const { needed, margin, feasible } = explainEnergy(available, task, ret, reserve);
  return (
    <Card title="Worked example: 25 EU vs 30 EU">
      <p className="mb-3 text-sm text-stone-600 dark:text-stone-300">
        A UAV has 25 EU left. A task needs 20 EU and the trip home another 10 EU. Move the sliders to see when the assignment becomes feasible.
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Slider label="Energy available" value={available} max={100} onChange={setAvailable} />
          <Slider label="Task energy" value={task} max={60} onChange={setTask} />
          <Slider label="Return energy" value={ret} max={60} onChange={setRet} />
          <Slider label="Safety reserve" value={reserve} max={30} onChange={setReserve} />
        </div>
        <div data-testid="example-result" className="flex flex-col justify-center rounded-md bg-stone-100 p-4 dark:bg-stone-800">
          <p className="text-2xl font-semibold">{feasible ? "✓ Feasible" : "✗ Infeasible"}</p>
          <p className="mt-1 text-sm">
            Needs {task} + {ret} + {reserve} = {needed} EU; has {available} EU (margin {fmt(margin, 0)} EU).
          </p>
          <p className="mt-2 text-xs text-stone-600 dark:text-stone-300">
            {feasible ? "This UAV can take the task and get home safely." : "Another UAV, or a closer landing point, is needed."}
          </p>
        </div>
      </div>
    </Card>
  );
}

export default function Model() {
  return (
    <div className="space-y-4">
      <FeasibilityExample />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Energy model">
          <Equation tex={String.raw`E_{\text{travel}} = k_i \cdot \varepsilon \qquad \varepsilon_{ab} = d_{ab}\,\frac{m(a)+m(b)}{2}`} />
          <Equation tex={String.raw`E_{\text{return}} = \min_{s \in \text{stations}} E(\text{last} \to s) \qquad E_{\text{reserve}} = \rho\, B_i^{\max}`} />
          <p className="text-xs text-stone-600 dark:text-stone-300">
            Normalized mode uses a per-UAV rate k. Physical mode uses cruise power and time. ε is length weighted by any energy zone.
          </p>
        </Card>
        <Card title="Feasibility constraint">
          <Equation tex={String.raw`E_{\text{route}} + \sum E_{\text{task}} + E_{\text{return}} + E_{\text{reserve}} \le B_i`} />
          <Equation tex={String.raw`B_i - \text{spent}_m - E_{\text{return}}(j_m) \ge E_{\text{reserve}} \quad \forall m`} />
          <p className="text-xs text-stone-600 dark:text-stone-300">The second line is the strict safety check: a UAV must be able to reach a station from every task on its route.</p>
        </Card>
        <Card title="Task assignment">
          <p className="text-sm" data-testid="assignment-note">Tasks are assigned by an internal energy-feasible insertion heuristic.</p>
        </Card>
        <Card title="Global objective">
          <Equation tex={String.raw`Z = \alpha\sum_i E_i + \beta\max_i T_i + \gamma\sum_i D_i + \delta\,\mathrm{Var}(L_i) + \lambda\!\!\sum_{j\ \text{unassigned}}\!\! P_j M`} />
          <p className="text-xs text-stone-600 dark:text-stone-300">The first four terms are normalized by reference values from the nearest-UAV baseline.</p>
        </Card>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Priority order">
          <ol className="list-decimal space-y-1 pl-5 text-sm">
            <li>Satisfy safety and hard constraints.</li>
            <li>Complete mandatory tasks.</li>
            <li>Minimize mission time and energy.</li>
            <li>Balance the workload.</li>
          </ol>
          <p className="mt-2 text-xs text-stone-600 dark:text-stone-300">Plans are compared in this order, with Z breaking ties at levels 3 and 4.</p>
        </Card>
        <Card title="Assumptions and limitations">
          <ul className="list-disc space-y-1 pl-5 text-sm">
            <li>2D flight at constant speed, with no wind.</li>
            <li>Charging stops are optional and off by default, with a simple linear charge model.</li>
            <li>No collision avoidance between UAVs.</li>
            <li>Energy values are illustrative simulation settings.</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
