# Android Agent

Android Agent 由 Python/FastAPI 服务端和 Android 客户端组成。App 使用服务端签发的 Token 连接、创建隔离的 Android 项目，并通过 Agent 修改和构建项目。第一阶段的完整范围见 `MVP_SPEC.md`。

## 创意广场（Android）

Android 客户端采用 View/XML 与 Jetpack Compose 混合架构。底部导航中的“创意”默认读取服务端目录，支持搜索、动态分类、分页、详情、封面和源码复制。后台编辑与上下架可在客户端刷新后生效，无需重新发布 APK。

- 管理员进入 `/admin/` → **创意目录**，可以新建、编辑草稿、发布指定版本、回滚历史版本、上下架、归档、设置推荐与排序，并管理分类和查看审计记录。
- 首次使用可点击 **导入内置创意**。默认只导入草稿，勾选“同时上架本次新增条目”才会发布；重复导入跳过已有条目，保留后台修改和下架状态。
- 线上版本与编辑草稿隔离。后台下架后，公开目录、详情和该条目独占的公开封面不可再读取；已被用户下载或复制的内容无法远程撤回。
- 目录缓存按服务地址隔离，网络失败时明确标注离线。成功返回空目录会清空旧目录，不会用本地示例补回已下架条目。
- 已接通用户草稿、源码/封面上传、后台审核和审核消息。正式账号进入 **我的创意**，保存本机草稿 → 同步云端 → 确认公开清单 → 提交审核；管理员在 **审核队列** 查看版本差异、退修、拒绝或批准发布，其他用户随后可在广场浏览。
- 正式账号可在公开详情收藏或举报；收藏按服务地址和账号隔离。管理员通过 **举报处理** 核对问题、填写公开处理结果和内部备注，必要时用版本检查原子下架；举报人和社区作者分别收到不含内部备注的消息。
- 新投稿默认暂停。启用 `admin_ui_enabled: true`、配置独立管理员凭据后，设置 `creative_submissions_enabled: true` 或 `AGENT_CREATIVE_SUBMISSIONS_ENABLED=true` 并重启服务。暂停新投稿仍保留草稿、撤回、已有审核和消息访问；`creative_catalog_enabled: false` 会关闭整个创意 API。
- 社区内容的受管应用和隔离构建验证仍未开放；功能状态以 `/api/creative/capabilities` 为准，人工审核通过不会标成构建验证通过。

独立的 **内置示例** 入口保留原有 500 个示例及离线预览：

- 点击卡片查看实时效果和完整 Compose 源码。
- “复制代码”把核心 `@Composable` 写入系统剪贴板。
- “应用到项目”选择已有项目后创建独立对话，把 Recipe 源码、兼容要求和构建验证要求发送给 Agent；Agent 会根据目标项目是 Compose 还是 XML/View 做适配。
- Recipe 目录位于 `android-app/app/src/main/java/com/androidagent/client/creative/CreativeCatalog.kt`。新增条目时应提供唯一 ID、分类、预览类型、可复制源码和最低 SDK。
- 当前包含 500 个示例（26 个视觉系列、48 套布局及原有组件），支持分类与风格组合筛选；收录规则、参考来源与生成方式见 [创意广场样式目录](docs/CREATIVE_SQUARE_CATALOG.md)。

Compose 目前仅作为新增视觉模块使用，不要求一次性迁移现有 Activity/Fragment。

未登录用户可以在登录页选择“暂不登录，浏览创意广场”进入游客模式。公开目录无需账号；内置示例的预览与源码复制离线可用，项目、任务、审批以及内置示例的“应用到项目”会显示登录或服务连接引导。目录管理的历史验收见 [C1 实施记录](docs/CREATIVE_SQUARE_IMPLEMENTATION_STATUS.md)，本次投稿能力、配置、额度与备份恢复见 [C2 实施记录](docs/CREATIVE_SQUARE_C2_IMPLEMENTATION_STATUS.md)。

## 第一阶段能力

