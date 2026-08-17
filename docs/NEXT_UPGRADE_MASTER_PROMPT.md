# Android Agent 后续升级总计划与完整执行提示词

> 更新日期：2026-08-16
> 项目路径：`/Users/sakura/Android Agent`
> 适用范围：Python/FastAPI Agent 服务、Electron 桌面端、原生 Android 客户端、测试、发布与运维
> 文档性质：可直接交给后续开发 Agent 的主提示词，也是阶段拆分、验收和交付基线

## 1. 使用方法

本文件整合并取代已完成的早期阶段提示词、旧路线图、旧进度快照和旧整改清单。

有两种使用方式：

1. **连续升级**：把本文件全文交给开发 Agent，要求从阶段 0 开始按依赖顺序推进。
2. **单阶段升级**：只复制“总执行约束”以及目标阶段，适合拆成多个独立任务。

不得跳过阶段 0。当前工作区存在大量未提交的桌面端、Android 端、后端和测试改动，后续 Agent 必须先识别这些改动的归属与完成度，不能用 `git reset --hard`、`git checkout --` 或覆盖式生成清除现有成果。

## 2. 当前能力基线

在开始升级前，先通过代码和测试重新验证以下基线，不得只依据本段文字宣布完成：

- FastAPI 服务端具备用户隔离、项目、Conversation、Task、Turn、规范事件、审批、Checkpoint、Diff、构建、APK、诊断和配额接口。
- Agent 支持 OpenAI-compatible/DeepSeek 与 Anthropic 消息投影、工具调用、权限模式、暂停、继续、停止、显式恢复和持久 Worker。
- 工具、Terminal、MCP、Hooks、Rules、Skills、Subagent、Worktree、Memory 已有安全边界和离线测试。
- Electron 桌面端具备 Monaco 编辑器、项目与会话、Agent 时间线、审批、Diff、Terminal、任务控制和安全凭证存储。
- Android 原生客户端正在从旧的单 TextView 对话页升级为多页面、多 View Type 的 Agent 控制端。
- 桌面端与 Android 端已有统一 UI/UX 基线：`docs/DESKTOP_ANDROID_UI_DESIGN_SYSTEM_AND_PROMPTS.md`。
- 当前已修复历史工具消息中 `tool_calls` 与 `tool_result` 被用户消息隔开的 400 协议错误；后续重构不得破坏该不变量。
- 桌面端任务运行时应直接提供暂停/继续/停止，连接状态文字必须与真实状态一致。
- 同一账号允许桌面端与 Android 端使用不同 Token；新增凭证不能使另一端掉线。

仍需重点升级的方向：

- 将当前大批未提交功能收敛为可审查、可复现的稳定基线。
- 统一跨端 API、状态机、错误模型和事件兼容策略。
- 完成 Android 端生产级 Agent 体验与后台可靠性。
- 提升桌面端任务控制、可访问性、性能和 Electron 安全边界。
- 将单进程内存治理升级为可选多实例一致治理。
- 建立可量化的上下文摘要质量、真实工作流 Eval 和性能预算。
- 建立签名、SBOM、provenance、灰度发布、升级和回滚闭环。

## 3. 总执行提示词

以下内容可直接复制给后续开发 Agent：

