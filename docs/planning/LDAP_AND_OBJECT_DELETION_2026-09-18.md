# 2026-09-18 实施计划：LDAP 认证与对象删除

状态：部分实施。已补齐线缆删除／拆除、影响预览、端口释放与版本／依赖保护，说明见 [线缆维护](../CABLE_MAINTENANCE.md)。真实 LDAP 联调及房间、机柜、设备、线槽删除仍待实施。下文保留原计划作为范围与验收依据，其中“当前不存在”等描述属于制定计划时的基线。

仓库：`/Users/lauraymond/Documents/SOC Platform/cable-management-system`

用户目标：使用目录账号登录个人／共享工作空间；在 3D 场景中能够找到删除入口并正确删除线缆等对象。

## 1. 当前基线与已确认问题

- 已具备个人／共享 workspace、成员角色、服务端 OIDC Code + PKCE、JWT Cookie、CSRF 与 `/app-next/auth/entry` 转入接口；真实身份提供方尚未联调。
- `docker-compose.yml` 和 `infra/keycloak/sim-realm.json` 已有 Keycloak，但导入配置仍包含旧 SPA 回调，尚未对齐 `/api/v1/auth/callback`；`scripts/local-dev.py` 当前强制 demo 身份。
- `SpatialInspector.tsx` 有对象详情与编辑入口，没有删除菜单；`api/scene_editor.py` 主要提供 POST/PATCH，尚无场景对象删除接口。
- `CableStatus.REMOVED`、`TenantOwnedMixin.deleted_at` 已存在，但现有 cable workflow 只有安装、测试、审批投用，没有完整拆除／删除流程。
- 测距面板已有“撤回一点／清空”，连线草稿已有“取消”；它们与数据库中保存的 Cable 不是同一种删除。
- 最近完整验证基线：后端 333 passed、1 skipped（真实 PostgreSQL RLS 环境未配置）；前端 285 passed；build 通过。

## 2. 当日实施顺序

| 顺序 | 工作 | 当日验收目标 |
| --- | --- | --- |
| 1 / P0 | LDAP → 统一身份平台 → 现有 OIDC | 真实目录账号登录并进入获授权空间；无目录环境时只完成可运行配置与测试，并明确保留真实联调项 |
| 2 / P0 | 已保存线缆的删除／拆除完整链路 | 3D 中能找到入口，确认后消失、端口释放、刷新不重现；关联历史和审计保留 |
| 3 / P1 | 设备、机柜、房间与线槽删除 | 空对象可删；存在依赖时明确解释并引导处理，不默认递归删除 |
| 4 | 浏览器回归、教程与交接 | 更新操作教程，验证账号隔离、失败恢复、手机布局与原场景功能 |

认证的外部连接等待期间可并行实施删除服务。P0 优先；复杂光纤拓扑级联删除、整房间批量清空、通用回收站恢复不纳入当日必须交付范围。

## 3. LDAP 接入方案

采用 `AD / OpenLDAP → Keycloak LDAP Federation → OIDC → 本系统`。这与现有外部平台 SSO 转入接口共用账号和会话。目录负责认证，本系统继续控制 workspace 成员、角色与项目／位置范围。

目录类型尚未确认，配置设计预留 AD 和 OpenLDAP，先以实际提供的目录做端到端验收。

### 工作项

1. 确认 LDAP 地址、目录类型、Users/Base DN、用户名属性、允许登录的用户／组范围、只读查询 Bind DN 和 CA 信任链。Bind 密码通过本地秘密配置或密钥管理传入，不写入仓库、计划或日志。
2. 使用验证证书的 LDAPS 或明确开启 StartTLS；Keycloak 目录访问设为只读，先限制小范围测试用户，避免全目录导入。
3. 更新 `sim-web` 为精确的服务端 callback URI；核对浏览器与 API 使用的 issuer、API audience、client 与公开域名。先沿用本机 API + loopback Keycloak 的开发结构，不重建现有数据环境。
4. 增加明确的本地 `demo / sso` 启动配置选择，保持现有 demo 可启动；SSO 模式不回退到 `X-Actor-ID`。
5. 提供受控的账号建档／subject 关联步骤（优先可重复执行的管理脚本），把真实 OIDC `sub` 关联 `UserIdentity`。不使用可改名的 LDAP DN 或邮箱直接替代稳定 subject；不自动按相同邮箱合并账号。
6. 首批账号由管理员明确分配个人／共享空间及权限。LDAP 登录成功不等于自动取得所有共享空间权限；目录组到 workspace 角色的自动映射列为后续增强。
7. 对齐登录失败、尚未关联账号、网络不可达及证书错误的页面提示；保留当前页面返回目标。

