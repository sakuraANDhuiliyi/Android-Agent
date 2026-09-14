package com.androidagent.client.theokit

import android.content.Context
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout

/**
 * TheokitCatalog — registry of all 108 migrated components with demo data,
 * mirroring the desktop showcase. Used by TheokitShowcaseActivity and unit
 * tests to guarantee full-family coverage.
 */
data class TheoComponent(
    val name: String,
    val family: String,
    val description: String,
    val build: (Context) -> View,
)

object TheokitCatalog {

    const val FAMILY_BUTTONS = "Buttons"
    const val FAMILY_AGENT = "Agent status & events"
    const val FAMILY_CHAT = "Chat & message pipeline"
    const val FAMILY_COMPOSER = "Composer"
    const val FAMILY_PROMPTS = "Prompts"
    const val FAMILY_APPROVAL = "Approvals & permissions"
    const val FAMILY_TOOLS = "Tools & diff"
    const val FAMILY_PLAN = "Plans, progress & logs"
    const val FAMILY_SESSION = "Session & context"
    const val FAMILY_MODEL = "Models & usage"
    const val FAMILY_INFRA = "Infrastructure"
    const val FAMILY_RULES = "Rules, skills & memory"
    const val FAMILY_PANELS = "Panels & viewers"
    const val FAMILY_SLIDES = "Editors & slides"

    val FAMILIES = listOf(
        FAMILY_BUTTONS, FAMILY_AGENT, FAMILY_CHAT, FAMILY_COMPOSER, FAMILY_PROMPTS,
        FAMILY_APPROVAL, FAMILY_TOOLS, FAMILY_PLAN, FAMILY_SESSION, FAMILY_MODEL,
        FAMILY_INFRA, FAMILY_RULES, FAMILY_PANELS, FAMILY_SLIDES,
    )

    private val SAMPLE_DIFF = """
        @@ -1,5 +1,7 @@
         import os
        -def main():
        -    print("hi")
        +def main() -> int:
        +    print("hello, theo")
        +    return 0
         if __name__ == "__main__":
             main()
    """.trimIndent()

    private val SAMPLE_DECK = """
        # Violet Forge
        ## TheoKit on Android
        - 108 components
        - Native Kotlin views
        - Token-driven theming
        ---
        # Design tokens
        ## Light & dark parity
        - oklch palette mapped to ARGB
        - 4dp spacing scale
    """.trimIndent()

