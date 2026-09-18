# 账号工作空间与网页 SSO 接入

## 使用方式

管理端右上角“账户与工作区”提供账号信息、空间切换、创建、成员授权与退出入口。

- **个人工作区**：独立保存机房、机柜、设备、线缆和布局，仅所有者能访问。不是个人视图或浏览器本地副本。
- **共享工作区**：资料在授权成员之间共享；查看者只读，编辑者可以规划和修改，所有者/管理员管理成员。
- 新建默认个人使用。需要协作时，勾选确认后启用共享，再通过已存在的账号邮箱逐一授权。不会自动公开或发送邮件。
- 界面的“撤销访问”同时撤销该账号在此空间内的成员资格与独立项目授权，其他空间不受影响。后续 API 请求重新验证权限；已打开页面在账号复核后清理旧内容。旧版 API 默认仅移除成员，需显式传入 `revoke_scoped_access=true` 才会一起撤销项目授权。
- 原有租户对应原有共享工作区，既有资产和权限保留。工作区复用完整租户数据隔离，不复制原资产。
- 切换空间会清除旧空间的项目和房间选择。浏览器只按账号保存最近工作位置；服务端返回的可访问空间列表决定实际选择范围。
- 正式账号由统一身份提供方认证；本地 `local-dev.py` 仍是明确标注的 demo 模式。

现有空间隔离依靠服务端权限、租户过滤以及 PostgreSQL RLS。此版本新增个人所有者约束，保护所有使用既有 Principal 的业务接口；前端隐藏按钮不作为权限边界。

## 外部平台跳转入口

有本系统有效 Cookie 的浏览器可以直接访问 `/app-next/locations` 等业务地址，页面会请求 `/api/v1/auth/session` 识别账号和可访问空间。

需要复用统一登录平台会话时，从外部导航到：

```text
/app-next/auth/entry?return_to=%2Fapp-next%2Flocations
```

指定空间和房间的示例（外部平台需对整个 return_to 做 URL 编码）：

```text
return_to=/app-next/3d?workspace=<workspace-uuid>&room=<room-uuid>
```

`workspace` 是空间选择参数，不是身份或授权。未授权空间返回拒绝页面，不会回退到其他人的空间。不能将账号、JWT、Cookie 或 opaque session 值放在跳转 URL 中。

流程：

```text
外部平台导航
  → /app-next/auth/entry
  → /api/v1/auth/session：已有可信 Cookie 则恢复账号
  → 无会话时 /api/v1/auth/login
  → 已配置的 OIDC 身份提供方（复用其登录会话）
  → /api/v1/auth/callback：服务端交换授权码、验证身份
  → 设置本系统 Cookie，跳回原业务页面
  → 服务端逐次验证工作区和业务权限
```

浏览器 Cookie 有域名边界；其他系统的任意 Cookie/opaque session 不能直接当成本系统身份。通用对接方式是共享 OIDC 身份提供方，或由可信统一网关把身份转换为本系统可验证的 OIDC JWT Cookie。已有网关 Cookie 必须符合相同 issuer、audience、签名、有效期及账号映射校验，不能只检查 Cookie 是否存在。跨系统 opaque session introspection、SAML、SCIM、多个 IdP 联邦尚未实现。

## 服务端配置

默认 `SSO_ENABLED=false`。正式部署配置见 `.env.example`，核心字段如下：

```dotenv
AUTH_MODE=oidc
DEMO_MODE=false
COOKIE_AUTH_ENABLED=true
CORS_ALLOW_CREDENTIALS=true
COOKIE_SECURE=true
COOKIE_SAMESITE=lax
OIDC_ISSUER_URL=https://identity.example/realms/infrastructure
OIDC_AUDIENCE=sim-api
SSO_ENABLED=true
SSO_CLIENT_ID=sim-web
SSO_REDIRECT_URI=https://cables.example/api/v1/auth/callback
SSO_STATE_TTL_SECONDS=300
```

另外通过密钥管理配置 `SSO_STATE_SECRET`（至少 32 字符的高熵随机密钥），所有 API 实例一致；机密客户端配置 `SSO_CLIENT_SECRET`，只在服务端保存。生产还需满足既有 HTTPS、受信任代理、精确 CORS/Host 和共享限流配置，不能只复制上述最小片段。

