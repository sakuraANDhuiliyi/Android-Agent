# History / Background Tasks / Subagents / Terminal V2

## Checkpoint 与恢复

主 Agent 每轮已有 before_turn / after_turn 快照。Conversation 的 Changes 现在提供按轮 Review 和 Revert turn；历史 Diff 不再误显示整个当前工作区，并禁用直接 Hunk 回退。Revert turn 在后续代码发生冲突时拒绝撤销，且操作前保存 manual 恢复点。

同一 checkpoint 幂等键的首次快照不可被重试覆盖，避免任务重放替换 before_turn 基线。

Project → History 展示已完成快照的时间、任务标题和本轮修改文件数（不是快照总文件数）。条目支持 View changes、Restore、Create branch。手工恢复点没有所属 Turn 时，View changes 比较该快照与当前工作区。

Restore 先展示当前文件到目标快照的变更清单，确认提交必须携带预览时的 revision。代码变化则拒绝提交；活动任务和运行中的终端也会阻止恢复。所有目标路径、快照 blob 与文件类型在写入前检查，拒绝符号链接。恢复前自动创建 manual 快照，可从 History 恢复回去。Conversation、消息和事件记录不会被删除。

边界：快照只覆盖现有 Agent 可写范围内的源文件/配置，不是整盘备份；构建产物、依赖缓存、未纳管文件不在恢复范围内。恢复改变工作目录文件，不切换 Git HEAD，也不重置暂存区。磁盘 I/O 异常仍可能造成部分写入；恢复前快照保留在 History 中。

Create branch 需要 Git-backed checkpoint。后端用私有临时 Git index，将受管快照叠加到该快照的 base revision 上创建 commit 和新 ref，不 checkout、不修改当前 index、不覆盖同名分支。未纳管路径沿用 base revision。用户自行输入分支名。

## Task Center 与后台同步

全局 Tasks 按 Running、Waiting（Approval / Paused）、Queued、Completed、Failed 展示。点击任务可打开所属 Conversation/Approval、Review 或 Agents 详情。显示项目、角色和运行耗时。

TaskRepository 持久化最近服务器任务快照、已观察状态和通知去重标记，缓存按服务器/用户/凭证指纹隔离。Task Center 离线可显示缓存。切换账号清除旧通知，旧 Worker 和旧审批 action 的身份指纹不匹配时不会继续操作。

WorkManager 2.10.1 负责联网约束、进程重启后的同步和重试。任务提交时启动一次性同步，进行短时轮询后交还 WorkManager 重试；另有 15 分钟周期兜底。Conversation 的 JobWatcher 仅用于前台实时展示，不再负责通知。服务器才是 Agent 执行者，退出 Activity 不会取消服务器任务。

完成通知带 Review；每个 approval 使用独立通知及 Deny / Allow action，action 交给持久化 Worker，只处理同一账号下仍 pending 的指定审批。Allow 在支持的平台上要求解锁。服务器同时检查 approval 与 job 的绑定，不能用另一个 job 的 URL 决定审批。

限制：这是可靠的最终状态同步，不是实时推送。Android 的 Doze、省电、网络校验和厂商限制可能延迟执行；用户强制停止 App 后须再次打开才能恢复正常调度。没有加入 FCM 或无限前台服务。周期任务最短间隔依据 [Android WorkManager 文档](https://developer.android.com/reference/androidx/work/PeriodicWorkRequest)，不能承诺锁屏后即时通知。已有服务端审批等待超时仍然适用；本轮未重做服务端审批持久化机制。

## Subagent 汇总

主 Conversation 的 Subagent 事件聚合为一张 “N agents worked” 卡片，保留角色和完成信息，不复制子对话。View details 从真实任务列表读取父/子任务状态、结果和错误，并可打开各自 Conversation。仅收到启动事件时不假装知道最终状态，提示进入详情查看。Task Center 同样提供 Agents 入口。

本轮提供已有 Subagent 能力的展示与导航，不新增自动分工策略或自动合并工作树决策。

启动/完成事件同时写入规范对话事件流，重开对话可重建汇总；事件不作为模型上下文注入，迟到的启动事件也不会覆盖已完成状态。

## Remote Terminal

Android Project → Terminal：Sessions / New、多 session 重连、命令输入、历史命令（选择仅填入，不立即执行）、清除历史、Copy、Stop（Ctrl-C）、Close session、选区/最近输出发送给 Agent。命令栏和四个操作固定在底部，输出可滚动和选择。输出按 seq 增量同步，断开页面后服务器 session 继续；重开可回放服务器保留的输出。

手机使用有界文本 transcript：处理跨分片 ANSI 控制串、退格等常见输出；不实现全屏 vim/top 等终端程序。输出最多保留 60k 字符，Agent 上下文最多 24k；命令历史最近 80 条按身份和项目隔离。服务器的已有环形缓冲、空闲超时和重启中断规则继续适用。

Desktop 保留 xterm PTY，增加已有 session 重连、Ctrl-C、Copy、Ask Agent；不同项目和身份的 session 不混用。Shell 自身提供方向键命令历史。Ask Agent 将选区或最近输出加入 Composer Context，保留已有草稿，不自动发送。

终端默认关闭，需要服务端配置 `terminal_enabled: true`。命令是用户直接执行，不经过 Agent 工具审批；原有后端访问控制与输入限制仍适用。后端与 APK 必须一起升级。

## 新增 API

- `GET /api/projects/{id}/history`
- `GET /api/projects/{id}/checkpoints/{checkpoint}/preview`
- `POST /api/projects/{id}/checkpoints/{checkpoint}/restore-snapshot`：`expected_revision`
- `POST /api/projects/{id}/checkpoints/{checkpoint}/branch`：`name`
- `GET /api/terminals/{id}/output?after_seq=0`：`terminal`、`chunks`、`next_seq`

保留旧 checkpoint restore 接口，增加活动任务/终端保护与恢复前备份。OpenAPI 已更新到 104 paths。终端重启恢复从 app factory 移到 ASGI lifespan，生成契约不会再把现有 session 标为中断。

## 验证

- Python：History、Terminal、Workspace、Feedback、Conversation Integration、Run Mode Approvals、API Contract 回归。
- Android：85 项 JVM 单测和 assembleDebug；新增跨分片 ANSI、输出上限、Subagent 汇总、revision/cursor 请求及任务身份解析测试。
- Desktop：语法检查与既有 unit suites。
- 隔离 Android API 31 模拟器：History / Revert turn 产生恢复前备份且对话保留；后台进程退出后由 JobScheduler 唤醒 WorkManager 发出带 Review 的完成通知；通知 Deny 确实令服务端审批变为 rejected。为了可重复验证，测试主动触发系统调度，不作为实际 Doze 通知时延承诺。
- 模拟器 Remote Terminal 执行命令并读取真实输出，Ask Agent 将输出带入新 Conversation 的 Context Chip，未自动提交消息。

依赖锁和 WorkManager 新依赖 SHA-256 验证元数据已更新，未关闭依赖验证。后续 [Workbench UI V3](WORKBENCH_UI_V3.md) 已补齐 Lint 工具链依赖校验项并修复阻断错误，`lintDebug` 通过；JVM 单测和静态 UI 夹具仍不等同于完整在线 Android 仪器测试。
