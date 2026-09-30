import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  List,
  Row,
  Select,
  Slider,
  Space,
  Typography,
} from "antd";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import type { InfrastructureContext } from "../api/context";
import { createApiClient } from "../api/client";
import {
  clampPlacement,
  contextKey,
  floorPlanError,
  moveObject,
  newDocument,
  removeObject,
  upsertObject,
  uuid,
  zoomLevel,
  type FloorObjectType,
  type FloorPlan,
  type FloorPlanDocument,
  type FloorPlanObject,
} from "./floorPlanUi";

type Props = { getContext: () => InfrastructureContext };
type Revision = {
  id: string;
  revision_number: number;
  checksum_sha256: string;
  change_summary: string;
  created_at: string;
  restored_from_revision_id: string | null;
};

export function FloorPlanPage({ getContext }: Props) {
  return <FloorPlanWorkbench key={contextKey(getContext())} getContext={getContext} />;
}

function FloorPlanWorkbench({ getContext }: Props) {
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  const context = getContext();
  const alive = useRef(true);
  const inFlight = useRef(false);
  const drag = useRef<{ objectId: string; offsetX: number; offsetY: number }>();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [planId, setPlanId] = useState("");
  const [plan, setPlan] = useState<FloorPlan>();
  const [document, setDocument] = useState<FloorPlanDocument>(newDocument());
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [zoom, setZoom] = useState(1);
  const [selectedId, setSelectedId] = useState<string>();
  const [changeSummary, setChangeSummary] = useState("");
  const [newObjectType, setNewObjectType] = useState<FloorObjectType>("rack");
  const [newObjectId, setNewObjectId] = useState("");
  const [newObjectLabel, setNewObjectLabel] = useState("");
  const [newObjectWidth, setNewObjectWidth] = useState(60);
  const [newObjectHeight, setNewObjectHeight] = useState(100);

  const canRead = permissions.includes("*") || permissions.includes("floor_plan:read");
  const canWrite = permissions.includes("*") || permissions.includes("floor_plan:write");
  const canPublish = permissions.includes("*") || permissions.includes("floor_plan:publish");

  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    api.request<{ principal: { permissions: string[] } }>("/tenants/current")
      .then(result => { if (!cancelled) setPermissions(result.principal.permissions); })
      .catch(error => {
        if (!cancelled) setNotice(floorPlanError(error).message);
      });
    return () => {
      cancelled = true;
      alive.current = false;
    };
  }, [api]);

  async function run(operation: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setNotice("");
    setConflict(false);
    try {
      await operation();
    } catch (error) {
      const parsed = floorPlanError(error);
      if (alive.current) {
        setNotice(parsed.message);
        setConflict(parsed.conflict);
      }
    } finally {
      inFlight.current = false;
      if (alive.current) setBusy(false);
    }
  }

  function adopt(result: FloorPlan) {
    setPlan(result);
    setPlanId(result.id);
    setDocument(result.document);
    setSelectedId(undefined);
  }

  async function loadRevisions(id: string) {
    const result = await api.request<{ items: Revision[] }>(
      `/floor-plans/${uuid(id)}/revisions`,
    );
    if (alive.current) setRevisions(result.items);
  }

  function canvasPoint(event: ReactPointerEvent<SVGSVGElement>) {
    if (!plan) return { x: 0, y: 0 };
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) / zoom,
      y: (event.clientY - rect.top) / zoom,
    };
  }

  function startDrag(
    event: ReactPointerEvent<SVGGElement>,
    object: FloorPlanObject,
  ) {
    if (object.locked) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const svg = event.currentTarget.ownerSVGElement;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    drag.current = {
      objectId: object.id,
      offsetX: (event.clientX - rect.left) / zoom - object.x,
      offsetY: (event.clientY - rect.top) / zoom - object.y,
    };
    setSelectedId(object.id);
  }

  function continueDrag(event: ReactPointerEvent<SVGSVGElement>) {
    if (!plan || !drag.current) return;
    const point = canvasPoint(event);
    setDocument(current => moveObject(
      current,
      drag.current!.objectId,
      point.x - drag.current!.offsetX,
      point.y - drag.current!.offsetY,
      plan.canvas_width,
      plan.canvas_height,
    ));
  }

  const selected = document.objects.find(object => object.id === selectedId);
  const viewWidth = plan?.canvas_width ?? 1000;
  const viewHeight = plan?.canvas_height ?? 800;
  const grid = document.grid_size;

  return <Space orientation="vertical" size="large" style={{ display: "flex" }}>
    <Typography.Title level={2}>2D Floor Plan Editor</Typography.Title>
    <Typography.Paragraph type="secondary">
      每次保存都会创建不可变 revision；写入、发布与恢复均使用 plan version 做乐观并发。
    </Typography.Paragraph>
    {notice && <Alert
      type={conflict ? "warning" : "error"}
      showIcon
      title={notice}
      action={conflict && planId
        ? <Button onClick={() => void run(async () => {
          const result = await api.request<FloorPlan>(`/floor-plans/${uuid(planId)}`);
          if (alive.current) adopt(result);
        })}>重新加载</Button>
        : undefined}
    />}

    <Card title="创建或加载 Floor Plan">
      <Row gutter={[12, 12]}>
        <Col flex="auto">
          <Input
            aria-label="Floor Plan UUID"
            placeholder="Floor Plan UUID"
            value={planId}
            onChange={event => setPlanId(event.target.value)}
          />
        </Col>
        <Col>
          <Button disabled={busy || !canRead} onClick={() => void run(async () => {
            const result = await api.request<FloorPlan>(`/floor-plans/${uuid(planId)}`);
            if (alive.current) {
              adopt(result);
              await loadRevisions(result.id);
            }
          })}>加载</Button>
        </Col>
      </Row>
      <Form
        layout="inline"
        style={{ marginTop: 12 }}
        initialValues={{
          name: "Floor Plan",
          units: "mm",
          canvas_width: 1000,
          canvas_height: 800,
          background_reference: "",
        }}
        onFinish={values => void run(async () => {
          if (!context.projectId || !context.locationId) {
            throw new Error("请先选择 Project 与 Location context。");
          }
          const result = await api.request<FloorPlan>("/floor-plans", {
            method: "POST",
            body: JSON.stringify({
              project_id: uuid(context.projectId),
              location_id: uuid(context.locationId),
              name: values.name,
              units: values.units,
              canvas_width: values.canvas_width,
              canvas_height: values.canvas_height,
              background_reference: values.background_reference || null,
            }),
          });
          if (alive.current) {
            adopt(result);
            await loadRevisions(result.id);
          }
        })}
      >
        <Form.Item name="name" rules={[{ required: true }]}>
          <Input aria-label="Floor Plan name" placeholder="Name" />
        </Form.Item>
        <Form.Item name="units">
          <Select aria-label="Floor Plan units" style={{ width: 90 }} options={[
            { value: "mm" }, { value: "m" }, { value: "ft" },
          ]} />
        </Form.Item>
        <Form.Item name="canvas_width">
          <InputNumber aria-label="Canvas width" min={1} max={1_000_000} />
        </Form.Item>
        <Form.Item name="canvas_height">
          <InputNumber aria-label="Canvas height" min={1} max={1_000_000} />
        </Form.Item>
        <Form.Item name="background_reference">
          <Input aria-label="Background reference" placeholder="Object key / reference" />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" disabled={busy || !canWrite}>
            创建
          </Button>
        </Form.Item>
      </Form>
    </Card>

    {plan && <Row gutter={[16, 16]}>
      <Col xs={24} xl={18}>
        <Card
          title={`${plan.name} · r${plan.current_revision_number} · v${plan.version}`}
          extra={<Space>
            <Typography.Text>Zoom</Typography.Text>
            <Slider
              style={{ width: 140 }}
              min={0.25}
              max={4}
              step={0.25}
              value={zoom}
              onChange={value => setZoom(zoomLevel(value))}
            />
          </Space>}
        >
          <div style={{ overflow: "auto", maxHeight: "72vh", border: "1px solid #d9d9d9" }}>
            <svg
              aria-label="Floor Plan canvas"
              width={viewWidth * zoom}
              height={viewHeight * zoom}
              viewBox={`0 0 ${viewWidth} ${viewHeight}`}
              onPointerMove={continueDrag}
              onPointerUp={() => { drag.current = undefined; }}
              onPointerCancel={() => { drag.current = undefined; }}
              style={{
                display: "block",
                backgroundImage: plan.background_reference
                  ? `url("${plan.background_reference}")`
                  : undefined,
                backgroundSize: "100% 100%",
                touchAction: "none",
              }}
            >
              <defs>
                <pattern
                  id="floor-grid"
                  width={grid}
                  height={grid}
                  patternUnits="userSpaceOnUse"
                >
                  <path
                    d={`M ${grid} 0 L 0 0 0 ${grid}`}
                    fill="none"
                    stroke="currentColor"
                    strokeOpacity="0.12"
                    strokeWidth="1"
                  />
                </pattern>
              </defs>
              <rect width="100%" height="100%" fill="url(#floor-grid)" />
              {document.paths.map(path => <g key={path.id}>
                <polyline
                  points={path.points.map(point => `${point.x},${point.y}`).join(" ")}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={path.width}
                  strokeOpacity="0.55"
                />
                <title>{path.label || path.id}</title>
              </g>)}
              {document.objects.map(object => <g
                key={object.id}
                transform={`translate(${object.x} ${object.y}) rotate(${object.rotation} ${object.width / 2} ${object.height / 2})`}
                onPointerDown={event => startDrag(event, object)}
                style={{ cursor: object.locked ? "not-allowed" : "move" }}
              >
                <rect
                  width={object.width}
                  height={object.height}
                  rx="4"
                  fill={object.id === selectedId ? "rgba(22,119,255,0.24)" : "rgba(0,0,0,0.08)"}
                  stroke={object.id === selectedId ? "#1677ff" : "currentColor"}
                  strokeWidth={object.id === selectedId ? 3 : 1}
                />
                <text x="6" y="18" fontSize="12">
                  {object.label || `${object.object_type}:${object.object_id.slice(0, 8)}`}
                </text>
              </g>)}
            </svg>
          </div>
        </Card>
      </Col>
      <Col xs={24} xl={6}>
        <Space orientation="vertical" size="middle" style={{ display: "flex" }}>
          <Card title="放置资源">
            <Space orientation="vertical" style={{ display: "flex" }}>
              <Select
                aria-label="Object type"
                value={newObjectType}
                options={["location", "rack", "device", "pathway"].map(value => ({ value }))}
                onChange={setNewObjectType}
              />
              <Input
                aria-label="Physical resource UUID"
                placeholder="Resource UUID"
                value={newObjectId}
                onChange={event => setNewObjectId(event.target.value)}
              />
              <Input
                aria-label="Object label"
                placeholder="Label"
                value={newObjectLabel}
                onChange={event => setNewObjectLabel(event.target.value)}
              />
              <Space>
                <InputNumber
                  aria-label="Object width"
                  min={1}
                  value={newObjectWidth}
                  onChange={value => setNewObjectWidth(value ?? 60)}
                />
                <InputNumber
                  aria-label="Object height"
                  min={1}
                  value={newObjectHeight}
                  onChange={value => setNewObjectHeight(value ?? 100)}
                />
              </Space>
              <Button disabled={busy || !canWrite} onClick={() => {
                const physicalId = uuid(newObjectId);
                const next: FloorPlanObject = {
                  id: `${newObjectType}-${physicalId}`,
                  object_type: newObjectType,
                  object_id: physicalId,
                  x: grid,
                  y: grid,
                  width: newObjectWidth,
                  height: newObjectHeight,
                  rotation: 0,
                  z_index: document.objects.length,
                  locked: false,
                  label: newObjectLabel.trim() || null,
                };
                setDocument(current => upsertObject(
                  current,
                  next,
                  plan.canvas_width,
                  plan.canvas_height,
                ));
                setSelectedId(next.id);
              }}>加入画布</Button>
            </Space>
          </Card>

          <Card title="选中对象">
            {selected
              ? <Space orientation="vertical" style={{ display: "flex" }}>
                <Typography.Text code>{selected.id}</Typography.Text>
                <InputNumber
                  aria-label="Selected X"
                  value={selected.x}
                  onChange={value => setDocument(current => moveObject(
                    current,
                    selected.id,
                    value ?? selected.x,
                    selected.y,
                    plan.canvas_width,
                    plan.canvas_height,
                  ))}
                />
                <InputNumber
                  aria-label="Selected Y"
                  value={selected.y}
                  onChange={value => setDocument(current => moveObject(
                    current,
                    selected.id,
                    selected.x,
                    value ?? selected.y,
                    plan.canvas_width,
                    plan.canvas_height,
                  ))}
                />
                <Button
                  danger
                  disabled={selected.locked}
                  onClick={() => {
                    setDocument(current => removeObject(current, selected.id));
                    setSelectedId(undefined);
                  }}
                >移除</Button>
              </Space>
              : <Typography.Text type="secondary">点击画布对象以选择。</Typography.Text>}
          </Card>

          <Card title="Revision 操作">
            <Space orientation="vertical" style={{ display: "flex" }}>
              <Input
                aria-label="Change summary"
                placeholder="Change summary"
                value={changeSummary}
                onChange={event => setChangeSummary(event.target.value)}
              />
              <Button type="primary" disabled={busy || !canWrite} onClick={() => void run(async () => {
                const result = await api.request<FloorPlan>(
                  `/floor-plans/${uuid(plan.id)}/draft`,
                  {
                    method: "PUT",
                    body: JSON.stringify({
                      expected_version: plan.version,
                      document,
                      change_summary: changeSummary,
                    }),
                  },
                );
                if (alive.current) {
                  adopt(result);
                  await loadRevisions(result.id);
                }
              })}>保存新 Revision</Button>
              <Button disabled={busy || !canPublish} onClick={() => void run(async () => {
                const result = await api.request<FloorPlan>(
                  `/floor-plans/${uuid(plan.id)}/publish`,
                  {
                    method: "POST",
                    body: JSON.stringify({ expected_version: plan.version }),
                  },
                );
                if (alive.current) {
                  adopt(result);
                  await loadRevisions(result.id);
                }
              })}>发布当前 Revision</Button>
              <Typography.Text type="secondary">
                Published: {plan.published_revision_number ?? "—"}
              </Typography.Text>
            </Space>
          </Card>

          <Card title="历史 Revision">
            <List
              size="small"
              dataSource={revisions}
              renderItem={revision => <List.Item
                actions={[<Button
                  key="restore"
                  size="small"
                  disabled={busy || !canWrite}
                  onClick={() => void run(async () => {
                    const result = await api.request<FloorPlan>(
                      `/floor-plans/${uuid(plan.id)}/revisions/${uuid(revision.id)}/restore`,
                      {
                        method: "POST",
                        body: JSON.stringify({
                          expected_version: plan.version,
                          change_summary: `Restore r${revision.revision_number}`,
                        }),
                      },
                    );
                    if (alive.current) {
                      adopt(result);
                      await loadRevisions(result.id);
                    }
                  })}
                >恢复</Button>]}
              >
                <List.Item.Meta
                  title={`r${revision.revision_number}`}
                  description={`${revision.change_summary || "No summary"} · ${revision.checksum_sha256.slice(0, 12)}`}
                />
              </List.Item>}
            />
          </Card>
        </Space>
      </Col>
    </Row>}
  </Space>;
}