- 身份提供方客户端允许精确回调 `/api/v1/auth/callback`，启用 Authorization Code 和 PKCE S256。
- 授权与 token 端点默认从 issuer discovery 获取，也可显式配置 `SSO_AUTHORIZATION_URL`、`SSO_TOKEN_URL`；HTTPS 必需，本机 loopback 调试可使用 HTTP。
- ID token audience 是 `SSO_CLIENT_ID`，access token audience 是 `OIDC_AUDIENCE`；API token 必须为本系统可校验的 JWT。
- 用户必须已建档，并以 `UserIdentity.oidc_subject` 关联统一账号。未知或停用账号不会自动注册、自动加入团队或默认授予管理员权限。
- 若 access token 携带 `tenant_id` claim，只允许该 workspace；此类 token 不提供跨空间创建能力。需要跨空间时，身份平台应签发不绑定单一租户的 API token，由本系统成员关系逐空间授权。
- Cookie 是本域、HttpOnly（认证 Cookie）、Secure，默认 SameSite=Lax；CSRF Cookie 可由同域前端读取并放入配置的请求头。自定义 CSRF 头需同步加入 CORS 允许列表。
- 本地退出清除本系统 Cookie 和浏览器旧 token；不会注销外部身份提供方的全局会话。再次选择统一登录可复用其 SSO 会话。
- 授权码、state 和 nonce 在服务端验证；过期、错误 state/nonce、身份不一致、开放重定向及 URL 凭据会被拒绝。授权码一次性使用由身份提供方执行，成功/失败回调都会清除浏览器流程 Cookie。
- 当前使用 JWT Cookie，其最长支持 3800 字符，过长会明确拒绝；尚未提供服务端 opaque session store、refresh token 续期或全局单点登出。

## API 合约

| 接口 | 用途 |
| --- | --- |
| `GET /api/v1/auth/config` | 浏览器登录能力与 CSRF 名称；不返回密钥 |
| `GET /api/v1/auth/session` | 已验证账号、可访问空间、权限与可创建状态 |
| `GET /api/v1/auth/login?return_to=...` | 开始服务器 OIDC 流程 |
| `GET /api/v1/auth/callback` | 校验授权码并建立 Cookie 登录 |
| `POST /api/v1/auth/logout` | CSRF 保护的本地退出 |
| `GET /api/v1/workspaces` | 当前账号可访问空间 |
| `POST /api/v1/workspaces` | 创建独立空间；界面默认 personal |
| `PATCH /api/v1/workspaces/{id}` | 管理员改名，所有者明确转换共享状态 |
| `GET /api/v1/workspaces/{id}/members` | 管理员读取成员 |
| `PUT /api/v1/workspaces/{id}/members` | 以邮箱授予 viewer/editor 权限；已有成员可携带 `expected_version` 防止覆盖别人修改 |
| `DELETE /api/v1/workspaces/{id}/members/{user_id}` | 撤销普通成员；支持 `expected_version` 和 `revoke_scoped_access`；不能移除所有者/管理员/自己 |
| `POST /api/v1/workspaces/{id}/leave` | 普通成员／限定范围协作方主动退出，撤销自己在此空间的成员与项目授权；保留共享资料 |

迁移 `200000000010` 增加 Tenant 的 workspace_kind、workspace_owner_id 及约束，旧数据默认 shared；没有新增资产表或更改资产 ID。升级前备份数据库。真实 PostgreSQL 普通应用角色的 RLS 矩阵仍需专门环境验证。

## 验证范围与接入验收

自动测试覆盖个人数据隔离、共享角色、成员撤销、停用账号/空间、JWT scope、Cookie CSRF、state/nonce/issuer/audience、跳转路径限制、账号偏好隔离与过期请求竞争。SSO 测试使用本地签名测试凭据和模拟 token endpoint，不等同真实 IdP 联调。

真实对接还需要：提供身份平台 issuer、client、回调域名和账号 subject 映射；使用两个真实账号验证从外部平台跳入、无权限拒绝、过期重新登录及退出。当前没有宣称 SOC Platform、Observium 或其他真实平台已完成单点登录接通。

参考：[OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0-18.html)、[MDN Cookie 域名与 SameSite](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie)。

## 本地验收记录（2026-09-17）