- SQLite 持久化项目任务、事件、Token usage、改动摘要和构建产物。
- 同一项目串行执行，可请求停止，服务重启后中断任务会标记失败。
- Agent 必须执行 `assembleDebug`，成功任务保留任务级 APK 和构建日志。
- 手机端支持连接/项目、多 Conversation、任务流、审批、steer/follow_up/pause/resume/cancel、Project Workspace Dashboard、Changes 分类审查、Hunk 接受/拒绝/解释/回退、Context Chips、`@` 文件/符号/目录检索、Context Inspector、Diff/Checkpoint 恢复、构建日志与 APK 下载安装分享；WebSocket 优先并在断线后游标轮询。

## 多对话（Cursor 式）

Mobile Code Explorer 提供逻辑目录、文件搜索/筛选、Modified/Open files、选区 Agent 操作及带版本检查的 Quick Edit。Build / Test / Problems 提供结构化构建摘要、JUnit 测试结果、统一问题列表和最多两次修复的自动反馈循环。使用方式与边界见 [File Explorer / Feedback V2](docs/FILE_EXPLORER_FEEDBACK_V2.md)。

Project History / Revert turn 提供保留对话的代码恢复与从快照创建分支；Task Center 使用 WorkManager 同步和审批/完成通知；Subagent 聚合展示；Android Remote Terminal 与桌面 xterm 支持多 session 和发送输出给 Agent。使用与恢复、后台时延边界见 [History / Tasks / Terminal V2](docs/HISTORY_TASKS_TERMINAL_V2.md)。

手机与桌面共享 Workbench UI V3 的颜色、按钮层级、卡片与响应式布局；新增大字体操作栏、可滚动登录页与窄窗口侧栏。视觉规范与回归范围见 [Workbench UI V3](docs/WORKBENCH_UI_V3.md)。

每个 Android 项目下可开多个独立 **Conversation（对话）**，各自保留 Agent 上下文：

- `GET/POST /api/projects/{id}/conversations` — 列表 / 新建
- `GET/PATCH/DELETE /api/conversations/{id}` — 详情 / 改标题 / 归档
- `POST /api/conversations/{id}/ask` — 在该对话中提问（多轮连续）
- 同一项目同时只跑一个 turn（workspace 锁）；未调用 `assembleDebug` 的追问也可成功
- 旧版 `POST /api/projects/{id}/ask` 仍可用，内部自动挂到默认对话

Conversation Composer 可通过 `+` 添加文件、目录、选区、Diff、构建日志、终端输出、错误、截图说明、Conversation 和 Symbol 上下文；输入 `@` 可按 Files、Symbols、Folders 分组搜索仓库索引。发送时显式 Context 会与自动仓库检索和项目 Memory 一起受统一预算控制，顶部 Context Inspector 可查看最近一轮实际使用的来源、估算 Token 和仓库 Symbol 数量。

## Conversation Event 模型

每个 Agent 轮次写入 `conversation_turns`，轮次内的消息、模型响应、工具调用和结果按严格递增的 `seq` 追加到 `conversation_events`。新会话上下文以这些不可变事件为权威来源，可重建 OpenAI-compatible 或 Anthropic 消息，并保留完整工具调用链。

主要规范事件包括：

- 消息与工具：`user_message`、`assistant_message`、`tool_call`、`tool_result`
- 生命周期：`turn_started`、`turn_completed`、`turn_failed`、`turn_canceled`、`turn_interrupted`
- 运行信息：`usage`、`provider_switch`、`model_switch`、`changes`
- 审批：`approval_required`、`approval_resolved`
- 上下文：`context_checkpoint`、可见的 `system_note` / `recovery_note`

旧数据库中的 `conversations.turns_json` 会在启动时幂等迁移为规范事件；字段仍保留，仅用于迁移和旧客户端的最终问答投影。`task_events` 继续承担 UI 日志、流式 delta 和 WebSocket 推送，`conversation_events` 则承担持久化、跨轮上下文和恢复，两者职责不同。

规范事件支持游标查询：

```http
GET /api/conversations/{conversation_id}/events?after_seq=0&limit=200&context_only=false
Authorization: Bearer <token>
```

`limit` 范围为 1-500，结果按 `seq` 升序返回，并提供 `next_after_seq` 与 `has_more`。接口严格校验 Conversation 所属用户并过滤凭证字段。

