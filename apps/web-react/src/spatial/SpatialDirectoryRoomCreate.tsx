import { PlusOutlined } from "@ant-design/icons";
import { Button } from "antd";
import { useMemo, useState } from "react";
import type { InfrastructureContext } from "../api/context";
import type { LocationRecord } from "../types";
import { SceneCreateDrawer } from "./SceneCreateDrawer";
import type { SpatialPayload } from "./sceneData";

type Props = {
  locations: LocationRecord[];
  parentId?: string;
  getContext: () => InfrastructureContext;
  disabled?: boolean;
  onCreated: (locationId: string) => void;
};

export function SpatialDirectoryRoomCreate({ locations, parentId, getContext, disabled, onCreated }: Props) {
  const [open, setOpen] = useState(false);
  const context = getContext();
  // Directory records supply parent choices only; these empty fields are not scene geometry.
  const payload = useMemo<SpatialPayload>(() => ({
    scope: { tenant_id: context.tenantId, project_id: context.projectId ?? null, location_id: null },
    locations: locations.map(location => ({
      id: location.id,
      parent_id: location.parent_id ?? null,
      identifier: location.identifier,
      name: location.name,
      location_type: location.location_type,
      dimensions: {}, coordinates: {}, transform_3d: {},
    })),
    racks: [], devices: [], ports: [], pathways: [], cables: [], truncated: [],
  }), [locations, context.tenantId, context.projectId]);
  const selectedParent = locations.some(location => location.id === parentId) ? parentId! : "";

  return <>
    <Button icon={<PlusOutlined aria-hidden="true" />} disabled={disabled} onClick={() => setOpen(true)}>创建房间</Button>
    {open && <SceneCreateDrawer kind="room" payload={payload} locationId={selectedParent} getContext={getContext}
      onClose={() => setOpen(false)} onCreated={result => {
        const createdId = result.locationId || result.id;
        if (createdId) onCreated(createdId);
      }} />}
  </>;
}