    val COMPONENTS: List<TheoComponent> = buildList {
        // ── Buttons ──────────────────────────────────────────────────
        add(TheoComponent("Button", FAMILY_BUTTONS, "5 variants") { ctx ->
            val row = ctx.theoRow(gap = 2f)
            row.addView(ctx.theoButton("Primary", TheoButtonVariant.PRIMARY, iconName = "zap"))
            row.addView(ctx.theoButton("Secondary", TheoButtonVariant.SECONDARY))
            row.addView(ctx.theoButton("Outline", TheoButtonVariant.OUTLINE))
            row.addView(ctx.theoButton("Ghost", TheoButtonVariant.GHOST))
            row.addView(ctx.theoButton("Danger", TheoButtonVariant.DESTRUCTIVE, iconName = "trash"))
            val scroll = android.widget.HorizontalScrollView(ctx)
            scroll.isHorizontalScrollBarEnabled = false
            scroll.addView(row)
            scroll
        })

        // ── Agent status & events ────────────────────────────────────
        add(TheoComponent("AgentErrorCard", FAMILY_AGENT, "error envelope") { ctx ->
            ctx.theoAgentErrorCard(
                title = "Tool error",
                detail = "E_TOOL_TIMEOUT: terminal exceeded 120s budget",
                kind = "timeout",
                timestamp = "14:32:08",
                actions = listOf("Retry" to TheoButtonVariant.SECONDARY, "Skip" to TheoButtonVariant.GHOST),
            )
        })
        add(TheoComponent("AgentEvent", FAMILY_AGENT, "timeline event row") { ctx ->
            ctx.theoAgentEvent("npm run build", TheoEventStatus.SUCCESS, "terminal", "exit 0 · 12.4s", "14:31")
        })
        add(TheoComponent("AgentHandoff", FAMILY_AGENT, "agent → agent") { ctx ->
            ctx.theoAgentHandoff("planner", "coder", "plan ready, dispatching implementation")
        })
        add(TheoComponent("AgentProfile", FAMILY_AGENT, "identity card") { ctx ->
            ctx.theoAgentProfile("Theo", "agent", "theo-v2 · 200k ctx")
        })
        add(TheoComponent("AgentStartingState", FAMILY_AGENT, "empty/start state") { ctx ->
            ctx.theoAgentStartingState("Ready to build", "Describe a task to get started", listOf("Loading workspace", "Indexing files"))
        })
        add(TheoComponent("AgentStream", FAMILY_AGENT, "streaming text") { ctx ->
            ctx.theoAgentStream("Analyzing the workspace layout…")
        })
        add(TheoComponent("AgentStreaming", FAMILY_AGENT, "thinking indicator") { ctx -> ctx.theoAgentStreaming() })
        add(TheoComponent("AgentTimeline", FAMILY_AGENT, "event rail") { ctx ->
            ctx.theoAgentTimeline(
                listOf(
                    TheoTimelineEntry("read src/main.py", TheoEventStatus.SUCCESS, "file-search", detail = "2.1 KB"),
                    TheoTimelineEntry("run pytest", TheoEventStatus.RUNNING, "terminal", detail = "12 passed"),
                    TheoTimelineEntry("grep TODO", TheoEventStatus.PENDING, "search"),
                ),
            )
        })
        add(TheoComponent("AgentToolRenderer", FAMILY_AGENT, "tool invocation") { ctx ->
            ctx.theoAgentToolRenderer("write_file", "path: src/ui.py\ncontent: …")
        })
        add(TheoComponent("CapabilityIndicator", FAMILY_AGENT, "enabled caps") { ctx ->
            ctx.theoCapabilityIndicator(listOf("shell" to true, "browser" to true, "network" to false))
        })
        add(TheoComponent("RunStats", FAMILY_AGENT, "duration/tokens/files") { ctx ->
            ctx.theoRunStats(duration = "2m 41s", tokens = "18.2k", filesChanged = 7)
        })
        add(TheoComponent("RunStatusPill", FAMILY_AGENT, "status badge") { ctx ->
            val row = ctx.theoRow(gap = 2f)
            TheoRunStatus.entries.forEach { row.addView(ctx.theoRunStatusPill(it)) }
            val scroll = android.widget.HorizontalScrollView(ctx)
            scroll.isHorizontalScrollBarEnabled = false
            scroll.addView(row)
            scroll
        })
        add(TheoComponent("SubAgentDispatch", FAMILY_AGENT, "sub-agent task") { ctx ->
            ctx.theoSubAgentDispatch("researcher", "gather API references", TheoRunStatus.RUNNING)
        })

        // ── Chat & message pipeline ──────────────────────────────────
        add(TheoComponent("BranchIndicator", FAMILY_CHAT, "branch counter") { ctx -> ctx.theoBranchIndicator(5, 2) })
        add(TheoComponent("ChatMessage", FAMILY_CHAT, "user message") { ctx ->
            ctx.theoChatMessageRoot("user", ctx.theoChatMessageContent("把 README 翻译成英文并保留结构。"))
        })
        add(TheoComponent("ChatMessageAction", FAMILY_CHAT, "single action") { ctx -> ctx.theoChatMessageAction("copy", "copy") })
        add(TheoComponent("ChatMessageActions", FAMILY_CHAT, "action bar") { ctx ->
            ctx.theoChatMessageActions(listOf("copy", "retry", "branch", "delete"))
        })
        add(TheoComponent("ChatMessageBranch", FAMILY_CHAT, "branch navigation") { ctx ->
            ctx.theoChatMessageBranch(
                listOf(
                    { ctx.theoText("分支 A：先写实现。", TheoType.BODY, ctx.theoPalette().foreground) },
                    { ctx.theoText("分支 B：先补测试。", TheoType.BODY, ctx.theoPalette().foreground) },
                    { ctx.theoText("分支 C：先重构。", TheoType.BODY, ctx.theoPalette().foreground) },
                ),
            )
        })
        add(TheoComponent("ChatMessageBranchContent", FAMILY_CHAT, "branch slot") { ctx ->
            ctx.theoChatMessageBranch(
                listOf(
                    { ctx.theoChatMessageContent("alt response 1") },
                    { ctx.theoChatMessageContent("alt response 2") },
                ),
            )
        })
        add(TheoComponent("ChatMessageBranchNext", FAMILY_CHAT, "next button") { ctx -> ctx.theoButton("›", TheoButtonVariant.GHOST, small = true) })
        add(TheoComponent("ChatMessageBranchPage", FAMILY_CHAT, "page label") { ctx -> ctx.theoMono("1 of 3", ctx.theoPalette().mutedForeground, TheoType.CODE_SM) })
        add(TheoComponent("ChatMessageBranchPrevious", FAMILY_CHAT, "prev button") { ctx -> ctx.theoButton("‹", TheoButtonVariant.GHOST, small = true) })
        add(TheoComponent("ChatMessageBranchSelector", FAMILY_CHAT, "dot selector") { ctx ->
            val p = ctx.theoPalette()
            val row = ctx.theoRow(gap = 1f)
            repeat(4) { i ->
                val d = android.view.View(ctx)
                d.layoutParams = LinearLayout.LayoutParams(TheoUi.dp(ctx, 7f), TheoUi.dp(ctx, 7f))
                d.background = TheoUi.roundedBg(ctx, if (i == 1) p.primary else p.border, TheoTokens.RADIUS_FULL)
                row.addView(d)
            }
            row
        })
        add(TheoComponent("ChatMessageContent", FAMILY_CHAT, "plain text body") { ctx -> ctx.theoChatMessageContent("已按要求完成翻译。") })
        add(TheoComponent("ChatMessageResponse", FAMILY_CHAT, "assistant reply") { ctx ->
            ctx.theoChatMessageResponse("已创建 3 个文件并通过测试。", streaming = true)
        })
        add(TheoComponent("ChatMessageRoot", FAMILY_CHAT, "assistant bubble") { ctx ->
            ctx.theoChatMessageRoot("assistant", ctx.theoChatMessageResponse("迁移完成：108 个组件。", streaming = false))
        })
        add(TheoComponent("ChatMessageToolbar", FAMILY_CHAT, "meta toolbar") { ctx -> ctx.theoChatMessageToolbar("14:32", "1.2k tokens") })
        add(TheoComponent("ChatThread", FAMILY_CHAT, "thread") { ctx ->
            ctx.theoChatThread(
                listOf(
                    ctx.theoChatMessageRoot("user", ctx.theoChatMessageContent("跑一下测试")),
                    ctx.theoChatMessageRoot("assistant", ctx.theoChatMessageResponse("152 passed, 0 failed.")),
                ),
            )
        })
        add(TheoComponent("MentionMenu", FAMILY_CHAT, "@ mention popup") { ctx ->
            ctx.theoMentionMenu(listOf("@sakura" to "owner", "@theo" to "agent", "#proj-alpha" to "project"))
        })

        // ── Composer ─────────────────────────────────────────────────
        add(TheoComponent("AgentComposer", FAMILY_COMPOSER, "full composer") { ctx ->
            ctx.theoAgentComposer(
                quickActions = listOf("Explain", "Refactor", "Test"),
                toolbarViews = listOf(ctx.theoBadge("theo-v2", TheoTone.MUTED), ctx.theoBadge("high effort", TheoTone.MUTED)),
            )
        })
        add(TheoComponent("AgentEditor", FAMILY_COMPOSER, "code editor + run") { ctx ->
            ctx.theoAgentEditor(text = "print(\"hello theo\")")
        })
        add(TheoComponent("ApprovalModeSelector", FAMILY_COMPOSER, "approval modes") { ctx ->
            ctx.theoApprovalModeSelector(listOf("auto", "ask", "strict"), selected = 1)
        })
        add(TheoComponent("AttachmentChip", FAMILY_COMPOSER, "file chip") { ctx ->
            val row = ctx.theoRow(gap = 2f)
            row.addView(ctx.theoAttachmentChip("spec.pdf", "1.2 MB"))
            row.addView(ctx.theoAttachmentChip("notes.md", "4 KB"))
            row
        })
        add(TheoComponent("ChatComposer", FAMILY_COMPOSER, "input row") { ctx -> ctx.theoChatComposer() })
        add(TheoComponent("IntentSelector", FAMILY_COMPOSER, "intent chips") { ctx ->
            ctx.theoIntentSelector(listOf("build", "fix", "explain"), selected = 0)
        })
        add(TheoComponent("ModelEffortPicker", FAMILY_COMPOSER, "effort levels") { ctx ->
            ctx.theoModelEffortPicker(listOf("low", "medium", "high"), selected = 2)
        })
        add(TheoComponent("QuickActionChips", FAMILY_COMPOSER, "quick actions") { ctx ->
            ctx.theoQuickActionChips(listOf("/test", "/build", "/deploy", "/review"))
        })
        add(TheoComponent("ThinkingLevelSelector", FAMILY_COMPOSER, "thinking levels") { ctx ->
            ctx.theoThinkingLevelSelector(listOf("off", "brief", "deep"), selected = 1)
        })

        // ── Prompts ──────────────────────────────────────────────────
        add(TheoComponent("ChoicePrompt", FAMILY_PROMPTS, "single select") { ctx ->
            ctx.theoChoicePrompt("选择部署目标", listOf("staging", "production", "preview"))
        })
        add(TheoComponent("ConfirmPrompt", FAMILY_PROMPTS, "yes/no") { ctx ->
            ctx.theoConfirmPrompt("删除 build 产物？", "该操作不可撤销")
        })
        add(TheoComponent("MultiSelectPrompt", FAMILY_PROMPTS, "multi select") { ctx ->
            ctx.theoMultiSelectPrompt("选择要启用的工具", listOf("terminal", "browser", "editor", "network"), checked = setOf(0, 2))
        })
        add(TheoComponent("PermissionModal", FAMILY_PROMPTS, "permission dialog") { ctx ->
            ctx.theoPermissionModal("允许访问密钥库？", "agent 请求读取 ~/.ssh 目录", listOf("read", "sensitive"))
        })
        add(TheoComponent("TextPrompt", FAMILY_PROMPTS, "text input") { ctx ->
            ctx.theoTextPrompt("命名这个分支", "feature/…")
        })

        // ── Approvals & permissions ──────────────────────────────────
        add(TheoComponent("ApprovalCard", FAMILY_APPROVAL, "risk-rail approval") { ctx ->
            ctx.theoApprovalCard("rm -rf builds/tmp", "清理 14 天以上的构建产物", TheoRiskLevel.DESTRUCTIVE, command = "rm -rf builds/tmp/*")
        })
        add(TheoComponent("AuditLogEntry", FAMILY_APPROVAL, "audit row") { ctx ->
            val col = ctx.theoColumn(gap = 1.5f)
            col.addView(ctx.theoAuditLogEntry("14:20", "sakura", "approval.grant"))
            col.addView(ctx.theoAuditLogEntry("14:22", "theo", "command.run", ok = false))
            col
        })
        add(TheoComponent("PermissionMatrix", FAMILY_APPROVAL, "scope matrix") { ctx ->
            ctx.theoPermissionMatrix(
                listOf(
                    Triple("fs.read", true, true),
                    Triple("fs.write", true, false),
                    Triple("net.fetch", false, false),
                ),
            )
        })

        // ── Tools & diff ─────────────────────────────────────────────
        add(TheoComponent("ArtifactPreview", FAMILY_TOOLS, "artifact card") { ctx ->
            ctx.theoArtifactPreview("app-debug.apk", "archive", "8.0 MB")
        })
        add(TheoComponent("BuildLogStream", FAMILY_TOOLS, "build output") { ctx ->
            ctx.theoBuildLogStream(
                listOf(
                    "> task :app:compileDebugKotlin" to TheoRunStatus.SUCCEEDED,
                    "> task :app:testDebugUnitTest" to TheoRunStatus.SUCCEEDED,
                    "> task :app:packageDebug" to TheoRunStatus.RUNNING,
                ),
            )
        })
        add(TheoComponent("CodeBlock", FAMILY_TOOLS, "syntax block") { ctx ->
            ctx.theoCodeBlock("kotlin", "fun main() {\n    println(\"hello\")\n}")
        })
        add(TheoComponent("CodeReviewPanel", FAMILY_TOOLS, "review comments") { ctx ->
            ctx.theoCodeReviewPanel(
                files = listOf("MainActivity.kt", "AgentApi.kt"),
                comments = listOf(
                    TheoReviewComment("MainActivity.kt", 42, "考虑在 onDestroy 释放 watcher", TheoTone.WARNING),
                    TheoReviewComment("AgentApi.kt", 10, "此处空安全处理正确", TheoTone.SUCCESS),
                ),
            )
        })
        add(TheoComponent("CreatedFilesCard", FAMILY_TOOLS, "created files") { ctx ->
            ctx.theoCreatedFilesCard(listOf("src/ui.py" to "2.4 KB", "tests/test_ui.py" to "1.1 KB"))
        })
        add(TheoComponent("DataPart", FAMILY_TOOLS, "json part") { ctx ->
            ctx.theoDataPart("""{"ok":true,"items":3}""")
        })
        add(TheoComponent("DiffViewer", FAMILY_TOOLS, "unified diff") { ctx -> ctx.theoDiffViewer("main.py", SAMPLE_DIFF) })
        add(TheoComponent("FilePart", FAMILY_TOOLS, "file part") { ctx -> ctx.theoFilePart("report.pdf", "application/pdf", "340 KB") })
        add(TheoComponent("ReasoningPart", FAMILY_TOOLS, "reasoning part") { ctx ->
            ctx.theoReasoningPart("先检查依赖，再决定增量或全量构建…")
        })
        add(TheoComponent("SourceDocumentPart", FAMILY_TOOLS, "doc source") { ctx ->
            ctx.theoSourceDocumentPart("AGENTS.md", "遵守临时数据库约束，禁止触碰真实用户数据…")
        })
        add(TheoComponent("SourceUrlPart", FAMILY_TOOLS, "url source") { ctx ->
            ctx.theoSourceUrlPart("Kotlin docs", "https://kotlinlang.org/api/latest")
        })
        add(TheoComponent("TextPart", FAMILY_TOOLS, "text part") { ctx -> ctx.theoTextPart("一段普通文本输出。") })
        add(TheoComponent("TerminalPanel", FAMILY_TOOLS, "terminal emulator") { ctx ->
            ctx.theoTerminalPanel(
                listOf(
                    TheoTerminalLine("$ npm test"),
                    TheoTerminalLine("✓ theokit 108 components", TheoTone.SUCCESS),
                    TheoTerminalLine("152 passed", TheoTone.SUCCESS),
                ),
            )
        })
        add(TheoComponent("ToolCall", FAMILY_TOOLS, "inline tool call") { ctx ->
            ctx.theoToolCall("search", "query: TODO", TheoRunStatus.SUCCEEDED)
        })
        add(TheoComponent("ToolCallCard", FAMILY_TOOLS, "tool card + output") { ctx ->
            ctx.theoToolCallCard("terminal", "npm run check", TheoRunStatus.SUCCEEDED, output = "0 errors\nok")
        })
        add(TheoComponent("ToolCallPart", FAMILY_TOOLS, "stream tool part") { ctx ->
            ctx.theoToolCallPart("terminal", "cmd: gradle assembleDebug")
        })
        add(TheoComponent("ToolResult", FAMILY_TOOLS, "tool result") { ctx ->
            ctx.theoToolResult("read_file", "…file content…", ok = true, durationMs = 34)
        })
        add(TheoComponent("ToolsList", FAMILY_TOOLS, "tool manifest") { ctx ->
            ctx.theoToolsList(listOf("terminal" to "run shell commands", "read_file" to "read workspace files", "browser" to "headless browsing"))
        })

        // ── Plans, progress & logs ───────────────────────────────────
        add(TheoComponent("LaneBoard", FAMILY_PLAN, "swim lanes") { ctx ->
            ctx.theoLaneBoard(
                listOf(
                    TheoLane("todo", listOf("Migrate icons", "Write tests")),
                    TheoLane("doing", listOf("Kotlin views")),
                    TheoLane("done", listOf("Token audit")),
                ),
            )
        })
        add(TheoComponent("ProgressChecklist", FAMILY_PLAN, "checklist") { ctx ->
            ctx.theoProgressChecklist(listOf("Token mapping" to true, "Icon set" to true, "Showcase page" to false))
        })
        add(TheoComponent("RunningTasksPanel", FAMILY_PLAN, "active tasks") { ctx ->
            ctx.theoRunningTasksPanel(
                listOf(
                    TheoRunningTask("build app", TheoRunStatus.RUNNING, 0.6f),
                    TheoRunningTask("run tests", TheoRunStatus.QUEUED),
                ),
            )
        })
        add(TheoComponent("StepsRail", FAMILY_PLAN, "steps rail") { ctx ->
            ctx.theoStepsRail(listOf("plan", "code", "test", "ship"), current = 1)
        })
        add(TheoComponent("TaskNode", FAMILY_PLAN, "single node") { ctx -> ctx.theoTaskNode("写迁移总结", TheoRunStatus.RUNNING) })
        add(TheoComponent("TaskPlan", FAMILY_PLAN, "hierarchical plan") { ctx ->
            ctx.theoTaskPlan(
                "迁移计划",
                listOf(
                    TheoTaskNodeData("桌面端", TheoRunStatus.SUCCEEDED, children = listOf(
                        TheoTaskNodeData("vanilla JS 组件", TheoRunStatus.SUCCEEDED),
                        TheoTaskNodeData("CSS 覆盖审计", TheoRunStatus.SUCCEEDED),
                    )),
                    TheoTaskNodeData("Android 端", TheoRunStatus.RUNNING, children = listOf(
                        TheoTaskNodeData("Kotlin 视图库", TheoRunStatus.RUNNING),
                        TheoTaskNodeData("总览 Activity", TheoRunStatus.PENDING),
                    )),
                ),
            )
        })
        add(TheoComponent("WorkLog", FAMILY_PLAN, "work log") { ctx ->
            ctx.theoWorkLog(
                listOf(
                    TheoWorkLogEntry("14:01", "read theokit-ui/dist", TheoEventStatus.SUCCESS),
                    TheoWorkLogEntry("14:20", "render 108 comps", TheoEventStatus.SUCCESS),
                    TheoWorkLogEntry("14:40", "screenshot review", TheoEventStatus.RUNNING),
                ),
            )
        })

        // ── Session & context ────────────────────────────────────────
        add(TheoComponent("AutoCompactNotice", FAMILY_SESSION, "compact warning") { ctx -> ctx.theoAutoCompactNotice(84) })
        add(TheoComponent("ChannelCard", FAMILY_SESSION, "channel") { ctx ->
            val col = ctx.theoColumn(gap = 2f)
            col.addView(ctx.theoChannelCard("Web Console", "web", true))
            col.addView(ctx.theoChannelCard("Android", "mobile", false))
            col
        })
        add(TheoComponent("ContextCard", FAMILY_SESSION, "context summary") { ctx ->
            ctx.theoContextCard(
                "Context",
                listOf(
                    TheoContextItem("files", "42", "file-text"),
                    TheoContextItem("tokens", "18.2k", "coins"),
                    TheoContextItem("session", "2h 15m", "clock"),
                ),
            )
        })
        add(TheoComponent("ContextWindowBar", FAMILY_SESSION, "window usage") { ctx -> ctx.theoContextWindowBar(64) })
        add(TheoComponent("ExportChatDialog", FAMILY_SESSION, "export dialog") { ctx ->
            ctx.theoExportChatDialog(listOf("Markdown", "JSON", "PDF"), selected = 0)
        })
        add(TheoComponent("FolderContextCard", FAMILY_SESSION, "folder context") { ctx ->
            ctx.theoFolderContextCard("workspaces/usr_theo", 128, "4.2 MB")
        })
        add(TheoComponent("FolderSelector", FAMILY_SESSION, "folder pick") { ctx ->
            ctx.theoFolderSelector(listOf("workspaces", "builds", "docs"), selected = 0)
        })
        add(TheoComponent("GatewayStatusIndicator", FAMILY_SESSION, "gateway dot") { ctx ->
            val col = ctx.theoColumn(gap = 1f)
            col.addView(ctx.theoGatewayStatusIndicator("healthy", 42))
            col.addView(ctx.theoGatewayStatusIndicator("degraded", 380))
            col
        })
        add(TheoComponent("ProjectSwitcher", FAMILY_SESSION, "project picker") { ctx -> ctx.theoProjectSwitcher("Android Agent") })
        add(TheoComponent("RecentFoldersList", FAMILY_SESSION, "recent folders") { ctx ->
            ctx.theoRecentFoldersList(listOf("android-app" to "~/work/android-app", "desktop" to "~/work/desktop"))
        })
        add(TheoComponent("SessionListItem", FAMILY_SESSION, "session row") { ctx ->
            val col = ctx.theoColumn(gap = 2f)
            col.addView(ctx.theoSessionListItem("迁移 theokit 组件", "今天 · 108 组件", "14:32", active = true))
            col.addView(ctx.theoSessionListItem("T09 admin 验收", "昨天", "11:19"))
            col
        })
        add(TheoComponent("SessionTimeline", FAMILY_SESSION, "session events") { ctx ->
            ctx.theoSessionTimeline(
                listOf(
                    TheoSessionEvent("14:00", "session started", TheoTone.PRIMARY),
                    TheoSessionEvent("14:20", "task completed", TheoTone.SUCCESS),
                    TheoSessionEvent("14:40", "pause requested", TheoTone.WARNING),
                ),
            )
        })

        // ── Models & usage ───────────────────────────────────────────
        add(TheoComponent("CostMeter", FAMILY_MODEL, "spend meter") { ctx -> ctx.theoCostMeter(spentCents = 421, budgetCents = 2000) })
        add(TheoComponent("ModelCard", FAMILY_MODEL, "model info") { ctx ->
            ctx.theoModelCard(
                TheoModelInfo("theo-v2", "theo labs", "200k", listOf("vision", "tools", "streaming")),
                selected = true,
            )
        })
        add(TheoComponent("ModelSelector", FAMILY_MODEL, "model list") { ctx ->
            ctx.theoModelSelector(
                listOf(
                    TheoModelInfo("theo-v2", "theo labs", "200k", listOf("tools")),
                    TheoModelInfo("theo-mini", "theo labs", "128k"),
                    TheoModelInfo("theo-reason", "theo labs", "200k", listOf("reasoning")),
                ),
                selectedIndex = 0,
            )
        })
        add(TheoComponent("TokenUsageChart", FAMILY_MODEL, "usage bars") { ctx ->
            ctx.theoTokenUsageChart(listOf("一" to 40, "二" to 65, "三" to 30, "四" to 80, "五" to 55, "六" to 20, "日" to 45))
        })
        add(TheoComponent("UsageMeter", FAMILY_MODEL, "quota meter") { ctx -> ctx.theoUsageMeter(72, "540k", "750k") })

        // ── Infrastructure ───────────────────────────────────────────
        add(TheoComponent("BrowserControls", FAMILY_INFRA, "browser bar") { ctx ->
            ctx.theoBrowserControls("https://example.com/docs", loading = true)
        })
        add(TheoComponent("CronJobCard", FAMILY_INFRA, "cron job") { ctx ->
            ctx.theoCronJobCard(TheoCronJob("daily-report", "0 9 * * 1-5", "周一 09:00", enabled = true))
        })
        add(TheoComponent("CronJobsList", FAMILY_INFRA, "cron list") { ctx ->
            ctx.theoCronJobsList(
                listOf(
                    TheoCronJob("daily-report", "0 9 * * 1-5", "周一 09:00"),
                    TheoCronJob("weekly-clean", "0 3 * * 0", "周日 03:00", enabled = false),
                ),
            )
        })
        add(TheoComponent("HookConfig", FAMILY_INFRA, "hooks config") { ctx ->
            ctx.theoHookConfig(
                listOf(
                    TheoHook("pre-commit", "lint-staged"),
                    TheoHook("post-build", "notify.sh", enabled = false),
                ),
            )
        })
        add(TheoComponent("HookEventLog", FAMILY_INFRA, "hook log") { ctx ->
            ctx.theoHookEventLog(
                listOf(
                    Triple("pre-commit", "14:01", true),
                    Triple("post-build", "14:22", false),
                ),
            )
        })
        add(TheoComponent("McpServerCard", FAMILY_INFRA, "mcp server") { ctx ->
            ctx.theoMcpServerCard(TheoMcpServer("filesystem", "stdio", 12, connected = true))
        })
        add(TheoComponent("McpServerList", FAMILY_INFRA, "mcp servers") { ctx ->
            ctx.theoMcpServerList(
                listOf(
                    TheoMcpServer("filesystem", "stdio", 12, true),
                    TheoMcpServer("browser", "sse", 5, false),
                ),
            )
        })
        add(TheoComponent("StabilityBundleViewer", FAMILY_INFRA, "bundle manifest") { ctx ->
            ctx.theoStabilityBundleViewer(
                "release-1.4.1",
                listOf(
                    Triple("tokens.css", "sha256:ab12…", true),
                    Triple("components.css", "sha256:cd34…", true),
                    Triple("fonts.css", "sha256:ef56…", false),
                ),
            )
        })

        // ── Rules, skills & memory ───────────────────────────────────
        add(TheoComponent("MemoryEditor", FAMILY_RULES, "memory entries") { ctx ->
            ctx.theoMemoryEditor(listOf("tech-stack" to "Kotlin, Electron, Node", "constraint" to "临时数据库约束"))
        })
        add(TheoComponent("RuleCard", FAMILY_RULES, "single rule") { ctx ->
            ctx.theoRuleCard("no-prod-secrets", "deny: data/**, *.env")
        })
        add(TheoComponent("RuleEditor", FAMILY_RULES, "rules list") { ctx ->
            ctx.theoRuleEditor(listOf("no-prod-secrets" to "deny: *.env", "tmp-only" to "allow: /tmp/**"))
        })
        add(TheoComponent("SkillCard", FAMILY_RULES, "skill card") { ctx ->
            ctx.theoSkillCard(TheoSkill("迁移组件", "React → Kotlin 视图迁移技能", listOf("/migrate", "theokit")))
        })
        add(TheoComponent("SkillEditor", FAMILY_RULES, "skill editor") { ctx ->
            ctx.theoSkillEditor(TheoSkill("迁移组件", "React → Kotlin 视图迁移技能", listOf("/migrate", "theokit")))
        })
        add(TheoComponent("SkillsList", FAMILY_RULES, "skills list") { ctx ->
            ctx.theoSkillsList(
                listOf(
                    TheoSkill("迁移组件", "React → Kotlin", listOf("/migrate")),
                    TheoSkill("回归测试", "端到端验证", listOf("/verify")),
                ),
            )
        })
        add(TheoComponent("SystemPromptEditor", FAMILY_RULES, "system prompt") { ctx ->
            ctx.theoSystemPromptEditor("你是一个严谨的迁移助手，遵守项目约束…")
        })

        // ── Panels & viewers ─────────────────────────────────────────
        add(TheoComponent("PreviewPanel", FAMILY_PANELS, "preview frame") { ctx -> ctx.theoPreviewPanel("app-debug.apk 预览", "tablet") })
        add(TheoComponent("Whiteboard", FAMILY_PANELS, "canvas legend") { ctx ->
            ctx.theoWhiteboard(listOf("rect" to "迁移计划", "circle" to "令牌审计", "diamond" to "验收节点"))
        })

        // ── Editors & slides ─────────────────────────────────────────
        add(TheoComponent("Slide", FAMILY_SLIDES, "single slide") { ctx ->
            ctx.theoSlide(TheoSlides.Slide("Violet Forge", "TheoKit on Android", listOf("108 components", "Native Kotlin views")))
        })
        add(TheoComponent("SlideDeck", FAMILY_SLIDES, "markdown deck") { ctx -> ctx.theoSlideDeck(SAMPLE_DECK) })
    }

    val componentNames: List<String> = COMPONENTS.map { it.name }

    fun byFamily(family: String): List<TheoComponent> = COMPONENTS.filter { it.family == family }

    /** Build a titled demo card around a component instance. */
    fun demoCard(ctx: Context, component: TheoComponent): View {
        val p = ctx.theoPalette()
        val card = ctx.theoCard(paddingUnits = 4f)
        val head = ctx.theoRow(gap = 2f)
        val name = ctx.theoText(component.name, TheoType.CODE, ctx.theoPalette().foreground)
        name.typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.BOLD)
        head.addView(name)
        val desc = ctx.theoText(component.description, TheoType.MICRO, p.mutedForeground)
        desc.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        desc.gravity = Gravity.END
        head.addView(desc)
        card.addView(head)
        card.addView(component.build(ctx))
        TheoRowGap.apply(card, ctx)
        return card
    }
}