当较早历史超过 200 个新增事件或约 120,000 字符时，服务会追加结构化 `context_checkpoint`。checkpoint 按目标、约束、决策、未解决事项、文件、测试、工具事实和错误分类，每条事实保留来源 `seq` 并在启用前验证引用范围；无效 checkpoint 会追加失效事件并回退到原始历史。checkpoint 只改变模型上下文边界，不删除数据库事件；任务内 compact 仍作为单次请求超限时的最后保护。

服务重启时，未完成工具会先得到 `service_interrupted` 合成失败结果，但不会自动继续模型或重新执行工具。用户可对中断任务调用 `POST /api/jobs/{job_id}/recover` 显式创建恢复 Task/Turn；若恢复模型再次请求有副作用的工具，仍必须重新审批。

规范事件写入、历史读取、Job/WebSocket 输出和事件查询 API 会识别并脱敏 Bearer Token、JWT、常见 API Key 前缀、URL 用户信息以及 `api_key=...` 等自由文本形式。结构化凭证字段继续拒绝写入。自由文本检测属于防泄漏保护而非密码保险库，无法保证识别所有私有密钥格式。

当前已支持跨 Conversation 的可控项目记忆（候选审批 + 本地 FTS 检索）。通用多实例消息队列仍未实现；服务中断任务仅修复事件链，恢复执行需要用户显式触发。

## 账号、多设备登录与目录隔离

服务端默认关闭网络注册。首次使用先在运行 Agent 的电脑上创建账号：

```bash
python3 -m agent register-user
```

命令会生成唯一的 `user_id` 和只显示一次的随机访问 Token。把 Token 填入 Android、Web 或桌面客户端：

- 账号数据库：`data/users.db`（只保存 Token 的 SHA-256 哈希）
- 用户项目：`workspaces/{user_id}/{project_id}`
- 用户构建：`builds/{user_id}/{project_id}`

之后所有 API 请求都通过 `Authorization: Bearer <token>` 确定用户身份。客户端不能通过修改 `user_id` 访问其他用户目录。

> Token 只显示一次。丢失后无法恢复原值；启用管理后台后可为原账号签发新 Token。

Android 客户端也支持邮箱密码账号。设置 `registration_enabled: true` 后可使用
`POST /api/auth/register` 注册、`POST /api/auth/login` 登录；每次登录创建一条独立
设备会话。`GET /api/devices` 可查看当前账号的设备，支持撤销单台设备或登出其他
设备。修改密码默认撤销其他设备，注销账号会撤销全部凭据并删除用户工作区、构建
产物和任务数据。密码只保存带随机盐的 scrypt 或 PBKDF2-SHA256 摘要。

受信内网默认可免邮箱验证。面向公网时应设置 `email_verification_required: true`，
并配置 `smtp_host`、`smtp_port`、`smtp_username`、`smtp_password`、`smtp_from`；
敏感 SMTP 密码建议通过 `AGENT_SMTP_PASSWORD` 环境变量注入。验证码仅保存 SHA-256
摘要、15 分钟过期且只能使用一次。

如确需让手机通过网络配对，在 `config.yaml` 同时设置
`registration_enabled: true` 和随机长字符串 `registration_token`，并在手机端填写该注册密钥。注册密钥不会保存在手机偏好中。

旧客户端继续调用 `POST /api/pair` 完成配对；`POST /api/register` 是兼容别名。账号客户端使用 `/api/auth/*`，已有配对 Token 无需迁移即可继续工作。浏览器 WebSocket 会先通过 Bearer Token 申请 5-120 秒、单次使用且绑定具体 Job/Terminal 的 ticket，长效 Token 不进入 URL。桌面 Token 使用系统 `safeStorage`，Android Token 使用 Keystore 加密，调试 Web 只使用当前标签页的 `sessionStorage`。

新版 Android 与桌面端的普通登录界面只需要邮箱和密码；服务器 HTTPS 地址由发布包统一配置，不再要求用户填写地址、Token 或配对密钥。完整生产部署、客户端地址注入、SMTP 与备份步骤见 [云服务器部署指南](docs/CLOUD_DEPLOY.md)。

