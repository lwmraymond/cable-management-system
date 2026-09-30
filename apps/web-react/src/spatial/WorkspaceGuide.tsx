import { ArrowRightOutlined, BookOutlined, SearchOutlined } from "@ant-design/icons";
import { Button, Checkbox, Drawer, Input, Progress } from "antd";
import { useState } from "react";
import "./workspaceGuide.css";

export type GuideAction = "room" | "rack" | "device" | "pathway" | "copper" | "fiber" | "measure" | "inventory" | "checks";
export type WorkspaceGuideProps = {
  open: boolean;
  onClose: () => void;
  onAction: (action: GuideAction) => void;
  disabled?: boolean;
  storageKey: string;
};
type GuideStep = { id: string; title: string; mode: string; keywords: string; instructions: string[]; actions: { action: GuideAction; label: string }[] };
const steps: GuideStep[] = [
  { id: "scope", title: "选择项目与空间范围", mode: "观察", keywords: "权限 搜索 查找 筛选", instructions: ["先从建筑目录选择楼层或房间，再点击“进入 3D”。建筑本身不合并展示所有楼层；场景顶部可切换楼层或房间。“对象列表”的全部房间只指本次进入的楼层或房间范围。", "点击对象查看详情，双击列表中的机柜进入机柜视图。同一楼层的多房间并排是示意布局，不代表房间之间的实测距离。"], actions: [{ action: "inventory", label: "打开对象列表" }] },
  { id: "room", title: "建立房间与出入口", mode: "保存后生效", keywords: "Server Room 入口 门 宽 深 高 空间", instructions: ["创建房间，填写编号、名称、类型及宽、深、高，确认后点击“保存房间”。房间尺寸单位为米。", "新房间默认在南墙居中创建入口。保存后选中房间，在对象详情中“配置出入口”，调整墙面、边距、净宽与净高，再“保存出入口”。"], actions: [{ action: "room", label: "创建房间" }] },
  { id: "rack", title: "批量布置机柜", mode: "保存后生效", keywords: "42U 网格 数量 列数 旋转 间距 移动", instructions: ["选择机柜工具，在房间地面点击落点；也可通过“填写参数”设置位置。填写数量、列数、间距、旋转和柜体尺寸。", "批量机柜编号会添加 -01、-02 等序号。坐标以机柜中心为准，单位米；宽、深为毫米，高度为 U。保存前检查边界与占位；已有机柜可在详情中“调整位置”。"], actions: [{ action: "rack", label: "放置机柜" }] },
  { id: "device", title: "安装设备并确认介质策略", mode: "保存后生效", keywords: "交换机 服务器 铜配线架 光纤配线架 模板 U位 U 位 端口 布线属性", instructions: ["选择设备工具后点击目标机柜，或在安装表单选择模板、机柜、起始 U 位及前后安装面。可点击“填写建议编号”和“采用空闲 U 位”辅助填表；核对后“保存设备”，端口随模板生成。", "选中设备或线槽，可在“布线属性”设置是否允许线缆及允许的铜缆 / 光纤介质，点击“保存布线属性”才生效。已占用或不符合策略的接口不可用于新连接。"], actions: [{ action: "device", label: "安装设备" }] },
  { id: "connect", title: "选择 A/B 端并确认走线", mode: "预览 → 确认保存", keywords: "铜缆 光纤 Cat6 Cat6A OS2 OM4 预留长度 推荐 路由 端口 直连", instructions: ["选择铜缆或光纤，点击机柜 / 设备，依次选择兼容且空闲的 A、B 端口。也可拖接端口，或在接线工作区搜索端口。", "双端有效后自动推荐走线，核对每段“使用 / 全段”长度及中途接入、离开位置。可换方案、排除线槽，或增删并排序路径段；手动修改后需“校验当前走线”。没有记录线槽的直连会明确提示。", "填写线缆编号与预留长度，核对“将保存的登记长度”。登记长度 = 当前路线估算 + 预留；确认后点击“确认连接并保存”。端口选择和路线预览不会创建线缆。"], actions: [{ action: "copper", label: "连接铜缆" }, { action: "fiber", label: "连接光纤" }] },
  { id: "measure", title: "测距并转为线槽", mode: "临时测量 → 保存线槽", keywords: "折线 XYZ 坐标 高度 测量 撤回 清空 Esc", instructions: ["选择测距工具，在同一房间连续点击地面或物件表面，最多 64 点。面板显示折线总长、水平投影距离与累计高差；不足两点时尚未形成测量。", "可撤回、清空；“完成测距”保留临时结果，“继续取点”接着测量。切换其他工具或 Esc 会清除临时测距，跨房间不能直接测量。", "点击“用于创建线槽”把折线带入表单。逐点核对房间本地 X / Y 与高度 Z，再“保存线槽”；也可直接绘制线槽。转换表单或取消均不会保存线槽。"], actions: [{ action: "measure", label: "开始测距" }, { action: "pathway", label: "直接绘制线槽" }] },
  { id: "inspect", title: "查线并核对长度来源", mode: "观察与检查", keywords: "运维 线路 检查 资料 完整性 登记 坐标 路径段 测试 删除 拆除 归档", instructions: ["在对象列表搜索线缆或从设备的关联线缆进入，查看两端、路径段及长度明细。端口连接示意不表示已经记录实际走线路径。", "分别核对登记线缆长度、路径段登记长度与坐标长度；登记值可能包含预留，均不自动等于现场实测。部分端点或路径段未加载时，先确认当前范围。", "“检查当前场景”核对已加载数据中的入口、摆位、长度 / 路由资料及既有线缆策略冲突。按问题入口查看或修复；检查结果不代表全网健康，也不表示所有问题可自动修复。", "维护已保存线缆：在详情点击“删除规划线缆”或“拆除线缆”，核对端点、路径及依赖。拆除需要填写原因，确认后释放端口并保留历史；没有一键恢复。无权、有未完成工单或已登记纤芯／线对拓扑时，先处理阻碍。取消接线草稿与清空临时测距不会删除已保存线缆。"], actions: [{ action: "checks", label: "检查当前场景" }, { action: "inventory", label: "查找线缆" }] },
];
const shortcuts = [
  ["拖动空白处", "旋转视角；平移工具下左键拖动为平移"],
  ["右键拖动", "平移视角"],
  ["滚轮 / + / −", "缩放视角"],
  ["W / A / S / D", "水平移动视角"],
  ["空格 / Shift", "按住上升 / 下降，松开停止"],
  ["F", "适配全部对象"],
  ["1 / 2 / 3", "正面 / 背面 / 俯视"],
  ["Esc", "退出当前工具并清除临时测距；在教程内仅关闭教程"],
];
const discoveryText = "Observium 尚未接入。未来发现设备或逻辑邻接，不等于已经确认物理端口对接、跳线和线槽经过顺序。规划与运维记录仍需结合现场核验后保存。";
const stepIds = new Set(steps.map(step => step.id));
function readProgress(key: string): { ids: string[]; unavailable: boolean } {
  let raw: string | null;
  try { raw = window.localStorage.getItem(key); }
  catch { return { ids: [], unavailable: true }; }
  try {
    const value: unknown = raw && raw.length <= 4096 ? JSON.parse(raw) : [];
    return { ids: Array.isArray(value) ? [...new Set(value.filter((id): id is string => typeof id === "string" && stepIds.has(id)))] : [], unavailable: false };
  } catch { return { ids: [], unavailable: false }; }
}