- 后端完整回归：333 passed，1 skipped（专用 PostgreSQL RLS runtime 未配置）。
- 前端完整回归：33 个测试文件、285 项测试通过；TypeScript + Vite production build 通过。
- 010 迁移在既有数据副本上验证：48 张原有表记录数不变，foreign_key_check 无异常；本地服务升级前另做 SQLite 在线备份。
- 浏览器实际创建“个人规划（演示）”，初始资产统计为 0；切回 Northstar University 后恢复原有 3 建筑、14 机柜、21 设备、18 线缆。
- 验证 `/app-next/auth/entry?return_to=%2Fapp-next%2Flocations` 在已识别演示账号下回到位置页面；这不代表真实外部 IdP 已联通。
- 手机宽度 375px 页面无水平溢出；恢复默认窗口尺寸、Northstar 共享空间及原 Floor 2 / 项目范围。浏览器未记录 JavaScript error。
- 仍有既有 Vite 主包尺寸提示；没有在本次认证改动中调整打包策略。


## 共享与隔离补全（2026-09-18）

### 用户操作

右上角“账户与工作区”现在明确显示个人／共享状态、当前角色、成员数量与可访问范围：

1. **个人使用**：创建独立个人空间。房间、机柜、设备、线缆、图纸都属于该空间，其他账号即使知道链接也不能访问。
2. **启用协作**：所有者确认转为共享，再用已建档账号的邮箱授予查看者或编辑者；不是自动公开，也不会发送邀请邮件。
3. **调整角色**：在成员行选择“修改角色”，核对下方邮箱和角色后保存。当前界面携带读取到的成员版本；旧版本修改／撤销返回 409，要求刷新核对，不能盲目重试覆盖。
4. **收回访问**：确认撤销时同时收回该账号在此空间的独立项目授权；账号之前创建的共享资料保留。
5. **主动退出**：普通成员或限定范围协作方可明确确认退出；仅撤销本人的成员和项目授权，不删除空间或资产。重新加入需要管理员授权。
6. **恢复个人空间**：实际所有者可操作；必须先清除其他成员及独立项目授权，否则服务端拒绝。原有未登记 `workspace_owner_id` 的历史共享空间不擅自指定所有者，也不自动开放此转换。

所有者／管理员不能通过“退出”使空间失去管理者。本轮没有实现所有权转移、匿名链接、逐对象分享、邀请邮件、跨空间复制／合并、真实账号自动建档或 LDAP 联调。

| 角色 | 内容能力 | 共享管理 |
| --- | --- | --- |
| 个人空间所有者 | 仅自己可见，使用已有业务功能 | 可明确启用共享 |
| 共享查看者 | 查阅、追踪及获授权导出 | 可主动退出；不能改成员 |
| 共享编辑者 | 查看，并规划、创建、编辑及测试 | 可主动退出；不能审批、管理成员或提升自己的权限 |
| 所有者／管理员 | 按实际权限管理空间内容 | 改名、成员授权和撤销；只有实际所有者能转换共享状态 |
| 限定范围协作方 | 只访问实际获授权项目／位置的对象 | 可退出自己的授权；不能扩大授权或转授权 |

共享的单位是工作空间。把某个空间设为共享不会把同账号的其他个人空间一起分享；“工作范围”筛选也不改变服务端权限。

### 服务端边界

- Principal 解析会验证所选项目、位置确实属于该空间且未删除。位置祖先遍历不能跨空间，也不复用过期父节点缓存。
- 成员角色、账号和空间状态从数据库重新读取，避免长生命周期会话把已降级的角色继续当成管理员。
- 创建房间／机柜／设备／线槽／线缆、安装／测试／审批、标签与追踪均检查实际关联对象；知道 UUID 不能代替授权。跨租户引用在可绕过 ORM 租户过滤的隔离测试会话中也被拒绝。
- 搜索及 Dashboard 按当前账号实际可见对象过滤；授权结果之后才截取返回条数，避免范围外对象挤占分页。
- 完整合规报告、原始审计和 CSV 属于全空间操作，仅工作空间成员且具有对应权限时可用；限定范围协作方返回 403，不返回看似完整的部分报告。
- 项目授权只能由具有对应权限的成员管理，目标账号须已启用、与所声明组织匹配，项目／位置须在本空间；不能授予自己不具备的权限。个人空间不能对外创建项目授权。组织级无具体账号的授权当前不能认证为主体，因此新建接口明确拒绝。
- 工作空间描述新增 `is_owner`、`can_leave`、`member_count`；成员列表新增 `version` 与 `has_scoped_access`。限定范围协作方不获得全空间成员数量。
- 共享管理与旧授权接口的创建／撤销共用空间事务锁，取得锁后重新读取共享状态和操作者权限。新增授权与恢复个人空间不能交错留下将来重新共享时生效的残余授权；SQLite 双连接回归已验证两种顺序。PostgreSQL 使用 `SELECT … FOR UPDATE`，尚未执行真实 PostgreSQL 并发测试。旧版客户端可不传成员版本以保持兼容；需要冲突保护的集成必须先读取成员版本并提交 `expected_version`，不能把可选参数理解为全体旧客户端已具备并发保护。