优先使用 Railway 时，仓库根目录已提供包含 Python、JDK 17、Android SDK 36 的 Dockerfile，按 [Railway 部署指南](docs/RAILWAY_DEPLOY.md) 挂载 `/data` Volume、配置 `/healthz` 健康检查并生成 HTTPS 域名。

## 启动服务

本地服务使用 Python 3.12（见 `.python-version`）；Android 构建使用 JDK 17 和 SDK 36，JDK 通过 `JAVA_HOME` 或本机 Gradle 配置指定。仓库不再固定某台 Mac 的 Android Studio 路径。Node 版本要求见 `desktop/package.json`。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
cp config.yaml.example config.yaml
python3 -m agent register-user
python3 -m agent serve
```

启动后：

- 本地调试操作台：`http://127.0.0.1:8000/ui/`（仅 loopback 且 `debug_web_ui_enabled: true` 时挂载）
- 账号管理后台：`http://127.0.0.1:8000/admin/`（需显式启用，见下文）
- API 文档：`http://127.0.0.1:8000/docs`

服务默认只监听 `127.0.0.1`。手机连接时需要显式把 `server_host` 改为 `0.0.0.0`；Android Debug 构建可在受信局域网使用 `http://192.168.1.100:8000`，Release 构建只允许 HTTPS。所有 API 请求仍必须携带有效 Token。PTY 终端默认关闭，只有在受信网络中确有需要时才设置 `terminal_enabled: true`。

### 账号管理后台

管理后台可以搜索、创建和注销账号，修改显示名称与邮箱验证状态，禁用账号、重置
密码，以及签发、查看元数据和撤销设备/API Token。密码摘要、Token 哈希和已有 Token
原文不会返回给浏览器；新 Token 仅在签发成功时显示一次。

后台默认关闭。推荐通过环境变量启动，管理员 Token 必须至少 24 位，且不能复用普通
用户 Token：

```bash
export AGENT_ADMIN_UI_ENABLED=true
export AGENT_ADMIN_TOKEN="$(openssl rand -hex 32)"
python3 -m agent serve
```

打开 `http://127.0.0.1:8000/admin/`，输入上述 Token。浏览器只在当前标签页的
`sessionStorage` 中保存它，退出或关闭标签页后清除。若管理后台需要经过公网访问，
必须置于 HTTPS 反向代理之后并限制来源 IP；不要直接暴露 HTTP 控制面。

## 网络搜索（Tavily）

在 `config.yaml` 配置后，Agent 可调用 `web_search`：

```yaml
tavily_api_key: "tvly-你的密钥"
```

也可使用环境变量 `TAVILY_API_KEY`。Key 申请：https://tavily.com

## 文件下载（需用户确认）

Agent 可调用 `download_file` 将 http/https 资源保存到工程内（推荐 `downloads/`）。
**每次下载都会暂停并弹出确认框，默认拒绝；只有你点「允许下载」后才会真正开始下载。**

## 桌面端（Electron + Monaco）

Cursor 式三栏桌面 IDE：左侧文件树 / 搜索 / 对话 / 任务，中间 Monaco + Diff Editor，右侧 Agent 对话 / Plan / 工具 / 审批，底部集成 xterm.js 终端、问题、输出和构建日志。支持对话管理、上下文 chip、断线重连、审批面板、checkpoint 恢复和响应式窄窗口。

需本机已安装 Node.js 22.12+。

```bash
cd desktop
npm ci
npm start
```

> Electron 本体约 100MB+，默认从 GitHub 下载，国内常会长时间无进度。本目录已配置 `.npmrc` 使用 npmmirror 镜像；若仍慢，可手动执行：
> `export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/`

- 默认尝试打开仓库下的 `workspaces/`（若存在）
- 支持打开文件夹 / 文件、多标签编辑、保存（⌘S / Ctrl+S）、未保存关闭确认
- 快捷键与菜单：新建、打开、另存为

## 验证与构建

