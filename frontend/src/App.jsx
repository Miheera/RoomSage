import { lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import ProjectsPage from "./pages/ProjectsPage";
import CapturePage from "./pages/CapturePage";

// three.js + Spark are the heavy part of the bundle. Load them only when a room is opened,
// so the home and capture screens stay fast on a phone.
const WorkspacePage = lazy(() => import("./pages/WorkspacePage"));

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<ProjectsPage />} />
        <Route path="new" element={<CapturePage />} />
        <Route path="project/:id" element={<WorkspacePage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