```text
你是一名资深 Agent 平台架构师、Python/FastAPI 工程师、Electron 工程师、Android 工程师、
安全工程师、测试工程师和发布工程师。你需要在现有 Android Agent 项目中直接完成后续升级，
而不是只给建议、伪代码或新的空壳目录。

项目路径：/Users/sakura/Android Agent

一、开始前必须阅读

1. README.md
2. MVP_SPEC.md
3. docs/ARCHITECTURE.md
4. docs/DESKTOP_ANDROID_UI_DESIGN_SYSTEM_AND_PROMPTS.md
5. docs/NEXT_UPGRADE_MASTER_PROMPT.md
6. config.yaml.example、requirements.lock、desktop/package.json、Android Gradle 配置
7. 与本阶段直接相关的实现和测试；不要只读文档

二、工作区保护

1. 先执行 git status --short、git diff --stat、git diff --check。
2. 当前工作区可能有其他任务留下的已修改和未跟踪文件；这些改动属于用户，必须保留。
3. 不得使用 git reset --hard、git clean -fd、git checkout --、覆盖式代码生成或批量格式化清除改动。
4. 修改前检查文件现状和 diff；只改本阶段范围，避免顺手重写无关模块。
5. 不修改或输出真实 API Key、Token、签名私钥、keystore、系统凭证和用户数据。
6. 不提交、不推送、不创建 PR，除非用户明确要求。

三、实施原则

1. 直接实现代码、迁移、测试和文档，不停留在方案层。
2. conversation_events 是 Conversation 历史权威来源；兼容投影不能反向成为第二事实源。
3. Task、Turn、审批和工具事件必须有稳定 ID、明确状态机和幂等语义。
4. 任意 assistant.tool_calls 后必须立即出现所有对应 tool/tool_result 消息，再出现新 user 消息。
5. 原始事件不能因为压缩、摘要或迁移被静默删除。
6. 所有高风险工具继续经过 schema、权限、审批、路径边界和取消检查。
7. 不为了通过测试关闭认证、沙箱、签名、证书校验或错误检查。
8. 不通过放宽断言、无限增加 timeout、隐藏失败状态或盲目更新截图掩盖回归。
9. 不访问真实付费模型或真实网络，除非用户明确授权；默认使用 fake/stub/fixture。
10. 桌面端和 Android 端共享协议语义，但遵循各自平台的信息架构，不能机械复制布局。

四、执行顺序

严格按阶段 0 → 8 推进。每阶段先建立可失败的回归测试，再实现，再运行该阶段与全局门禁。
发现前置阶段未完成时，先修前置问题，不在错误基线上叠加新功能。

五、每阶段必须交付

1. 根因或当前差距。
2. 实际修改文件及职责。
3. 数据库/API/状态机兼容说明。
4. 新增测试及其覆盖的失败模式。
5. 实际运行命令、退出码、通过数量和未运行项原因。
6. 安全、性能、可访问性与回滚影响。
7. 剩余风险和下一阶段入口条件。

六、终止条件

只有当代码、迁移、测试、构建、视觉检查、文档和回滚说明均完成时，才能宣告相应阶段完成。
如果依赖外部签名证书、设备、网络或用户选择，应完成所有可离线部分并准确列出阻塞项，
不得伪造通过结果。
```

## 4. 阶段 0：工作区收敛与可信基线

### 目标

把当前大量跨端改动整理成可理解、可测试、可继续升级的基线。本阶段不增加产品功能。

### 必做项

1. 生成变更清单，按以下来源分类：
   - 后端协议、Worker、上下文和凭证。
   - Electron UI、状态、任务控制和测试。
   - Android 导航、时间线、审批、设置、资源和测试。
   - 截图、构建产物、审计产物、缓存和临时文件。
2. 判断所有未跟踪文件属于：应纳入源码、应纳入测试基线、应加入 `.gitignore`、可安全删除。
3. 禁止删除无法确认来源的用户文件；对生成型产物只清理明确可再生且未被测试引用的内容。
4. 运行全量离线验证，记录首次真实失败，不先改断言：

```bash
python3 -m unittest discover -s tests -v
cd desktop && npm run check && npm run test:unit && npm run test:screenshot
cd ../android-app && ./gradlew testDebugUnitTest assembleDebug --offline
cd .. && python3 scripts/release_check.py
git diff --check
```

5. Electron 端到端测试使用不冲突端口，并确保清理子进程：

```bash
cd desktop
AGENT_SMOKE_PORT=18123 AGENT_SMOKE_STUB_PORT=19477 \
  node tests/electron-smoke.test.js
```

6. 对截图做人工视觉检查，不能只根据脚本退出码判断。
7. 输出一份“当前可运行能力矩阵”和“已知失败矩阵”，作为阶段 1 输入。

### 完成标准

- 现有改动没有丢失。
- 所有源码和生成物归属明确。
- 测试结果可在干净临时数据目录复现。
- 没有残留 8000/8123/9477/18123/19477 端口进程或后台 Worker。
- 文档不再声称已完成实际未通过的测试。

## 5. 阶段 1：核心协议、状态机与任务控制稳定化

### 目标

消除跨 Provider、跨重启、跨端重放时的上下文和任务状态不一致，确保暂停、继续、停止始终真实有效。

### 实施要求

1. 为 OpenAI-compatible 与 Anthropic 建立同一组协议不变量测试：
   - 工具调用和结果成组、顺序稳定、不重复。
   - 用户中途消息不能把工具调用与结果隔开。
   - 部分工具结果、失败结果、服务中断和恢复具有明确策略。
   - 当前用户 Prompt 不重复。