```bash
# Python（带 hash 的发布依赖锁 + 全量测试）
python3 -m pip install --require-hashes -r requirements.lock
python3 -m pip install -r requirements-dev.txt
python3 -m pytest tests -q

# 创意种子与共享 API 契约
python3 scripts/export_creative_seed.py --check
python3 scripts/check_api_contract.py

# 创意后台：真实浏览器连接临时服务，不使用生产数据
CREATIVE_TEST_PYTHON=python3 node desktop/tests/creative-live.test.js

# Desktop
cd desktop && npm run check && npm run test:unit && npm run test:screenshot

# Android
cd android-app && ./gradlew testDebugUnitTest assembleDebug --offline

# 发布门禁（敏感信息扫描 + git diff --check + 上述客户端）
python3 scripts/release_check.py

# 离线 Eval 套件（16 场景，确定性 fake，无付费模型）
PYTHONPATH=. python3 -c "from evals import run_all_evals; print(sum(r.passed for r in run_all_evals()))"
```

手机端 Debug APK 位于 `android-app/app/build/outputs/apk/debug/app-debug.apk`。有模拟器时可另行执行 `./gradlew connectedDebugAndroidTest`（可选 smoke）。

架构说明见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

双端 UI/UX 基线见
[`docs/DESKTOP_ANDROID_UI_DESIGN_SYSTEM_AND_PROMPTS.md`](docs/DESKTOP_ANDROID_UI_DESIGN_SYSTEM_AND_PROMPTS.md)，
会话节点身份、Tool Cluster、流式归类与响应式排版见
[`docs/CONVERSATION_TIMELINE_V2.md`](docs/CONVERSATION_TIMELINE_V2.md)，
后续升级顺序、工作单、验收标准与实施提示词见
[`docs/UNIFIED_UPGRADE_PLAN.md`](docs/UNIFIED_UPGRADE_PLAN.md)，
源码参考依据见 [`docs/CLAUDE_CODE_REFERENCE_ANALYSIS.md`](docs/CLAUDE_CODE_REFERENCE_ANALYSIS.md)。

创意广场后台管理、用户投稿、审核发布与版本化应用的专项方案见
[`docs/CREATIVE_SQUARE_PLATFORM_UPGRADE.md`](docs/CREATIVE_SQUARE_PLATFORM_UPGRADE.md)。

## 权限模式与 Workspace trust

- 运行模式：`ask` / `workspace` / `read_only`（见 `agent/permissions.py`）。
- 项目级 MCP 需用户明确 trust 后才会注册工具。
- Rules / Skills / Hooks / MCP 配置均受路径沙箱约束；符号链接越界不会被加载。

## Git、Checkpoint 与恢复

- Checkpoint 是内容寻址快照，**不是** Git commit。
- Dirty workspace 恢复冲突时返回 `error=conflict`，不会静默覆盖。
- 服务重启会为未完成工具写入 `service_interrupted`；只有用户调用恢复接口后才创建新 Turn。

## 队列、Rules/Skills/MCP/Hooks、Subagent、记忆

- 任务队列：SQLite lease claim + fencing token + 独立心跳；运行中暂停需 worker 确认，有界 worker 池允许隔离 Subagent 并行。
- Rules / Skills：预算注入 system prompt；恶意越界文件被拒绝。
- MCP / Hooks：仅启用 stdio MCP；调用结果未知时只重置连接、不重试调用；Hook 不能削弱硬拒绝。
- Subagent：explore / reviewer / test_runner / implementer；角色工具白名单在定义和执行层双重强制，worktree 由服务端分配路径。仓库若跟踪密钥类文件将拒绝创建 worktree。
- 项目记忆：候选 → 用户批准 → 检索注入；与 Conversation checkpoint 分离。详见阶段十八。

## 数据备份与迁移

```bash
./scripts/backup_data.sh                 # 打包 data/ workspaces/ builds/
python3 scripts/migrate_db.py --backup   # 幂等 schema 确保 + 可选备份
python3 scripts/scan_secrets.py          # 敏感信息扫描
```

迁移到云服务器时持久化：

```text
data/
workspaces/
builds/
```

可通过 `AGENT_DATA_DIR` 把账号与任务库放到独立磁盘。生产环境应使用 HTTPS；如启用 `/api/register`，还应在反向代理层限流并定期轮换注册密钥。

## 桌面打包与 Android 发布

**Desktop（Electron）**

