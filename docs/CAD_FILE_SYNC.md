# 3D CAD 文件同步 v1

本功能延续 `codex/local-build` 的 React / Three.js 空间工作区，入口 `/app-next/3d`，左侧 **CAD 文件同步**。不需要付费 CAD 组件，不使用旧分支的独立 2D 编辑器。文件同步采用人工审阅流程，不是实时 CAD 插件。

## 使用

1. 在现有界面新建房间、机柜、设备/端口模板、线槽和线缆，或选择已有单个房间。
2. 打开 CAD 文件同步，选择房间，导出 DXF 或 IFC；可另下载映射说明。
3. 在支持该 profile 的工具里改原生几何，保留稳定 ID 和基线属性。DXF 机柜用 INSERT 位置/朝向，IFC 用 ObjectPlacement；不要改名称来匹配对象。
4. 选择修改后的文件。导入仅保存不可变原件和差异，不会直接改系统。
5. 核对可更新项及其基线、当前值、文件值，再按 **确认应用**。有冲突需重新导出或在系统中处理，不能强制覆盖。
6. 关闭/按 Esc 不应用草稿；已应用修订再次导入/提交不会重复创建对象。

## 精确支持范围

| 项目 | 导出 | 从 CAD 回写 |
| --- | --- | --- |
| 房间 | 边界代理盒、尺寸、单房间原点 | 不回写尺寸/原点 |
| 机柜 | 真实登记位置、旋转、宽深高代理盒 | XYZ 位置和朝向；复用系统权限、版本、碰撞和房间边界校验 |
| 设备 | 机柜内派生代理盒 | 不回写型号、尺寸、U 位、安装面或身份 |
| 端口 | 按系统布局计算的示意锚点及稳定身份 | 派生坐标只作参考；不改身份、归属、占用或接线 |
| 线槽段 | 有序三维折线、登记长度 | 仅未被任何线缆路由引用的折线顶点；保留登记长度 |
| 线缆 | 已登记路径/局部路线、A/B 端口；无路由时明确为示意线 | 不回写路径、端接、物理身份或登记长度 |
| IFC 连接关系 | 端口归属、线缆端口连接、配线架前后映射 | 修改、增删、重接或换归属均阻止整批应用 |
| 未映射外部几何 | 原文件保留，计入参考项 | 不自动建立设备、端口或线缆；当前没有人工映射编辑器 |

部分导出/漏对象、重复 UUID、未知或跨快照 ID、改基线版本、跨租户/工作区映射、代理形状变化、镜像/缩放/倾斜、不支持的实体、闭合 DXF 路径、过深嵌套等会阻止应用。没有默删或按文件名/坐标猜测身份。机柜成组移动沿现有逐柜语义验证，有中间碰撞的交换位置会在预览标明不支持；需分阶段移动。

移动机柜后，设备和示意端口由系统重新计算；不会自动改真实线缆端接、重铺线缆或把图示长度冒充现场实测长度。引用线槽的路由含有方向、partial-route offsets、geometry hash；v1 明确拒绝破坏这些依赖的回写。

## 公开交换 profile：CMS-CAD-1

- 权威坐标为**单房间局部米、Z 向上**；渲染层的 Three.js Y-up 不是交换坐标。不同房间没有测绘全局坐标，不叠加到同一原点。
- 系统旋转采用 `x'=x+dx*cosθ+dy*sinθ, y'=y-dx*sinθ+dy*cosθ`，故 DXF/IFC 绕 +Z 的标准角为 **负系统 yaw**。0/360 等价。
- DXF 为 UTF-8 ASCII R2013，`$INSUNITS=6`。代理盒为 BLOCK + INSERT + 12 LINE，线槽/线缆为保留 XYZ 的开放 3D POLYLINE，端口为 POINT；不是平面投影。注册 XDATA `CMS_CAD` 承载 app UUID、kind、snapshot UUID、version、登记长度；隐藏块属性协助辨识，handle/名称不作身份依据。可解析明确 m/mm/cm/inch/foot 单位和有界 Z-up 嵌套 INSERT。
- IFC 为 IFC4 Add2 TC1 profile：IfcSpace、IfcCableCarrierSegment、IfcCableSegment、IfcDistributionPort；机柜/设备为明确标注的 IfcBuildingElementProxy。GlobalId 由 app UUID 稳定映射，CMS_Exchange/CMS_Object 属性关联服务器快照。几何从真实 Placement/Representation 读取，不从属性 JSON 倒推。支持有界 LocalPlacement 嵌套和显式长度单位。不是完整 BIM 作者工具。
- 只有 geometry 差异可进入受支持回写。未映射参考不会渲染为新业务对象；导出没有端口测绘精度承诺，跨范围线缆有明确遗漏说明。
- DWG 能力检查始终返回不可用，界面按钮禁用。本版本不安装/调用 ODA、RealDWG、BricsCAD 或 LibreDWG，不重命名 DXF 冒充 DWG。