2. 从真实历史事件构造至少一个曾触发 400 的匿名化 fixture，stub 服务必须像真实 Provider 一样拒绝非法历史。
3. 明确定义 Task 状态：
   - `queued`
   - `running`
   - `awaiting_approval`
   - `paused`
   - `cancel_requested`
   - `succeeded`
   - `failed`
   - `canceled`
   - `interrupted`
4. 后端状态、WebSocket/poll、Desktop、Android 使用同一状态语义和中文文案映射。
5. 暂停和停止必须是服务端权威操作：
   - 运行中暂停在安全检查点确认。
   - 已暂停才能继续。
   - 停止对排队、运行、等待审批和暂停均有效。
   - 重复操作幂等。
6. Desktop：
   - 顶部连接状态、Agent 面板状态和状态栏一致。
   - 活动任务直接展示暂停/继续/停止，不埋在二级菜单。
   - Composer 在活动任务中仍能发送 steer/follow-up，并保留停止入口。
7. Android：
   - Conversation 页提供同语义的任务控制。
   - 后台/重连后从服务端恢复真实状态，不凭本地按钮状态猜测。
8. 连接凭证：
   - 同一用户支持多设备独立 Token。
   - 新增 Token 不撤销旧设备。
   - Desktop 使用系统安全存储，Android 使用 Keystore。
   - 明文临时 Token 在完成配置后立即删除。

### 测试

- Provider 消息重建单测和真实 DB fixture 回归。
- Worker pause/resume/cancel 故障注入。
- Desktop 运行、暂停、停止、断线、重连截图和 E2E。
- Android 状态 normalizer 与生命周期重连测试。
- 多 Token 同用户与跨用户隔离测试。

### 完成标准

- Stub Provider 不再发现悬空工具调用。
- 活动任务一定有可发现的停止入口。
- 服务重启后不会自动重放有副作用工具。
- 两端状态在同一服务端快照下完全一致。

## 6. 阶段 2：单一跨端 API 契约与兼容治理

### 目标

结束 Desktop、Android、Web 和后端各自猜字段的状态，建立可演进、可验证的协议。

### 实施要求

1. 以 FastAPI OpenAPI 和规范事件 schema 为权威契约。
2. 对公开请求启用严格字段校验，未知字段默认拒绝；兼容字段必须有版本和弃用周期。
3. 统一：
   - 错误 envelope、错误 code、可重试标记和用户可见文案。
   - 分页 cursor、排序、时间戳和空值语义。
   - Task/Turn/Event/Approval/Artifact DTO。
   - HTTP 与 WebSocket 的事件字段。
4. 为 Desktop 建立生成或验证型 TypeScript contract；为 Android 建立 Kotlin DTO/fixture 校验。
5. 每个公开端点至少有一份三端共享 fixture，覆盖成功、401、404、409、422、429、5xx。
6. 规范事件增加显式 `schema_version`；旧事件迁移/投影必须幂等。
7. API 返回资源 ID/URL，不泄露宿主机绝对路径、claim token、内部连接对象或原始异常。
8. 建立契约漂移 CI：OpenAPI 或 fixture 改变时，客户端测试必须同步。

### 完成标准

- 不再出现客户端发送 `message_key` 等服务端忽略字段的静默漂移。
- Desktop、Android 对同一 fixture 产生相同领域状态。
- 所有弃用字段有删除日期、迁移方式和回滚策略。

## 7. 阶段 3：Android 端生产级远程 Agent

### 目标

完成原生 Android 客户端的主路径，使手机端适合审批、追踪、轻量引导、下载和安装产物，而不是桌面 UI 的缩小版。

### 设计基线

严格遵循 `docs/DESKTOP_ANDROID_UI_DESIGN_SYSTEM_AND_PROMPTS.md` 的 Android 信息架构、状态、颜色、间距、字体、无障碍和页面清单。

### 实施要求

1. 根级导航至少包含 Projects、Activity、Approvals；大屏使用 Navigation Rail/双栏。
2. Connection Settings：
   - 配对、Token 状态、服务地址、TLS 状态和连接诊断清晰。
   - Release 禁止 cleartext；Debug 明文策略与 Release 隔离。
   - 凭证不进入备份、日志、Intent、剪贴板历史或截图。
3. Conversation：
   - RecyclerView/ListAdapter 多 View Type。
   - Turn、assistant、user、status、tool group、approval、changes、error、artifact 分层。
   - 历史事件和实时事件通过稳定 ID 去重、合并和排序。
   - 工具过程默认折叠，失败和待审批自动展开。
   - Markdown、表格、代码块、复制、长行横向滚动可用。