```bash
cd desktop
npm ci
npm start          # 开发

# macOS 发布需要已导入 Keychain 的 Developer ID 与 notarization 凭证；
# 缺少任一项时打包会直接失败
export CSC_NAME='Developer ID Application: ...'
export APPLE_ID='release@example.com'
export APPLE_APP_SPECIFIC_PASSWORD='从 CI secret 注入'
export APPLE_TEAM_ID='...'
npm run dist:mac
npm run verify:signature -- "dist/Android Agent-darwin-arm64/Android Agent.app"
```

桌面自动更新默认关闭。只有打包后的应用设置 `ANDROID_AGENT_UPDATE_URL=https://...`
才会检查更新；HTTP 更新源会被拒绝，下载仍由平台签名验证保护。

**Android APK**

```bash
cd android-app
./gradlew assembleRelease   # 需本机 keystore / 签名配置
# 或调试包：
./gradlew assembleDebug
# 输出：app/build/outputs/apk/debug/app-debug.apk
```

Release 签名从 `ANDROID_AGENT_KEYSTORE`、`ANDROID_AGENT_KEYSTORE_PASSWORD`、
`ANDROID_AGENT_KEY_ALIAS` 和 `ANDROID_AGENT_KEY_PASSWORD` 注入，私钥不进入仓库。
版本由 `ANDROID_AGENT_VERSION_CODE` / `ANDROID_AGENT_VERSION_NAME` 注入。

对 APK、DMG/ZIP 等产物生成 checksum、依赖清单和本地 provenance：

```bash
python3 scripts/generate_release_manifest.py \
  --artifact android-app/app/build/outputs/apk/release/app-release.apk \
  --output .artifacts/release-manifest.json
```

Python 依赖由 `requirements.lock` 的 hash 固定；Node 使用
`desktop/package-lock.json`；Gradle 使用 dependency lock 和
`gradle/verification-metadata.xml`。发布门禁另执行 `npm audit --omit=dev`。

## 资源治理与诊断

默认限制请求体、Prompt、项目/Conversation/活动任务、规范事件、Terminal、MCP、
Memory 和任务流事件数量；磁盘低于水位时拒绝新写任务，构建日志与 APK 按项目保留最近
一组有限产物。配置项见 `config.yaml.example`。

可选 Hook、MCP reader 和 cleanup 失败会写入脱敏诊断库。当前用户可查询：

```http
GET /api/diagnostics?project_id=...&task_id=...&limit=100
Authorization: Bearer <token>
```

## 安全边界与已知限制

- 路径解析拒绝前缀和符号链接越界。工具、终端、MCP 与 Git 使用操作系统隔离：Linux 必须提供 bubblewrap 和非特权用户命名空间，macOS 使用 `sandbox-exec`；只开放当前工作区和指定运行时，禁止网络，HOME、临时文件与 Gradle 缓存均在工作区内。缺少沙箱时拒绝执行，`AGENT_CMD_SANDBOX=0` 不再关闭隔离。
- 游客默认关闭；启用后只提供限额只读问答，恢复既有游客会话必须携带有效 Token。验证码有发送冷却、失败次数限制和原子消费。配置及迁移说明见 [安全执行与凭据配置](docs/SECURITY_EXECUTION.md)。
- 下载会校验每次 DNS 结果、拒绝私网/混合地址、限制重定向与大小并原子落盘。部署到不受信网络时仍应使用 HTTPS 和出口代理做最终 egress 控制。
- Android Token 由 Keystore 加密保存，WebSocket 使用 Authorization 头；APK 缓存按服务器、账号、项目和任务隔离，退出时清理。下载校验服务端 SHA-256，安装前展示包名、版本、文件和签名摘要；任务产物不可用时不会退回其他构建。
- 已知限制：密钥自由文本检测非完备；审批超时下限 30s；索引/记忆为单机 SQLite；WS ticket 和速率窗口为单进程内存状态；真实 Gradle 构建依赖本机 SDK/JDK 17；Eval 中构建步骤为 mock。

## 迁移到云服务器

代码不依赖本机账号系统。迁移时复制项目代码，并持久化 `data/`、`workspaces/`、`builds/`。单机 systemd + Caddy、HTTPS、邮箱账号、客户端打包及多实例迁移说明见 [docs/CLOUD_DEPLOY.md](docs/CLOUD_DEPLOY.md)。
