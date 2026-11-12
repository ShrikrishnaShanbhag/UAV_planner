import { createBrowserRouter } from "react-router-dom";
import App from "./App";
import Compare from "./pages/Compare";
import Dashboard from "./pages/Dashboard";
import Experiments from "./pages/Experiments";
import Model from "./pages/Model";
import Planner from "./pages/Planner";
import ScenarioBuilder from "./pages/ScenarioBuilder";
import Simulation from "./pages/Simulation";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: "builder", element: <ScenarioBuilder /> },
      { path: "planner", element: <Planner /> },
      { path: "simulation", element: <Simulation /> },
      { path: "compare", element: <Compare /> },
      { path: "experiments", element: <Experiments /> },
      { path: "model", element: <Model /> },
    ],
  },
]);