4. 滚动：
   - 仅当用户接近底部时自动跟随。
   - 用户阅读历史时显示“回到最新”。
   - 分页加载不跳动，不丢当前位置。
5. 审批 Inbox：
   - 展示人类可读命令、路径、域名、风险和作用范围。
   - 支持允许一次、拒绝、始终允许；高风险操作不得默认永久允许。
6. 任务控制与输入：暂停、继续、停止、steer、follow-up 的状态和错误可见。
7. Background：
   - WebSocket 断线退避重连，必要时回退 polling。
   - 待审批、任务完成、失败通过 Notification Channel 提醒。
   - 点击通知回到准确 Project/Conversation/Task。
8. Diff、Build Log、APK：
   - 大文件增量加载。
   - APK 校验 size、SHA-256、包名、版本和签名摘要后才能安装。
   - Android 版本限制和未知来源安装流程有明确提示。
9. 可访问性：TalkBack、48dp 目标、大字体、暗色、横屏、360dp 和 600dp+。

### 测试与验收

- Timeline normalization、Diff、Approval、状态控制单测。
- ActivityScenario/生命周期恢复测试。
- 360dp、字体 1.3x、深色、平板截图。
- `testDebugUnitTest`、`assembleDebug --offline` 通过。
- 有设备时运行 connected smoke；没有设备时明确标注未运行。

## 8. 阶段 4：Desktop Agent IDE 体验、安全与性能

### 目标

把桌面端收敛为高密度、稳定、可键盘操作的 Agent 工作台。

### 实施要求

1. 遵循统一设计系统，不引入第二套 token、圆角或状态颜色。
2. 信息架构：Activity Bar、Project/Conversation、Editor、Agent Thread、Panel、Status Bar 职责清晰。
3. Agent Thread：
   - 状态和任务控制始终可发现。
   - 多 Turn 折叠保持手动展开状态。
   - 流式 delta 只形成一个 assistant bubble。
   - Approval、Changes、Error 和 Final 不互相覆盖。
4. Diff/Review：
   - Monaco model 生命周期正确，无泄漏。
   - 多文件切换、添加/删除文件、超大 diff 和二进制文件有明确降级。
   - 恢复 checkpoint 前显示影响范围和冲突。
5. Composer：
   - 任务中 steer/follow-up 模式明确。
   - 文件/目录/选区上下文可见且可移除。
   - 连接断开时保留草稿，不误发。
6. Electron 安全：
   - `contextIsolation`、sandbox、CSP、导航和 `window.open` 限制保持开启。
   - Renderer 不能凭绝对路径任意读写；通过 main process capability 操作已授权 workspace。
   - 安全凭证写系统存储；失败时明确告知只在当前会话使用。
7. 性能：
   - 1,000+ 时间线项虚拟化或增量渲染。
   - 大日志、大 Markdown、大工具输出不阻塞主线程。
   - 轮询、审批同步和错误 toast 有节流。
8. 可访问性：完整键盘流、焦点环、ARIA live、对比度、缩放、窄屏 focus switch。

### 测试与验收

- Node 单测覆盖状态机、normalizer、render reconciliation 和 API contract。
- Screenshot 测试覆盖空闲、运行、暂停、停止中、失败、审批、Diff、长输出、窄屏。
- Electron E2E 覆盖配对、重启恢复、任务控制、审批、Diff、并发会话和资源清理。
- 视觉基线改变必须附带人工检查结论，不盲目覆盖全部 PNG。

## 9. 阶段 5：多实例一致治理与云部署选项

### 目标

保留当前单机 SQLite 的简单部署，同时提供可选的多实例一致实现。

### 实施要求

1. 抽象并实现可替换存储接口：
   - User/Token。
   - Task lease/fencing。
   - WS ticket。
   - Rate limit/quota。
   - Event/outbox。
2. 单机默认仍可使用 SQLite；多实例推荐 PostgreSQL，短期原子 ticket/限流可使用 Redis。
3. 所有 ticket 一次消费、TTL、用户/资源绑定必须跨进程原子。
4. Task claim、heartbeat、pause、resume、cancel 使用数据库服务端时间和 fencing。
5. 规范事件、Task/Turn 状态和 outbox 在一个事务边界提交；通知由 outbox 异步投递。
6. APK、构建日志和大产物支持本地文件系统与对象存储适配器。
7. API 保持兼容；部署模式通过配置选择，启动时验证不完整配置并失败关闭。
8. 数据迁移：
   - SQLite → PostgreSQL dry-run、校验、正式迁移、回滚。
   - 事件数量、hash、用户/项目归属和产物摘要核对。
