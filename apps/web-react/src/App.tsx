import { Refine } from "@refinedev/core";
import { ErrorComponent, notificationProvider } from "@refinedev/antd";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { useCallback, useMemo, useState } from "react";
import { buildDataProvider } from "./api/dataProvider";
import { loadContext, saveContext, type InfrastructureContext } from "./api/context";
import { AppShell } from "./components/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { LocationsPage } from "./pages/LocationsPage";
import { RacksPage } from "./pages/RacksPage";
import { CablesPage } from "./pages/CablesPage";
import { FiberPage } from "./pages/FiberPage";
import { FiberTopologyPage } from "./pages/FiberTopologyPage";
import { FloorPlanPage } from "./pages/FloorPlanPage";
import { LoginPage } from "./pages/LoginPage";
import { OidcCallbackPage } from "./pages/OidcCallbackPage";
import { NotFoundPage } from "./pages/NotFoundPage";

export default function App() {
  const [context, setContext] = useState<InfrastructureContext>(() => loadContext());
  const getContext = useCallback(() => context, [context]);
  const dataProvider = useMemo(() => buildDataProvider(getContext), [getContext]);
  const updateContext = (next: InfrastructureContext) => setContext(saveContext(next));
  return <BrowserRouter basename="/app-next">
    <Refine dataProvider={dataProvider} notificationProvider={notificationProvider} options={{ syncWithLocation: true, warnWhenUnsavedChanges: true }} resources={[{ name: "locations" }, { name: "racks" }, { name: "cables" }]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/oidc/callback" element={<OidcCallbackPage />} />
        <Route path="/*" element={<AppShell context={context} onContextChange={updateContext}><Routes><Route index element={<DashboardPage getContext={getContext} />} /><Route path="locations" element={<LocationsPage getContext={getContext} />} /><Route path="racks" element={<RacksPage getContext={getContext} />} /><Route path="cables" element={<CablesPage getContext={getContext} />} /><Route path="fiber" element={<FiberPage getContext={getContext} />} /><Route path="fiber-topology" element={<FiberTopologyPage getContext={getContext} />} /><Route path="floor-plans" element={<FloorPlanPage getContext={getContext} />} /><Route path="error" element={<ErrorComponent />} /><Route path="404" element={<NotFoundPage />} /><Route path="*" element={<Navigate to="/404" replace />} /></Routes></AppShell>} />
      </Routes>
    </Refine>
  </BrowserRouter>;
}
