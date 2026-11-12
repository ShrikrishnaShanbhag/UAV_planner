import type { ButtonHTMLAttributes, ReactNode } from "react";
import { useUi } from "@/store/ui";

export function Button({
  variant = "secondary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" }) {
  const styles = {
    primary: "bg-blue-600 text-white hover:bg-blue-700 disabled:bg-blue-300 dark:disabled:bg-blue-900",
    secondary:
      "border border-stone-300 bg-white text-stone-800 hover:bg-stone-100 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-100 dark:hover:bg-stone-800",
    ghost: "text-stone-600 hover:bg-stone-100 dark:text-stone-300 dark:hover:bg-stone-800",
  }[variant];
  return (
    <button
      className={`rounded-md px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60 ${styles} ${className}`}
      {...props}
    />
  );
}

export function Card({ title, actions, children, className = "" }: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-stone-200 bg-white p-4 dark:border-stone-800 dark:bg-stone-900 ${className}`}>
      {(title || actions) && (
        <header className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">{title}</h2>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-stone-500 dark:text-stone-400">{children}</p>;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <p role="status" className="py-6 text-center text-sm text-stone-500 dark:text-stone-400">
      {label}…
    </p>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block text-xs font-medium text-stone-600 dark:text-stone-300" title={hint}>
      {label}
      <div className="mt-1">{children}</div>
    </label>
  );
}

export const inputClass =
  "w-full rounded-md border border-stone-300 bg-white px-2 py-1 text-sm text-stone-900 dark:border-stone-700 dark:bg-stone-950 dark:text-stone-100";

export function Tabs<T extends string>({ value, options, onChange }: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div role="tablist" className="flex flex-wrap gap-1">
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={o.value === value}
          onClick={() => onChange(o.value)}
          className={`rounded-md px-3 py-1 text-sm ${
            o.value === value
              ? "bg-stone-800 text-white dark:bg-stone-200 dark:text-stone-900"
              : "text-stone-600 hover:bg-stone-100 dark:text-stone-300 dark:hover:bg-stone-800"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Toasts() {
  const toasts = useUi((s) => s.toasts);
  const dismiss = useUi((s) => s.dismiss);
  return (
    <div className="fixed bottom-4 right-4 z-50 flex w-80 max-w-[90vw] flex-col gap-2" aria-live="polite">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role="alert"
          className={`flex items-start justify-between gap-3 rounded-md px-3 py-2 text-sm text-white shadow-lg ${
            toast.kind === "error" ? "bg-red-700" : "bg-stone-700"
          }`}
        >
          <span>{toast.message}</span>
          <button aria-label="Dismiss" onClick={() => dismiss(toast.id)}>
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-stone-600 dark:text-stone-300">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: item.color }} />
          {item.label}
        </li>
      ))}
    </ul>
  );
}