9. 增加两个 API 实例、两个 Worker 的并发和故障注入测试。

### 完成标准

- 同一 ticket 不能被两个实例消费。
- 慢任务不会因 lease 误判被重复执行。
- 单机用户无需 Redis/PostgreSQL 仍可正常运行。
- 云部署有 TLS、反向代理、持久卷、备份和恢复说明。

## 10. 阶段 6：上下文质量、Memory 与 Eval 闭环

### 目标

从“格式合法”升级到“长期任务事实可靠、质量可量化”。

### 实施要求

1. 保留 deterministic checkpoint 作为可靠基线。
2. 可选模型摘要必须输出 provider-independent 结构：
   - goal
   - constraints
   - decisions
   - unresolved
   - files
   - tests
   - tool_facts
   - errors
3. 每项事实引用 `source_seq`；validator 校验工具 ID、文件、状态和覆盖范围。
4. 验证失败追加 invalidated 事件并回退原始历史，不覆盖旧 checkpoint。
5. Memory 明确 user/project/local scope、来源、置信度、冲突和批准状态。
6. 构建 Eval 数据集：
   - 100+ 轮长历史。
   - 并行与失败工具。
   - 中途用户消息。
   - 多 Provider 投影。
   - 暂停/恢复/审批。
   - 跨项目隔离。
7. 指标至少包含：
   - 约束召回率。
   - 工具链完整率。
   - 事实幻觉率。
   - 未解决事项保留率。
   - token 节省率。
   - 首 token 与整轮延迟。
8. 默认离线 fake；真实 Provider Eval 必须显式开关、预算上限、脱敏数据和结果隔离。

### 完成标准

- 摘要不是“看起来更短”就通过。
- OpenAI-compatible 与 Anthropic 投影在同一事实集上结果一致。
- 任意损坏 checkpoint 都能安全回退。

## 11. 阶段 7：安全、可观测性与性能预算

### 安全

1. 建立 Token 列表、设备标识、签发时间、最后使用、单设备撤销和轮换；数据库只存 hash。
2. Android Release 禁止 cleartext，生产部署强制 HTTPS；证书异常不给“继续忽略”。
3. Electron IPC capability、路径、外部导航、更新源和签名重新审计。
4. 命令、Terminal、MCP、下载和 APK 安装继续执行最小权限与完整审计。
5. 日志和 diagnostics 自动脱敏 Token、Authorization、环境变量、主机路径和用户输入中的疑似密钥。
6. 运行 SAST、依赖漏洞、secret scan、SBOM 和锁文件漂移门禁。

### 可观测性

1. 定义 request/task/turn/tool/approval correlation ID。
2. 结构化日志、指标和 trace 不记录凭证与完整敏感 Prompt。
3. 指标：队列时间、模型延迟、工具延迟、审批等待、失败率、重连率、取消延迟、事件积压、资源用量。
4. diagnostics 有明确严重性、组件、用户可见摘要和运维详情分层。
5. 建立健康检查、就绪检查和依赖降级语义。

### 性能预算

1. API p95、事件重放、1,000/10,000 条时间线、日志和 Diff 建立预算。
2. SQLite/PostgreSQL 查询索引通过真实查询计划验证。
3. Worker、Terminal、MCP 和 Electron renderer 无线程、进程、fd、Monaco model 泄漏。
4. Android 冷启动、内存、滚动帧率和后台耗电有基线。

### 完成标准

- 故障能够从用户提示追踪到 request/task/turn/tool。
- 任何诊断导出不包含可用凭证。
- 性能回归超过预算会在 CI 失败或要求显式批准。

## 12. 阶段 8：可信发布、灰度升级与最终交付

### 目标

形成可重复、可签名、可验证、可回滚的发布链路。

### 实施要求

1. CI 固定使用：
   - `pip --require-hashes`
   - `npm ci`
   - Gradle dependency locking/verification
   - 离线二次构建或缓存完整性检查
2. Android：
   - 受保护 Secret 注入 release keystore。
   - 校验 APK/AAB 包名、版本、证书、SHA-256、权限清单。
   - Debug 与 Release applicationId/网络策略/日志策略清晰隔离。
