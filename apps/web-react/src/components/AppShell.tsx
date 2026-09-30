import { ApartmentOutlined, BorderOutlined, DashboardOutlined, LinkOutlined, MenuFoldOutlined, MenuUnfoldOutlined, NodeIndexOutlined } from "@ant-design/icons";
import { Button, Layout, Menu, Space, Typography } from "antd";
import { useState, type PropsWithChildren } from "react";
import { Link, useLocation } from "react-router-dom";
import type { InfrastructureContext } from "../api/context";
import { TenantContextEditor } from "./TenantContextEditor";

const { Header, Sider, Content } = Layout;
const items = [
  { key: "/fiber", icon: <NodeIndexOutlined />, label: <Link to="/fiber">Fiber Splice</Link> },
  { key: "/fiber-topology", icon: <LinkOutlined />, label: <Link to="/fiber-topology">Fiber Topology</Link> },
  { key: "/floor-plans", icon: <BorderOutlined />, label: <Link to="/floor-plans">2D Floor Plan</Link> },
  { key: "/", icon: <DashboardOutlined />, label: <Link to="/">Dashboard</Link> },
  { key: "/locations", icon: <ApartmentOutlined />, label: <Link to="/locations">Locations</Link> },
  { key: "/racks", icon: <NodeIndexOutlined />, label: <Link to="/racks">Racks</Link> },
  { key: "/cables", icon: <LinkOutlined />, label: <Link to="/cables">Cables</Link> },
];

export function AppShell({ children, context, onContextChange }: PropsWithChildren<{ context: InfrastructureContext; onContextChange: (value: InfrastructureContext) => void }>) {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();
  return <Layout className="application-layout">
    <Sider collapsible collapsed={collapsed} trigger={null} width={236}>
      <div className="product-mark">{collapsed ? "SIM" : "Structured Infrastructure"}</div>
      <Menu theme="dark" mode="inline" selectedKeys={[location.pathname]} items={items} />
    </Sider>
    <Layout>
      <Header className="application-header">
        <Space><Button type="text" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed(!collapsed)} /><Typography.Text strong>Physical Infrastructure Source of Truth</Typography.Text></Space>
        <Space><Typography.Text type="secondary">{context.tenantId ? `Tenant ${context.tenantId.slice(0, 8)}` : "Tenant not selected"}</Typography.Text><TenantContextEditor value={context} onChange={onContextChange} /><Button href="/app/">Legacy WebGL</Button></Space>
      </Header>
      <Content className="application-content">{children}</Content>
    </Layout>
  </Layout>;
}