## 数据、事务和运行

可选依赖：在独立 Python 3.12+ 环境运行 `python -m pip install --index-url https://pypi.org/simple -e '.[dev,cad]'`。固定 `ezdxf==1.4.4`、`ifcopenshell==0.9.0`；许可见 [THIRD_PARTY_CAD.md](THIRD_PARTY_CAD.md)。仅验证了 macOS ARM64 / Python 3.12，本机格式工作进程需要 `/bin/ps` 读取自己的 RSS；无法监测则失败，不放宽保护。

迁移 `200000000013` 增加三个租户表：导出快照、导入修订、应用回执。PostgreSQL FORCE RLS 及不可变触发器；ORM 同样拒绝历史修改/删除。**迁移只进不退**：回退需恢复升级前备份，不能按受 RLS 过滤的空记录结果删除所有租户历史。不要直接对原数据库运行演示启动脚本/seed。

Apply 只接收已暂存 revision ID 和预览 token，不接受客户端替代值。锁内重验权限、版本和几何；线槽取与 ConnectivityService 相同的有序 Pathway 锁，再检查所有路由引用。业务变更、审计和唯一 domain receipt 同一事务。SQLite 多连接已验证重复 Apply 唯一提交；没有用 SQLite 替代 PostgreSQL 并发/RLS 证明。

文件上限 8 MiB；上传流在 multipart 完整落盘前也有 8 MiB + 64 KiB 包装上限。解析在数据库写锁之前执行。原生导入和格式生成均在独立进程，12 秒 CPU、20 秒墙钟、1 GiB RSS 监控、32 MiB 输出限制；20,000 对象、每条路径 512 点、16 层嵌套、导出累计 100,000 顶点。工作进程不继承数据库/认证环境；不加载外部 XREF、图片、URL 或用户提供的本机路径。格式转换不持有数据库写锁；转换后取短事务锁、重新检查授权与完整场景摘要。有对象变化便拒绝保存该导出，要求从新快照重试。

## 验证与复现

```sh
PYTHONPATH=apps/api:. pytest -q
cd apps/web-react
npm test -- --maxWorkers=1
npm run typecheck
npm run build
```

实际浏览器需要独立环境安装 `tests/e2e/requirements.txt`，并从 Playwright 官方源安装对应 Chromium；或设置 `PLAYWRIGHT_BROWSERS_PATH` 指向已安装的对应版本。

```sh
python tests/e2e/run_cad_acceptance.py --evidence /absolute/synthetic-evidence
```

脚本创建临时合成 SQLite 和独立 Chromium profile，仅监听随机 loopback 端口，不接触原库或手动预览。通过界面创建资源、连接、导出/上传/确认；辅助只读检查真实 camera pose 和网络响应。CAD 编辑只改原生 INSERT/ObjectPlacement，保留身份属性。截图、下载文件、差异、失败原因写到证据目录。外部 CAD GUI 往返和独立第二解析器仍未执行；IfcOpenShell schema/EXPRESS 校验不等于第二个解析器或外部 CAD GUI 认证。真实 PostgreSQL RLS 运行依赖显式测试 DSN，未配置时明确跳过。