### 浏览器内容隔离

账号、空间、项目／位置、角色或授权范围变化时，页面清理旧资源请求结果及查询缓存，再挂载对应内容。晚到的旧响应不能进入新账号／新空间，旧请求的 401／403 也不能误清新的会话。丢弃晚到的写响应不代表服务端写入已取消，客户端不会自动重试提交。

在页面重新获得焦点、重新可见、收到当前 API 的 403 或其他标签页的会话信号时，重新向服务器读取账号与空间权限。普通“缺少某项操作权限”的 403 不会直接注销整个账号；明确失去身份或空间访问则清除原内容。指定但无权的空间深链接显示阻断页，必须由用户选择其他有权空间，不能静默切换。

同源标签页之间仅广播“会话变更／退出”与随机标识，不广播账号资料、对象内容或 token。退出立即清空当前页访问；服务端退出失败时明确提示重试，不宣称 Cookie 已被撤销。远端撤权不是实时推送，已显示内容在下一次上述复核时清除，服务端每次请求仍独立授权。

本地偏好只保存账号对应的空间选择；禁用浏览器偏好存储不授予权限。真实跨系统会话仍需前述 OIDC 接入，不能用其他网站的任意 Cookie 或 URL 参数冒充本系统账号。

### 本轮验证

- 后端完整回归：442 passed，1 skipped；未配置专用 PostgreSQL RLS runtime DSN，因此跳过真实 PostgreSQL 隔离矩阵。这不等同完成生产数据库验收。
- 前端完整回归：35 个文件、337 项测试通过；严格 TypeScript 和 Vite production build 通过。主包与三维包仍有超过 500 kB 的构建提示。
- 覆盖成员角色版本冲突、撤销独立授权、自助退出、个人空间恢复、失效账号、缓存成员降级、跨租户引用、范围外统计与导出、3D 缓存对象移位／父级变化、晚到响应、跨标签页及损坏的 401 响应等场景。
- 本地服务更新前备份：`.local/backups/before-workspace-sharing-20260918-112530.db`。重启后 49 张表记录数不变，`foreign_key_check` 无异常，迁移版本仍为 `200000000010`。
- 浏览器只读验收：共享空间管理菜单、成员列表、个人内容说明、启用共享前确认、个人／共享切换及三维场景正常；既有个人空间统计为 0，切回 Northstar University 恢复 3 栋建筑、14 个机柜、21 台设备、18 条线缆。恢复原 Floor 2／项目范围，停留在位置页面，未记录 JavaScript error。
- 实际授予／撤销权限、退出、转换共享状态等写操作在隔离测试数据库验证；本轮没有改变本地既有成员权限或资产。本轮不增加数据库表或迁移。
- 当前本地启动器仍采用演示身份。真实 LDAP／OIDC 账号联调、生产 RLS、账号建档管理、邮件邀请及所有权转移不在本轮完成范围。

### 多范围授权变更的内容失效（2026-09-18 后续）

会话中的每个 workspace 增加不透明 `access_revision`，覆盖所有实际有效授权的范围、权限与有效期。即使第一个范围不变，第二个范围撤权或修改权限也会使前端访问指纹变化，清理旧内容并丢弃晚到响应；等价的授权排列次序不触发重置。此修复不扩大权限，不增加实时推送；仍在已有焦点／可见性／API 拒绝等会话复核时生效。
