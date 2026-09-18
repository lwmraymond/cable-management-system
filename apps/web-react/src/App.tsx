import { Refine, type NotificationProvider } from "@refinedev/core";
import { Alert, App as AntApp, Button, Card, Spin } from "antd";
import routerProvider from "@refinedev/react-router";
import { QueryClient } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef } from "react";
import { ApiError } from "./api/client";
import { buildDataProvider } from "./api/dataProvider";
import { useWorkspaceSession } from "./auth/useWorkspaceSession";
import { safeReturnTo } from "./auth/browserSession";
import { startOidcLogin } from "./auth/oidc";
import { WorkspaceMenu } from "./workspaces/WorkspaceMenu";
import { AppShell } from "./components/AppShell";
import { BusinessRoutes } from "./components/BusinessRoutes";
import { PageErrorBoundary } from "./components/PageErrorBoundary";
import { LoginPage } from "./pages/LoginPage";
import { OidcCallbackPage } from "./pages/OidcCallbackPage";

const SpatialPage = lazy(() => import("./spatial/SpatialPage"));

export default function App() { return <BrowserRouter basename="/app-next"><AccountApplication /></BrowserRouter>; }

function AccountApplication() {
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  const target = safeReturnTo(params.get("return_to") ?? `/app-next${location.pathname}${location.search}${location.hash}`);
  const requestedWorkspace = new URL(target, window.location.origin).searchParams.get("workspace");
  const account = useWorkspaceSession(requestedWorkspace);
  const { context, updateContext } = account;
  const autoStarted = useRef(false);
  const entry = location.pathname === "/auth/entry";
  const login = location.pathname === "/login";
  useEffect(() => {
    if (!account.loading && !account.session && account.config?.sso_enabled && entry && account.error instanceof ApiError && account.error.status === 401 && !autoStarted.current) {
      autoStarted.current = true; void startOidcLogin(target);
    }
  }, [account.loading, account.session, account.config, account.error, entry, target]);
  const { notification } = AntApp.useApp();
  const notificationProvider = useMemo<NotificationProvider>(() => ({
    open: ({ key, type, message, description }) => notification.open({
      key, type: type === "progress" ? "info" : type, title: message, description,
    }),
    close: key => notification.destroy(key),
  }), [notification]);
  const getContext = useCallback(() => context, [context]);
  const dataProvider = useMemo(() => buildDataProvider(getContext), [getContext]);
  const queryClient = useMemo(() => new QueryClient(), [account.contentKey]);
  useEffect(() => () => queryClient.clear(), [queryClient]);
  if (location.pathname === "/oidc/callback") return <OidcCallbackPage />;
  if (account.loading) return <div className="login-card" role="status"><Spin /> 正在验证账号和工作空间…</div>;
  if (!account.session || login) return <LoginPage config={account.config} error={account.error?.message} returnTo={target} onRetry={() => { if (login && account.session) navigate("/"); else account.retry(); }} />;
  if (entry) return <Navigate to={target.replace(/^\/app-next/, "") || "/"} replace />;
  const workspaceMenu = <WorkspaceMenu session={account.session} context={context} onSelect={workspace => { account.selectWorkspace(workspace); navigate("/"); }} onRefresh={account.refresh} onSignOut={() => void account.signOut()} />;
  if (account.accessDenied) return <Card className="login-card"><h1>无法访问指定工作空间</h1><p>{account.error?.message ?? "请明确选择你有权限访问的工作空间。"}</p>{workspaceMenu}<Button onClick={account.retry}>重新检查权限</Button></Card>;
  if (!context.tenantId) return <Card className="login-card"><h1>创建你的第一个工作空间</h1><p>个人空间独立保存机房和线缆资料，共享空间允许授权成员协作。</p>{workspaceMenu}</Card>;
  const pageKey = [location.pathname, context.tenantId, context.actorId, context.projectId, context.locationId].join(":");
  return <>
    <Refine key={account.contentKey} routerProvider={routerProvider} dataProvider={dataProvider} notificationProvider={notificationProvider} options={{ syncWithLocation: true, warnWhenUnsavedChanges: true, reactQuery: { clientConfig: queryClient } }} resources={[{ name: "locations" }, { name: "racks" }, { name: "cables" }]}>
      <Routes>
        <Route path="/3d" element={<PageErrorBoundary key={pageKey}><Suspense fallback={<div role="status" aria-live="polite" style={{ padding: 40 }}>正在加载三维工作区…</div>}><SpatialPage getContext={getContext} onContextChange={updateContext} /></Suspense></PageErrorBoundary>} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/oidc/callback" element={<OidcCallbackPage />} />
        <Route path="/*" element={<AppShell context={context} onContextChange={updateContext} accountControl={workspaceMenu}><PageErrorBoundary key={pageKey}><BusinessRoutes getContext={getContext} onContextChange={updateContext} /></PageErrorBoundary></AppShell>} />
      </Routes>
    </Refine>
    {account.error && <div className="account-error"><Alert type="error" showIcon title="账号操作未完成" description={account.error.message} action={<Button onClick={account.retry}>重新检查</Button>} /></div>}
  </>;
}
