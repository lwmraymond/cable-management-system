import { Button, Card, Col, Descriptions, Row, Table, Typography } from "antd";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { InfrastructureContext } from "../api/context";
import { createApiClient } from "../api/client";
import type { PageResult, RackRecord } from "../types";
import { useApiResource } from "../components/useApiResource";
import { AsyncState } from "./AsyncState";

export function RacksPage({ getContext }: { getContext: () => InfrastructureContext }) {
  const api = createApiClient({ getContext });
  const [selected, setSelected] = useState<string>();
  const state = useApiResource(async () => {
    const result = await api.request<RackRecord[] | PageResult<RackRecord>>("/racks");
    return Array.isArray(result) ? result : result.items;
  }, [getContext().tenantId]);
  const elevation = useApiResource(() => selected ? api.request<Record<string, unknown>>(`/racks/${selected}/elevation`) : Promise.resolve({}), [selected]);
  return <><Typography.Title level={2}>Racks and elevation</Typography.Title><Row gutter={16}><Col xs={24} xl={14}><Card><AsyncState loading={state.loading} error={state.error} empty={!state.data?.length}><Table rowKey="id" size="small" dataSource={state.data} onRow={(row) => ({ onClick: () => setSelected(row.id) })} columns={[{ title: "Identifier", render: (_, row: RackRecord) => row.rack_identifier ?? row.identifier }, { title: "Name", dataIndex: "name" }, { title: "Height", dataIndex: "height_u", render: (value) => value ? `${value}U` : "—" }, { title: "", render: (_, row: RackRecord) => <Link to={`/3d?rack=${encodeURIComponent(row.id)}${row.location_id ? `&location=${encodeURIComponent(row.location_id)}` : ""}`}><Button size="small">3D 查看</Button></Link> }]} /></AsyncState></Card></Col><Col xs={24} xl={10}><Card title="Rack elevation API"><AsyncState loading={Boolean(selected) && elevation.loading} error={elevation.error} empty={!selected}><Descriptions column={1} size="small" items={Object.entries(elevation.data ?? {}).slice(0, 12).map(([key, value]) => ({ key, label: key, children: typeof value === "object" ? <pre>{JSON.stringify(value, null, 2)}</pre> : String(value) }))} /></AsyncState></Card></Col></Row></>;
}
