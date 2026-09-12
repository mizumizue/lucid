import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import "sonner/dist/styles.css";
import { AppShell } from "./components";
import {
  ActivityPage,
  ArtifactDetailPage,
  DailyPage,
  GuidePage,
  JobsPage,
  MemoryPage,
  OverviewPage,
  RecallGraphPage,
  SessionDetailPage,
  SessionsPage,
} from "./views";
import { RefreshProvider } from "./hooks";

export default function App() {
  return (
    <BrowserRouter>
      <RefreshProvider>
        <Toaster closeButton position="bottom-right" richColors />
        <Routes>
          <Route element={<AppShell />} path="/">
            <Route element={<OverviewPage />} index />
            <Route element={<SessionsPage />} path="sessions" />
            <Route element={<SessionDetailPage />} path="sessions/:id" />
            <Route element={<JobsPage />} path="jobs" />
            <Route element={<MemoryPage />} path="memory" />
            <Route element={<RecallGraphPage />} path="recall-graph" />
            <Route element={<ActivityPage />} path="activity" />
            <Route element={<ArtifactDetailPage />} path="artifacts/:id" />
            <Route element={<DailyPage />} path="daily" />
            <Route element={<GuidePage />} path="guide" />
            <Route element={<Navigate replace to="/" />} path="*" />
          </Route>
        </Routes>
      </RefreshProvider>
    </BrowserRouter>
  );
}

