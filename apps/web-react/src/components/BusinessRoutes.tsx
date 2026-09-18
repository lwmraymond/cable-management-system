import { Spin } from "antd";
import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { InfrastructureContext } from "../api/context";
import { DashboardPage } from "../pages/DashboardPage";
import { LocationsPage } from "../pages/LocationsPage";
import { NotFoundPage } from "../pages/NotFoundPage";

const RacksPage = lazy(() => import("../pages/RacksPage").then(module => ({ default: module.RacksPage })));
const CablesPage = lazy(() => import("../pages/CablesPage").then(module => ({ default: module.CablesPage })));
const FiberPage = lazy(() => import("../pages/FiberPage").then(module => ({ default: module.FiberPage })));
const FiberTopologyPage = lazy(() => import("../pages/FiberTopologyPage").then(module => ({ default: module.FiberTopologyPage })));
const FloorPlanPage = lazy(() => import("../pages/FloorPlanPage").then(module => ({ default: module.FloorPlanPage })));
const ErrorComponent = lazy(() => import("@refinedev/antd").then(module => ({ default: module.ErrorComponent })));

type Props = {
  getContext: () => InfrastructureContext;
  onContextChange: (context: InfrastructureContext) => void;
};

export function BusinessRoutes({ getContext, onContextChange }: Props) {
  const { pathname } = useLocation();
  return <Suspense key={pathname} fallback={
    <div className="workspace-panel" role="status" aria-live="polite" style={{ padding: 32 }}>
      <Spin size="small" /> 正在加载工作页面…
    </div>
  }>
    <Routes>
      <Route index element={<DashboardPage getContext={getContext} />} />
      <Route path="locations" element={<LocationsPage getContext={getContext} onContextChange={onContextChange} />} />
      <Route path="racks" element={<RacksPage getContext={getContext} />} />
      <Route path="cables" element={<CablesPage getContext={getContext} />} />
      <Route path="fiber" element={<FiberPage getContext={getContext} />} />
      <Route path="fiber-topology" element={<FiberTopologyPage getContext={getContext} />} />
      <Route path="floor-plans" element={<FloorPlanPage getContext={getContext} />} />
      <Route path="error" element={<ErrorComponent />} />
      <Route path="404" element={<NotFoundPage />} />
      <Route path="*" element={<Navigate to="/404" replace />} />
    </Routes>
  </Suspense>;
}