### 必须实测

- 正确密码可登录；错误密码、目录中停用账号、未建档账号被拒绝。
- 首次登录、已有统一登录会话从外部导航进入、退出后重新登录、过期后重新认证。
- 两个账号互不读取个人空间；共享空间按 viewer/editor 授权。
- TLS 验证失败和目录超时不会回退到 demo，也不会显示成功状态。
- 验证目录停用后的新登录阻断。已签发 JWT 的失效时间另行记录，不能把“目录账号停用”宣传为现有浏览器会话即时撤销；首版设置较短 access-token 有效期，后续再补目录状态同步／统一会话撤销。

相关现有文件：`infra/keycloak/sim-realm.json`、`docker-compose.yml`、`.env.example`、`scripts/local-dev.py`、`apps/api/app/api/browser_auth.py`、`apps/api/app/api/deps.py`、`apps/web-react/src/pages/LoginPage.tsx`。

## 4. 删除功能的用户交互

### 入口

- 选中对象后，在右侧“对象详情”增加“更多操作 → 删除／拆除”。线缆提供明显的删除入口，避免只依赖快捷键。
- 线缆列表的行操作提供相同入口；后续把同一动作接到设备／机柜／位置列表。
- 场景短右键可以打开上下文菜单，但不能破坏已有右键拖动摄像机行为；若冲突，优先交付详情面板与列表菜单。
- `Delete` 快捷键仅在场景拥有焦点且对象可操作时打开确认框，不直接执行；文本输入、中文输入法、弹窗与表单中不触发。Mac Backspace 是否映射需明确限制在画布焦点内。

### 不同对象的行为

| 对象／状态 | 用户动作与规则 |
| --- | --- |
| 未保存连线草稿 | “取消连线”，只清除当前草稿，不调用持久化删除 |
| 临时测距线 | “撤回一点／清空测距”，沿用已有操作，教程解释区别 |
| 规划线缆 | “删除线缆”，确认编号、A/B 端点与关联影响后软删除 |
| 已安装／测试／投用线缆 | “拆除并归档”，写入 REMOVED，释放活动连接；保存安装、测试与审计历史 |
| 带熔接、通道、breakout 等依赖的光缆 | 首版列出可见阻碍，要求先解除关联，不自动级联摧毁光纤拓扑 |
| 线槽／线槽段 | 无活动线缆路由使用时可删；否则展示需要改道的线缆，先改道或拆除 |
| 设备 | 无活动端口连接、熔接盒／拓扑依赖时可删；否则先解除相关连接 |
| 机柜 | 清空／移走设备并解除需要保留的平面图绑定后可删 |
| 房间 | 无子房间、机柜、设备、线槽或其他活动引用时可删；保留历史版本与审计 |

确认框显示对象类型、名称／编号、将释放的端口和影响。不可删除时显示原因和可操作链接，不只用灰色按钮。失败时保留选中对象与场景，不伪装为已删除；成功后重新读取服务端场景、容量、占用和统计。

首版删除与拆除不可一键恢复；使用确认、历史与审计保障可追溯性。后续回收站／撤销恢复必须重新检查端口和名称冲突后才能上线。

## 5. 删除服务与数据一致性

拟新增独立 `services/object_deletion.py` 和薄 API 路由，避免把全部逻辑堆进 SpatialPage 或 main.py。最终路径在实施时与既有 REST 资源统一；下列为拟定合约，当前尚不存在：

- `POST /api/v1/scene/deletion-preview`：对象类型、ID；返回可执行动作、版本、影响和阻碍。
- `DELETE /api/v1/cables/{id}`：允许删除的规划对象，带版本前置条件。
- `POST /api/v1/cables/{id}/remove`：拆除已安装对象，要求原因与版本前置条件。
- 其他对象采用对应资源 DELETE 路由，复用同一依赖检查服务。