export function WorkspaceGuide({ open, onClose, onAction, disabled = false, storageKey }: WorkspaceGuideProps) {
  return <div className="workspace-guide-boundary" onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); event.preventDefault(); if (open) onClose(); } }}>
    <Drawer rootClassName="workspace-guide" title={<span><BookOutlined aria-hidden="true" /> 3D 工作区教程</span>} open={open} onClose={onClose} closable={{ "aria-label": "关闭教程" }} size="min(640px, 100vw)" destroyOnHidden>
      <GuideContent key={storageKey} storageKey={storageKey} onAction={onAction} disabled={disabled} />
    </Drawer>
  </div>;
}

function GuideContent({ storageKey, onAction, disabled }: Pick<WorkspaceGuideProps, "storageKey" | "onAction" | "disabled">) {
  const [query, setQuery] = useState("");
  const [progress, setProgress] = useState(() => readProgress(storageKey));
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const matches = (text: string) => words.every(word => text.toLocaleLowerCase().includes(word));
  const visibleSteps = steps.filter(step => matches([step.title, step.keywords, ...step.instructions, ...step.actions.map(action => action.label)].join(" ")));
  const showShortcuts = matches(`快捷键 视角 键盘 ${shortcuts.flat().join(" ")}`);
  const showDiscovery = matches(`规划 运维 发现数据 ${discoveryText}`);
  const markRead = (id: string) => {
    const ids = progress.ids.includes(id) ? progress.ids.filter(value => value !== id) : steps.filter(step => progress.ids.includes(step.id) || step.id === id).map(step => step.id);
    let unavailable = false;
    try { window.localStorage.setItem(storageKey, JSON.stringify(ids)); } catch { unavailable = true; }
    setProgress({ ids, unavailable });
  };
  return <div className="workspace-guide-content">
    <p className="workspace-guide-intro">用于空间规划和运维查线。观察、筛选、路线预览与临时测距不会写入工程记录；创建或编辑需要点击对应的保存按钮。</p>
    <div className="workspace-guide-controls">
      <Input aria-label="搜索教程" placeholder="搜索端口、预留长度、U 位或快捷键" prefix={<SearchOutlined aria-hidden="true" />} value={query} onChange={event => setQuery(event.target.value)} allowClear />
      <div className="workspace-guide-progress"><span>阅读进度：已读 {progress.ids.length} / 7 步</span><Progress percent={Math.round(progress.ids.length / 7 * 100)} showInfo={false} strokeColor="#5483a5" size="small" /></div>
      <p>仅记录本浏览器的阅读状态，不代表实际工程完成度。</p>
      {progress.unavailable && <p role="status">浏览器暂时无法保存阅读进度，本次阅读仍可继续。</p>}
    </div>
    {visibleSteps.length > 0 && <section aria-label="7 步快速上手" className="workspace-guide-steps"><h2>7 步快速上手{words.length > 0 && <small> · 匹配 {visibleSteps.length} 步</small>}</h2>
      {visibleSteps.map(step => <article key={step.id} className="workspace-guide-step" aria-label={step.title}>
        <div className="workspace-guide-step-heading"><span aria-hidden="true">{steps.indexOf(step) + 1}</span><div><h3>{step.title}</h3><small>{step.mode}</small></div></div>
        <ol>{step.instructions.map(instruction => <li key={instruction}>{instruction}</li>)}</ol>
        <div className="workspace-guide-step-actions">{step.actions.map(action => <Button key={action.action} aria-label={action.label} disabled={disabled} onClick={() => onAction(action.action)} icon={<ArrowRightOutlined aria-hidden="true" />}>{action.label}</Button>)}</div>
        <Checkbox checked={progress.ids.includes(step.id)} onChange={() => markRead(step.id)} aria-label={`标记已读：${step.title}`}>标记已读</Checkbox>
      </article>)}
    </section>}
    {showShortcuts && <section className="workspace-guide-reference" aria-label="快捷键"><h2>快捷键</h2><p>先点击画布再使用快捷键；输入框中的按键不会移动视角。</p><dl>{shortcuts.map(([key, description]) => <div key={key}><dt><kbd>{key}</kbd></dt><dd>{description}</dd></div>)}</dl></section>}
    {showDiscovery && <section className="workspace-guide-reference" aria-label="未来发现数据"><h2>规划记录与未来发现数据</h2><p>{discoveryText}</p></section>}
    {!visibleSteps.length && !showShortcuts && !showDiscovery && <p className="workspace-guide-empty" role="status">没有匹配的教程。试试“端口”“U 位”或“测距”。</p>}
  </div>;
}
