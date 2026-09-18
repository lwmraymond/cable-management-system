import { AimOutlined, ApartmentOutlined, ArrowLeftOutlined, BorderOutlined, DashboardOutlined, DeploymentUnitOutlined, LinkOutlined, MenuFoldOutlined, MenuOutlined, MenuUnfoldOutlined, NodeIndexOutlined } from "@ant-design/icons";
import { Button, ConfigProvider, Drawer, Grid, Layout, Menu, Tooltip } from "antd";
import { useCallback, useEffect, useState, type PropsWithChildren, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import type { InfrastructureContext } from "../api/context";
import { createApiClient } from "../api/client";
import type { LocationRecord, PageResult } from "../types";
import { TenantContextEditor } from "./TenantContextEditor";
import { useApiResource } from "./useApiResource";

const { Header, Sider, Content } = Layout;
const navigation = [
  { title: "工作空间", entries: [
    { path: "/", label: "工作台", icon: <DashboardOutlined /> },
    { path: "/3d", label: "3D 空间工作区", icon: <DeploymentUnitOutlined /> },
    { path: "/floor-plans", label: "2D 平面图", icon: <BorderOutlined /> },
  ] },
  { title: "基础设施", entries: [
    { path: "/locations", label: "位置与空间", icon: <ApartmentOutlined /> },
    { path: "/racks", label: "机柜与设备", icon: <NodeIndexOutlined /> },
    { path: "/cables", label: "线缆管理", icon: <LinkOutlined /> },
  ] },
  { title: "光纤工程", entries: [
    { path: "/fiber", label: "光纤熔接", icon: <NodeIndexOutlined /> },
    { path: "/fiber-topology", label: "光纤拓扑", icon: <DeploymentUnitOutlined /> },
  ] },
];

export function AppShell({ children, context, onContextChange, accountControl }: PropsWithChildren<{ accountControl?: ReactNode; context: InfrastructureContext; onContextChange: (value: InfrastructureContext) => void }>) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const screens = Grid.useBreakpoint();
  const mobile = screens.md === false;
  const location = useLocation();
  const getContext = useCallback(() => context, [context]);
  const scope = useApiResource(async signal => {
    if (!context.tenantId) return [];
    const result = await createApiClient({ getContext }).request<LocationRecord[] | PageResult<LocationRecord>>("/locations", { signal });
    return Array.isArray(result) ? result : result.items;
  }, [getContext]);
  const selectedLocation = scope.data?.find(item => item.id === context.locationId);
  const scopeLabel = context.locationId ? selectedLocation?.name ?? "已选工作位置" : "选择工作范围";
  const currentGroup = navigation.find(group => group.entries.some(entry => entry.path === location.pathname));
  const currentPage = currentGroup?.entries.find(entry => entry.path === location.pathname)?.label ?? "工作区";
  useEffect(() => { setMobileOpen(false); }, [location.pathname]);
  const items = navigation.map(group => ({ type: "group" as const, key: group.title, label: group.title, children: group.entries.map(entry => ({
    key: entry.path, icon: entry.icon, title: entry.label,
    label: <Link to={entry.path} aria-current={location.pathname === entry.path ? "page" : undefined}>{entry.label}</Link>,
  })) }));
  const menu = <Menu aria-label="主导航" mode="inline" selectedKeys={[location.pathname]} items={items} onClick={() => setMobileOpen(false)} />;
  const brand = <Link to="/" className="product-mark" aria-label="基础设施管理工作台"><span className="product-symbol"><DeploymentUnitOutlined /></span><span className="product-name"><strong>基础设施管理</strong><small>INFRASTRUCTURE MANAGER</small></span></Link>;
  const footer = <div className="application-nav-footer"><a href="/app/" title="打开旧版工作台"><ArrowLeftOutlined /><span>旧版工作台</span></a><small>空间规划 · 线路运维</small></div>;
  return <ConfigProvider theme={{ token: { colorPrimary: "#35678d", colorText: "#243446", colorTextSecondary: "#617184", colorBorder: "#dce3eb", borderRadius: 8, controlHeight: 36, fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif' } }}>
    <a className="application-skip-link" href="#workspace-main">跳到主要内容</a>
    <Layout className="application-layout">
      <Sider className="application-sidebar" theme="light" collapsed={collapsed} trigger={null} width={224} collapsedWidth={76} breakpoint="lg" onBreakpoint={setCollapsed}>
        {brand}<nav>{menu}</nav>{footer}
      </Sider>
      <Drawer rootClassName="application-mobile-nav" title="基础设施管理" placement="left" size="min(280px, 90vw)" open={mobile && mobileOpen} onClose={() => setMobileOpen(false)} closable={{ "aria-label": "关闭导航" }} destroyOnHidden>{menu}{footer}</Drawer>
      <Layout className="application-main-layout">
        <Header className="application-header">
          <div className="application-header-location"><Tooltip title={mobile ? "打开导航" : collapsed ? "展开导航" : "收起导航"}><Button type="text" aria-label={mobile ? "打开导航" : collapsed ? "展开导航" : "收起导航"} aria-expanded={mobile ? mobileOpen : !collapsed} icon={mobile ? <MenuOutlined /> : collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => mobile ? setMobileOpen(true) : setCollapsed(!collapsed)} /></Tooltip><span className="application-header-section">{currentGroup?.title ?? "工作空间"}</span><span className="application-header-divider" aria-hidden="true">/</span><span className="application-header-page">{currentPage}</span></div>
          <div className="application-header-context"><AimOutlined aria-hidden="true" /><span className="application-scope-caption">工作范围</span><TenantContextEditor allowIdentityEdit={!accountControl} value={context} onChange={onContextChange} locations={scope.data} loading={scope.loading} error={scope.error} onReload={() => void scope.reload()} triggerLabel={scopeLabel} />{accountControl}</div>
        </Header>
        <Content id="workspace-main" tabIndex={-1} className="application-content">{children}</Content>
      </Layout>
    </Layout>
  </ConfigProvider>;
}