执行删除必须在一个事务内重新校验权限、对象版本、依赖和端口占用；预览结果不是执行授权。避免“预览后新增关联”被同时删除。重复提交行为明确、不会重复释放别人新占用的端口，也不会重复创建审计事件。

### 已发现的关键陷阱

1. `CableTermination` 的 `uq_physical_port_termination` 仍是全表唯一约束；只写 `deleted_at` 会导致端口看似空闲、重新接线却因唯一约束失败。计划用迁移将其改为活动记录唯一索引，同时保留其他租户／端接约束，分别验证 SQLite 和 PostgreSQL。
2. `PhysicalPortClaim` 已有 `deleted_at IS NULL` 的唯一索引。删除／拆除时，CableTermination、PhysicalPortClaim、CableRouteSegment 必须同步解除活动关系；保留历史行供审计，不能只删 3D 图形或 Cable 父记录。`PhysicalPortClaim.owner_id` 是无外键的多态 UUID，必须以 `owner_type=cable_termination` 和端接记录 ID 精确解除 claim，不能假设数据库自动级联。
3. 删除权限独立定义，如 `cable:delete`、`cable:remove`、`device:delete` 等。首版所有者／管理员具备权限，viewer 禁止；编辑者默认可以删除无依赖的规划资料，拆除已投用对象默认仍由管理员执行。旧角色权限不静默扩大。
4. 单个对象的删除需要完整、准确的项目／位置范围校验；跨房间线缆要核对两端。客户端已加载数据可能不完整，后端必须检查全量依赖。无权读取的关联只返回概括性阻碍，不泄露其他范围对象名称。
5. `REMOVED` 和软删除必须同步反映到 3D、拓扑追踪、端口可用性、线槽容量、列表、检查助手与统计；历史视图需要显式筛选，不能把已拆除对象当活动连接。当前仅报表明确排除 REMOVED，场景与 occupied 主要依赖 deleted_at 和端接／claim，因此只修改状态不满足删除验收。
6. `Device.rack_id` 的数据库行为为 `ON DELETE SET NULL`，不能依靠外键阻止删除非空机柜；服务层应明确阻断，防止设备被静默移成未入柜状态。未完成工单等活动业务引用也应出现在删除预览中，先处理关联再执行。
7. `Cable.identifier` 的唯一性在首版继续保留，避免删除后悄悄复用历史编号。若后续支持复用，需单独决定历史查询及恢复冲突规则。

相关文件：`apps/api/app/models.py`、`fiber_models.py`、`services/connectivity.py`、`services/workflow.py`、`services/scene.py`、`services/workspaces.py`、`apps/web-react/src/spatial/SpatialInspector.tsx`、`SpatialPage.tsx`、`keyboardShortcuts.ts`、`pages/CablesPage.tsx`。

## 6. 验收用例与交付

- 规划线缆从 3D／列表删除后，刷新仍消失；A/B 原端口可重新连线，不能被旧的删除请求误释放。
- 铜缆与无复杂拓扑依赖的光缆均覆盖；有关联的光缆得到可解释的阻断。
- 查看者不能删除；其他账号的个人空间和其他租户不可访问；并发编辑导致明确版本冲突。
- 拆除已投用线缆保留测试、工单与审计历史，活动连接和容量不再计入。
- 非空机柜／房间、被占用线槽的删除被阻断；移走依赖后可成功删除空对象。
- 输入框内的 Delete／Backspace 不影响场景；取消确认不改变数据；请求失败可以重试。
- 迁移在空库和当前库副本上验证，先备份再应用到本地；不修改原有演示资产作为破坏性测试样本。
- 运行相关后端测试、完整前端回归及 build；在浏览器实际创建临时对象后走删除流程，更新 3D 教程和认证接入说明。

执行起点：先确认目录连接资料；同时落地线缆删除服务与测试，然后接 UI。真实 LDAP 未就绪时，继续完成删除功能并保留具体联调步骤，不把模拟成功标记为真实认证已接通。

## 参考

- [Keycloak Server Administration：LDAP / User Federation](https://www.keycloak.org/docs/latest/server_admin/)
- [Microsoft：LDAP signing 与 channel binding](https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/ldap-signing)
- 仓库现有：[账号工作空间与 SSO](../ACCOUNT_WORKSPACES_AND_SSO.md)、[3D 使用教程](../USER_GUIDE_3D.md)
