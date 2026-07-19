# Android Agent

Android Agent 由 Python/FastAPI 服务端和 Android 客户端组成。App 可初始化设备连接、创建隔离的 Android 项目，并通过 Agent 修改和构建项目。第一阶段的完整范围见 `MVP_SPEC.md`。

## 第一阶段能力

- SQLite 持久化项目任务、事件、Token usage、改动摘要和构建产物。
- 同一项目串行执行，可请求停止，服务重启后中断任务会标记失败。
- Agent 必须执行 `assembleDebug`，成功任务保留任务级 APK 和构建日志。
- 手机端支持任务提交、轮询、停止、历史恢复、调试事件、APK 下载与安装。

## 多对话（Cursor 式）

每个 Android 项目下可开多个独立 **Conversation（对话）**，各自保留 Agent 上下文：

- `GET/POST /api/projects/{id}/conversations` — 列表 / 新建
- `GET/PATCH/DELETE /api/conversations/{id}` — 详情 / 改标题 / 归档
- `POST /api/conversations/{id}/ask` — 在该对话中提问（多轮连续）
- 同一项目同时只跑一个 turn（workspace 锁）；未调用 `assembleDebug` 的追问也可成功
- 旧版 `POST /api/projects/{id}/ask` 仍可用，内部自动挂到默认对话

## 设备初始化与目录隔离

App 首次使用时填写服务器地址并点击“初始化设备连接”。服务端会生成唯一的 `user_id` 和随机访问 Token：

- 账号数据库：`data/users.db`（只保存 Token 的 SHA-256 哈希）
- 用户项目：`workspaces/{user_id}/{project_id}`
- 用户构建：`builds/{user_id}/{project_id}`

之后所有 API 请求都通过 `Authorization: Bearer <token>` 确定用户身份。客户端不能通过修改 `user_id` 访问其他用户目录。

> Token 只在注册响应中返回一次。请勿清除 App 数据；丢失 Token 后无法恢复原账号。

## 启动服务

```bash
python3 -m pip install -r requirements.txt
cp config.yaml.example config.yaml
python3 -m agent serve
```

启动后：

- 可视化操作台：`http://127.0.0.1:8000/ui/`（打开即自动连接本机 `local` 用户，无需注册）
- API 文档：`http://127.0.0.1:8000/docs`

手机连接时使用电脑的局域网 IP，例如 `http://192.168.1.100:8000`。无 Token 时 API 同样默认使用 `local` 用户；需要隔离时再走注册或配置 Token。

## 桌面端（Electron + Monaco）

Cursor 式三栏桌面编辑器：左侧文件树、中间 Monaco、右侧 AI 占位（暂未接 Agent）。

需本机已安装 Node.js 18+。

```bash
cd desktop
npm install
npm start
```

> Electron 本体约 100MB+，默认从 GitHub 下载，国内常会长时间无进度。本目录已配置 `.npmrc` 使用 npmmirror 镜像；若仍慢，可手动执行：
> `export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/`

- 默认尝试打开仓库下的 `workspaces/`（若存在）
- 支持打开文件夹 / 文件、多标签编辑、保存（⌘S / Ctrl+S）、未保存关闭确认
- 快捷键与菜单：新建、打开、另存为

## 验证与构建

```bash
python3 -m unittest discover -s tests -v
cd android-app && ./gradlew assembleDebug
cd ../template && ./gradlew assembleDebug
```

手机端 Debug APK 位于 `android-app/app/build/outputs/apk/debug/app-debug.apk`。

## 迁移到云服务器

代码不依赖本机账号系统。迁移时复制项目代码，并持久化以下三个目录即可：

```text
data/
workspaces/
builds/
```

可通过 `AGENT_DATA_DIR` 把账号数据库放到独立持久化磁盘。生产环境应使用 HTTPS，并在反向代理层为 `/api/register` 添加限流。若以后需要多实例部署，可保持 API 不变，将 `UserStore` 的 SQLite 实现替换为云数据库。
