import { create } from "zustand";
import type { Theme } from "@/lib/colors";

export interface Toast {
  id: number;
  kind: "error" | "info";
  message: string;
}

interface UiState {
  theme: Theme;
  toasts: Toast[];
  toggleTheme: () => void;
  notify: (message: string, kind?: Toast["kind"]) => void;
  dismiss: (id: number) => void;
}

let nextToast = 1;

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") return saved;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export const useUi = create<UiState>((set, get) => ({
  theme: initialTheme(),
  toasts: [],
  toggleTheme: () => {
    const theme: Theme = get().theme === "light" ? "dark" : "light";
    try {
      localStorage.setItem("theme", theme);
    } catch {
      // Storage may be unavailable; the theme still applies for this session.
    }
    applyTheme(theme);
    set({ theme });
  },
  notify: (message, kind = "error") => {
    const id = nextToast++;
    set((state) => ({ toasts: [...state.toasts, { id, kind, message }] }));
    setTimeout(() => get().dismiss(id), 6000);
  },
  dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
}));

export function reportError(error: unknown): void {
  useUi.getState().notify(error instanceof Error ? error.message : "Something went wrong.");
}