3. macOS：
   - Developer ID 签名、Hardened Runtime、notarization、staple 验证。
   - 自动更新只允许 HTTPS 和签名元数据。
4. 产物：
   - CycloneDX/SPDX SBOM。
   - SHA-256 checksum。
   - provenance/构建环境/源码 commit。
   - 版本化 release manifest。
5. 发布策略：内部 → 小比例灰度 → 全量；每阶段有健康阈值和暂停条件。
6. 数据库 migration 必须向前兼容上一客户端版本，提供备份、dry-run 和回滚。
7. 编写 Token/签名密钥轮换、坏版本撤回、服务降级和数据恢复 Runbook。
8. 最终发布门禁不得修改 tracked fixture；报告写入独立 artifacts 目录。

### 最终验证矩阵

```bash
# Backend
python3 -m pip install --require-hashes -r requirements.lock
python3 -m unittest discover -s tests -v

# Desktop
cd desktop
npm ci
npm run check
npm run test:unit
npm run test:screenshot
AGENT_SMOKE_PORT=18123 AGENT_SMOKE_STUB_PORT=19477 node tests/electron-smoke.test.js

# Android
cd ../android-app
./gradlew testDebugUnitTest assembleDebug --offline

# Repository gates
cd ..
python3 scripts/scan_secrets.py
python3 scripts/release_check.py
git diff --check
git status --short
```

有签名环境时额外执行 Release 构建、APK 证书检查、macOS codesign/notarization 和更新验证。

## 13. 跨阶段验收清单

### 功能

- Desktop 和 Android 均能配对、选择项目/会话、发起任务、查看流式输出、审批、暂停、继续、停止。
- 历史重放与实时事件无重复、无丢失、顺序稳定。
- Diff、构建日志、APK 和恢复入口真实可用。
- 断线、重启和 Provider 失败后状态诚实，不伪装成功。

### 正确性

- 不存在悬空 `tool_calls`。
- Task、Turn、terminal event 状态可对账。
- 有副作用工具不会因重试、恢复或多 Worker 重复执行。
- 摘要和 Memory 不跨用户/项目泄漏。

### 安全

- 所有 API 和 WebSocket 需要正确认证。
- Token 不出现在 URL、日志、截图、localStorage、普通 SharedPreferences 或备份。
- 文件、命令、下载、MCP、Worktree、Terminal 无路径/权限旁路。
- Release 产物签名和摘要可验证。

### UX 与可访问性

- 所有状态同时有文字/图标，不只依赖颜色。
- Desktop 键盘完整可用；Android TalkBack 和大字体可用。
- 运行任务的停止入口无需打开二级菜单。
- 失败提供原因、可恢复动作和诊断 ID。

### 工程质量

- 测试不访问真实模型/网络，除非显式 opt-in。
- 无残留后台进程、线程、端口、临时 Token、半文件和 Monaco model。
- 文档、OpenAPI、客户端 DTO 和实际实现一致。
- Git 差异只包含本阶段授权范围。

## 14. 阶段交付报告模板

后续 Agent 每完成一个阶段，必须按以下格式报告：

```text
阶段：
状态：完成 / 部分完成 / 阻塞

结果：
- 用户可感知变化
- 核心技术变化

根因或差距：
- ...

修改文件：
- path：职责

协议与数据兼容：
- API：
- DB migration：
- Desktop：
- Android：
- 回滚：

验证：
- 命令：
- 退出码：
- 通过数量：
- 视觉/设备检查：

安全与性能：
- ...

未完成与风险：
- ...

下一阶段入口条件：
- ...
```

## 15. 文档维护规则

1. `README.md` 只保留安装、运行、验证、能力入口和已知限制。
2. `MVP_SPEC.md` 保留产品边界与最初成功标准，不作为实时进度表。
3. `docs/ARCHITECTURE.md` 描述当前实际架构，代码改变后同步。
4. `docs/DESKTOP_ANDROID_UI_DESIGN_SYSTEM_AND_PROMPTS.md` 是双端 UI/UX 设计与验收基线。
5. 本文件是唯一后续升级路线与执行提示词，不再新增 `1.md`、`阶段N.md` 或重复 Roadmap。
6. 临时审计结果进入 `.artifacts/` 或任务报告，不在根目录永久堆积。
7. 任何“已完成”必须附实际测试证据和日期；过期进度快照应更新或删除。
