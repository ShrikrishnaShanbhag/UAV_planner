import { create } from "zustand";
import { api } from "@/api/client";
import type { LogEntry, Plan, ReplanFrame, SimFrame, SimulationState, UavFrame } from "@/api/client";
import { reportError } from "@/store/ui";

interface TimelineFrame {
  t: number;
  uavs: UavFrame[];
}

interface SimStore {
  simId: string | null;
  state: SimulationState | null;
  timeline: TimelineFrame[];
  connected: boolean;
  playing: boolean;
  t: number;
  endTime: number;
  speed: number;
  uavs: UavFrame[];
  log: LogEntry[];
  plan: Plan | null;
  oldPlan: Plan | null;
  lastReplan: ReplanFrame | null;
  connect: (simId: string) => Promise<void>;
  disconnect: () => void;
  receive: (frame: SimFrame) => void;
  send: (cmd: string, payload?: Record<string, unknown>) => void;
  reset: () => void;
}

let socket: WebSocket | null = null;

const initial = {
  simId: null,
  state: null,
  timeline: [],
  connected: false,
  playing: false,
  t: 0,
  endTime: 0,
  speed: 10,
  uavs: [],
  log: [],
  plan: null,
  oldPlan: null,
  lastReplan: null,
};

function socketUrl(simId: string): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws/simulations/${simId}`;
}

export const useSimulation = create<SimStore>((set, get) => ({
  ...initial,

  connect: async (simId) => {
    get().disconnect();
    const [state, timeline] = await Promise.all([api.simulation(simId), api.timeline(simId)]);
    set({ ...initial, simId, state, timeline: timeline.frames, plan: state.plan, endTime: state.end_time });
    socket = new WebSocket(socketUrl(simId));
    socket.onopen = () => set({ connected: true });
    socket.onclose = () => set({ connected: false, playing: false });
    socket.onerror = () => reportError(new Error("The simulation connection failed."));
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data as string);
      if (data.type === "error") reportError(new Error(data.message));
      else get().receive(data as SimFrame);
    };
  },

  disconnect: () => {
    if (socket) {
      socket.onclose = null;
      socket.close();
      socket = null;
    }
    set({ connected: false, playing: false });
  },

  receive: (frame) => {
    const { log, plan, simId } = get();
    const next: Partial<SimStore> = {
      t: frame.t,
      uavs: frame.uavs,
      endTime: frame.end_time,
      playing: frame.playing ?? false,
      log: frame.seeked ? log : [...log, ...frame.events],
    };
    if (frame.replan) {
      next.oldPlan = plan;
      next.plan = frame.replan.plan;
      next.lastReplan = frame.replan;
      if (simId) {
        // A replan changes the rest of the mission, so refresh the charts.
        api.timeline(simId).then((timeline) => set({ timeline: timeline.frames })).catch(reportError);
        api.simulation(simId).then((state) => set({ state })).catch(reportError);
      }
    }
    set(next);
  },

  send: (cmd, payload) => {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ cmd, payload }));
      if (cmd === "speed" && payload) set({ speed: Number(payload.value) });
    }
  },

  reset: () => {
    get().disconnect();
    set(initial);
  },
}));
