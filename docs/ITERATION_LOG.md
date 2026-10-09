# Android Agent 迭代记录

每轮只推进一项可验证的改进。提交前必须完成仓库发布门禁以及该功能的浏览器、接口或设备验证；失败先修复，未完成的必需验证不视为通过。测试使用隔离数据与本地模型桩。用户要求停止时停止后续开发与推送。

## 双端动态任务表情

日期：2026-10-09。状态：实现、独立审查与完整验收已完成。

Android 聊天标题栏和桌面两处助手入口接入离线 SVG 表情，映射实际任务状态。修复未知状态回退、后台与被遮挡面板仍播放动画、页面恢复及系统减少动画偏好等边界。上游的球形角色仍限个人技术学习研究，保留全部许可证，不能用于商业产品。

验收范围：32 种原始表情动态渲染、任务状态联动、重复状态稳定、前台动画、后台暂停/恢复、DOM 显隐、面板聚焦、减少动画、页面缓存恢复、Android 生命周期和离线本地资源。增加发布门禁中的表情专项、资源同步检查、后端 E2E、真实 Electron 场景和 Android lint；同轮更新 `brace-expansion` 5.0.9 → 5.0.12，修复依赖审计发现的递归与扩展拒绝服务问题。

加强测试时发现暂停恢复会重复追加原始指令，并从头使用响应编号，事件去重掩盖了实际命令重跑。现按 turn 身份恢复提示、响应编号与成功编辑证据；回归测试直接核对实际执行日志由 `1,2,1,2,3` 修复为 `1,2,3`，另覆盖 OpenAI/Anthropic 协议和同文新一轮输入。修正 Electron/E2E 测试使用隔离账号、数据与本地模型桩，并检查真实工具结果；修复远程更新中导致 Android lint 失败的越界布局宽度。

测试环境：Python 3.12.14、Node 24.14.0、Android Studio JBR 17、仓库现有 Android SDK。临时 Python 环境与日志位于忽略目录 `.artifacts/`，不上传凭据、数据库或本地草稿。

最终结果：默认统一发布门禁 **18/18 全通过，无跳过项**，报告 `.artifacts/iteration-001/release.json`。其中后端 566 项测试及 23 项子测试、15 个完整 E2E 场景通过；Android 174 项 JVM 测试、Debug 构建与 lint 通过（0 错误、344 条警告）；桌面单元、表情、Studio、创意接口、截图和两套 Electron 实测通过；npm audit 无漏洞。模拟器另行验证播放、关闭动画、后台静止、返回恢复和退出销毁，证据位于 `.artifacts/iteration-001/android-motion-results.json`。源文件校验和、双端资源同步、暂存区差异格式与敏感信息扫描通过。

下一轮已确认的恢复缺口：worker 每次续跑重置本轮快照、构建与编辑事实，暂停时提前写入终态快照；会影响跨暂停的变更审查与最终验证记录。已消费的 steer 也没有写入规范历史，尚未覆盖真实引导消息的暂停恢复。本轮解决重复执行，不据此宣称恢复链路已全部完成。下一轮先修复这些事实恢复和检查点边界，再补双端恢复入口。

## 下一步候选

以下优先级来自官方资料与本仓库代码缺口的比较，属于本项目的实施选择，不表示复制其他产品的内部实现。每轮开始时重新核实资料和当前代码。

| 顺序 | 改进 | 本项目缺口 | 主要验收 |
| --- | --- | --- | --- |
| 1 | 双端恢复中断任务 | 服务端已有 `/api/jobs/{id}/recover`，客户端没有完整入口 | 中断卡恢复、防双击与双端并发、沿用会话、来源标记、重连与账号隔离、副作用重新审批 |
| 2 | 本轮验证结果卡 | 已有 feedback_runs，交付卡的测试与安装仍固定为未验证 | 构建、单测、APK、安装分别展示；旧报告不得算本轮通过；零测试与取消状态明确 |
| 3 | 引导与追问回执 | 已有消息键与消费时间，客户端目前主要用 Toast | 已接收、待处理、已采用分开；重试去重、服务重启恢复、顺序与账号隔离 |

## 参考依据

- [Codex 平台与开放 Agent harness](https://developers.openai.com/blog/codex-as-a-platform)：宿主负责展示执行事件、审批与结果，支持将现有状态准确传达给用户。
- [Codex 手机工程工作流](https://developers.openai.com/blog/mastering-codex-remote-for-engineering)：移动端围绕任务结果、变更审查和继续反馈组织工作。
- [Claude Code 会话恢复](https://code.claude.com/docs/en/common-workflows#resume-previous-conversations) 与 [checkpointing](https://code.claude.com/docs/en/checkpointing)：会话恢复及检查点为中断后继续工作提供明确入口。
- [Claude Code 验证实践](https://code.claude.com/docs/en/best-practices#give-claude-a-way-to-verify-its-work)：测试输出、命令结果与截图是功能完成的验证依据。
- [Claude Code 消息队列](https://code.claude.com/docs/en/interactive-mode#queue-messages-while-claude-works)：运行中的输入需要可见的排队状态。
- [Cursor 更新记录](https://prod.cursor.com/docs/release-notes)：错误恢复入口、最近一轮审查和追问恢复改进可作为交互参考。
- [brace-expansion 递归漏洞](https://github.com/advisories/GHSA-qhr7-859c-m2p7) 与 [扩展复杂度漏洞](https://github.com/advisories/GHSA-q2hr-2g5m-vwhr)：此次依赖升级的直接依据。
