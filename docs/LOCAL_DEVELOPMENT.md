# macOS 本地构建与运行

更新日期：2026-09-18。基于 PR #7 的 `15796aaab3b43bb4a238b63e89c7401db58a91f6`，本地工作分支 `codex/local-build`。

## 当前运行地址

- React 管理端：http://127.0.0.1:18000/app-next/
- Three.js 3D 工作区：http://127.0.0.1:18000/app-next/3d
- 原生 WebGL 界面：http://127.0.0.1:18000/app/
- 现场 PWA：http://127.0.0.1:18000/app/field/
- API 文档：http://127.0.0.1:18000/api/v1/docs

启动脚本使用独立 `.local/app.db`（SQLite），自动迁移到当前 head（截至 2026-09-18 为 `200000000012`） 并幂等导入 Northstar 演示数据。仅监听 `127.0.0.1`，启用 demo 身份。React 首次打开会自动读取演示租户上下文；已有上下文或 OIDC token 时保留用户选择。

## 已安装环境的日常命令

在仓库根目录执行：

```bash
.venv/bin/python scripts/local-dev.py status
.venv/bin/python scripts/local-dev.py stop
.venv/bin/python scripts/local-dev.py build
.venv/bin/python scripts/local-dev.py start
```

后端修改后执行 stop/start；前端修改后执行 build，再刷新页面。服务后台运行，重启 Mac 后需再次执行 start。停止服务保留数据。可用 `start --port 18001` 更换端口。

- 日志：`.local/server.log`、`.local/setup.log`
- 进程信息：`.local/server.json`，停止前会核对进程属于本仓库
- 数据和本地文件：`.local/app.db`、`.local/storage/`
- 构建产物：`apps/web-react/dist/`

前端热更新开发：后端启动后，在 `apps/web-react` 执行 `npm run dev`，打开 http://localhost:5173/app-next/。Vite 默认代理到 `127.0.0.1:18000`；若更换 API 端口，同步调整 `vite.config.ts`。

## 新环境安装

需要原生 ARM64 Python 3.12+、Node.js 22.12+。macOS 自带的旧 Python 不适用。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install '.[dev]'
cd apps/web-react
npm exec --yes --package=npm@11 -- npm ci --no-audit --no-fund
cd ../..
.venv/bin/python scripts/local-dev.py build
.venv/bin/python scripts/local-dev.py start
```

本机 npm 10 安装时遇到 Arborist `edgesOut` 错误，已通过临时 npm 11 安装成功，没有修改全局 npm。前端已生成锁文件，React/React DOM 和 React Router 版本已对齐。

## 本次修复

- 将非 revision 的迁移辅助文件移出 Alembic `versions/`，修复最新迁移无法发现的问题；revision ID 和数据库结构保持不变。
- 统一标准 API 入口中的 Fiber、2D Floor Plan、现场幂等重放和集成模块。额外 `main_*` 入口指向同一个应用，使用标准 Floor Plan API 合约。
- 消除两套 Floor Plan ORM 对同名表的重复定义；旧 Python 字段名兼容映射到 canonical 模型。
- 修复 SQLite 下 Webhook 重试时间的时区比较问题，以及与固定日期绑定的测试。
- 补齐遗漏的 Channel/Breakout、OTDR 组件，修复 React 类型、通知适配和路由实例冲突。
- 支持 React 子页面刷新，并为原生界面接通 Cable Schedule CSV 导出。
- 修正 `make test` / `make dry-run` 的测试发现范围，覆盖所有模块。

## 最新验证（2026-09-18）

- 后端完整回归：516 passed、1 skipped（未配置真实 PostgreSQL RLS DSN）。
- 前端完整回归：45 文件、436 项通过；TypeScript、Vite 生产构建和本轮 Ruff 检查通过。
- 240 柜超算场景、端口分页、线路创建及局部线槽路由已通过本地浏览器验证。
- 功能、迁移及验收入口见 [本次版本说明](RELEASE_2026-09-18.md)。

## 初次部署验证记录（历史）

- 本地部署初次 `make dry-run`：207 passed、1 skipped；加入 3D 场景 API 后，完整后端测试为 238 passed、1 skipped。跳过项为未配置真实 PostgreSQL 的 Forced-RLS 测试。
- `npm test`：60 passed；包含 Channel/OTDR、场景坐标与连接、跨房间/跨机架选择及 WebGL 回调生命周期回归测试。
- `npm run build`：TypeScript 和 Vite 正式构建通过。
- 空库升级到 `008`、重复 seed、CSV 下载与审计验证通过。
- 浏览器验证：首页、线缆列表、2D Floor Plan、Channel/Breakout 和 OTDR 入口正常；实际创建平面图、绑定机架、保存 r2 并发布成功。
- 本地演示平面图 ID：`83667cd1-e0ad-47ec-b52a-85a0d6f3452f`。在 2D Floor Plan 页的 UUID 输入框加载即可查看。

运行初期 API RSS 约 100 MiB；Python 依赖约 132 MiB，加入 Three.js 后前端依赖约 394 MiB，构建产物约 2.4 MiB。它们是演示规模下的测量，不代表生产并发容量。

## 3D 空间工作区

新增独立 `/app-next/3d` 页面，管理端菜单、机架列表和线缆列表均有入口。支持房间/Server Room、批量机柜、模板设备、线槽及线缆创建与机柜位置保存。线缆常驻实线，选中高亮；带编号的桥架、U 位和设备面板可选择与定位。读取使用 `GET /api/v1/scene`，编辑使用 `/scene/*`，沿用当前租户、项目、位置与读写权限。

详细的数据约定、操作方式与当前边界见 [3D_WORKSPACE.md](3D_WORKSPACE.md)。

## 当前边界

此部署用于本机开发。真实 PostgreSQL RLS、Keycloak 登录、真实 NetBox/Webhook 外部服务和生产负载测试尚未在本机验证。前端主 bundle 仍较大（压缩前约 1.7 MiB），后续可按页面拆包。本次发布分支为 `codex/local-build`；本地数据库、日志和构建产物保存在各自忽略目录中。
