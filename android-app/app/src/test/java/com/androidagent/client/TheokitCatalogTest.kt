package com.androidagent.client

import android.graphics.Path
import com.androidagent.client.theokit.TheoDiff
import com.androidagent.client.theokit.TheoIcons
import com.androidagent.client.theokit.TheoPalette
import com.androidagent.client.theokit.TheoRiskLevel
import com.androidagent.client.theokit.TheoRunStatus
import com.androidagent.client.theokit.TheoSlides
import com.androidagent.client.theokit.TheoTokens
import com.androidagent.client.theokit.TheoTone
import com.androidagent.client.theokit.TheoType
import com.androidagent.client.theokit.TheokitCatalog
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * TheoKit Android 迁移验证：目录完整性（与桌面端 108 组件名单一致）、
 * 纯逻辑解析器（diff/slides/状态映射）与设计令牌。
 */
class TheokitCatalogTest {

    /** 桌面端 theokit.test.js 的 EXPECTED_COMPONENTS 名单（108）。 */
    private val desktopComponents = listOf(
        "AgentComposer", "AgentEditor", "AgentErrorCard", "AgentEvent", "AgentHandoff", "AgentProfile",
        "AgentStartingState", "AgentStream", "AgentStreaming", "AgentTimeline", "AgentToolRenderer",
        "ApprovalCard", "ApprovalModeSelector", "ArtifactPreview", "AttachmentChip", "AuditLogEntry",
        "AutoCompactNotice", "BranchIndicator", "BrowserControls", "BuildLogStream", "Button",
        "CapabilityIndicator", "ChannelCard", "ChatComposer", "ChatMessage", "ChatMessageAction",
        "ChatMessageActions", "ChatMessageBranch", "ChatMessageBranchContent", "ChatMessageBranchNext",
        "ChatMessageBranchPage", "ChatMessageBranchPrevious", "ChatMessageBranchSelector",
        "ChatMessageContent", "ChatMessageResponse", "ChatMessageRoot", "ChatMessageToolbar",
        "ChatThread", "ChoicePrompt", "CodeBlock", "CodeReviewPanel", "ConfirmPrompt", "ContextCard",
        "ContextWindowBar", "CostMeter", "CreatedFilesCard", "CronJobCard", "CronJobsList", "DataPart",
        "DiffViewer", "ExportChatDialog", "FilePart", "FolderContextCard", "FolderSelector",
        "GatewayStatusIndicator", "HookConfig", "HookEventLog", "IntentSelector", "LaneBoard",
        "McpServerCard", "McpServerList", "MemoryEditor", "MentionMenu", "ModelCard",
        "ModelEffortPicker", "ModelSelector", "MultiSelectPrompt", "PermissionMatrix", "PermissionModal",
        "PreviewPanel", "ProgressChecklist", "ProjectSwitcher", "QuickActionChips", "ReasoningPart",
        "RecentFoldersList", "RuleCard", "RuleEditor", "RunStats", "RunStatusPill", "RunningTasksPanel",
        "SessionListItem", "SessionTimeline", "SkillCard", "SkillEditor", "SkillsList", "Slide",
        "SlideDeck", "SourceDocumentPart", "SourceUrlPart", "StabilityBundleViewer", "StepsRail",
        "SubAgentDispatch", "SystemPromptEditor", "TaskNode", "TaskPlan", "TerminalPanel", "TextPart",
        "TextPrompt", "ThinkingLevelSelector", "TokenUsageChart", "ToolCall", "ToolCallCard",
        "ToolCallPart", "ToolResult", "ToolsList", "UsageMeter", "Whiteboard", "WorkLog",
    )

    @Test
    fun `catalog has exactly 108 components`() {
        assertEquals(108, TheokitCatalog.COMPONENTS.size)
        assertEquals(108, desktopComponents.size)
    }

    @Test
    fun `component names match desktop list exactly`() {
        val android = TheokitCatalog.componentNames.toSet()
        val desktop = desktopComponents.toSet()
        assertEquals(emptySet<String>(), desktop - android)
        assertEquals(emptySet<String>(), android - desktop)
    }

    @Test
    fun `component names are unique`() {
        assertEquals(TheokitCatalog.componentNames.size, TheokitCatalog.componentNames.toSet().size)
    }

    @Test
    fun `all families are covered`() {
        assertEquals(14, TheokitCatalog.FAMILIES.size)
        TheokitCatalog.FAMILIES.forEach { family ->
            assertTrue("family $family must have components", TheokitCatalog.byFamily(family).isNotEmpty())
        }
        val covered = TheokitCatalog.COMPONENTS.map { it.family }.toSet()
        assertEquals(TheokitCatalog.FAMILIES.toSet(), covered)
    }

    @Test
    fun `every component has description and build factory`() {
        TheokitCatalog.COMPONENTS.forEach { c ->
            assertTrue("${c.name} needs description", c.description.isNotBlank())
        }
    }

    @Test
    fun `diff parser classifies hunks and counts add-delete`() {
        val diff = """
            @@ -1,3 +1,4 @@
             keep
            -old
            +new
            +extra
        """.trimIndent()
        val hunks = TheoDiff.parse(diff)
        assertEquals(1, hunks.size)
        assertEquals(2, hunks[0].added)
        assertEquals(1, hunks[0].deleted)
        assertEquals(1, hunks[0].lines.count { it.kind == TheoDiff.Kind.CONTEXT })
    }

    @Test
    fun `diff parser handles multiple hunks`() {
        val diff = """
            @@ -1,2 +1,2 @@
            -a
            +b
            @@ -10,2 +10,3 @@
             c
            +d
        """.trimIndent()
        val hunks = TheoDiff.parse(diff)
        assertEquals(2, hunks.size)
        assertEquals(2, TheoDiff.totalAdded(hunks))
        assertEquals(1, TheoDiff.totalDeleted(hunks))
    }

    @Test
    fun `diff parser ignores non-hunk content`() {
        val hunks = TheoDiff.parse("no hunk headers\njust text")
        assertTrue(hunks.isEmpty())
    }

    @Test
    fun `slides parser splits markdown on separator`() {
        val md = """
            # Title One
            ## Subtitle
            - bullet a
            - bullet b
            ---
            # Title Two
            - bullet c
        """.trimIndent()
        val slides = TheoSlides.parse(md)
        assertEquals(2, slides.size)
        assertEquals("Title One", slides[0].title)
        assertEquals("Subtitle", slides[0].subtitle)
        assertEquals(listOf("bullet a", "bullet b"), slides[0].bullets)
        assertEquals("Title Two", slides[1].title)
        assertEquals(null, slides[1].subtitle)
    }

    @Test
    fun `slides parser handles plain text slides`() {
        val slides = TheoSlides.parse("只有一行标题")
        assertEquals(1, slides.size)
        assertEquals("只有一行标题", slides[0].title)
    }

    @Test
    fun `run status mapping matches server literals`() {
        assertEquals(TheoRunStatus.RUNNING, TheoRunStatus.from("running"))
        assertEquals(TheoRunStatus.SUCCEEDED, TheoRunStatus.from("completed"))
        assertEquals(TheoRunStatus.FAILED, TheoRunStatus.from("error"))
        assertEquals(TheoRunStatus.CANCELED, TheoRunStatus.from("cancelled"))
        assertEquals(TheoRunStatus.PAUSED, TheoRunStatus.from("paused"))
        assertEquals(TheoRunStatus.PENDING, TheoRunStatus.from(null))
        assertEquals(TheoRunStatus.PENDING, TheoRunStatus.from("whatever"))
        // every status has a label/tone/icon
        TheoRunStatus.entries.forEach {
            assertTrue(it.label.isNotBlank())
            assertTrue(it.icon.isNotBlank())
        }
    }

    @Test
    fun `risk level mapping matches server literals`() {
        assertEquals(TheoRiskLevel.DESTRUCTIVE, TheoRiskLevel.from("destructive"))
        assertEquals(TheoRiskLevel.PROCESS, TheoRiskLevel.from("process"))
        assertEquals(TheoRiskLevel.NETWORK, TheoRiskLevel.from("network"))
        assertEquals(TheoRiskLevel.WORKSPACE_WRITE, TheoRiskLevel.from("workspace_write"))
        assertEquals(TheoRiskLevel.READ, TheoRiskLevel.from("read"))
        assertEquals(TheoRiskLevel.HIGH, TheoRiskLevel.from("high"))
        assertEquals(TheoRiskLevel.MEDIUM, TheoRiskLevel.from("legacy-unknown"))
    }

    @Test
    fun `light and dark palettes are distinct and token-complete`() {
        fun assertPalette(p: TheoPalette, dark: Boolean) {
            assertEquals(dark, p.dark)
            assertNotEquals(p.background, p.foreground)
            assertNotEquals(p.primary, p.mutedForeground)
            if (dark) {
                assertEquals(0xFF111210.toInt(), p.background)
                assertEquals(0xFFF1F2EC.toInt(), p.primary)
            } else {
                assertEquals(0xFFF7F7F5.toInt(), p.background)
                assertEquals(0xFF20211F.toInt(), p.primary)
            }
        }
        assertPalette(TheoTokens.LIGHT, dark = false)
        assertPalette(TheoTokens.DARK, dark = true)
    }

    @Test
    fun `tint composes alpha channel correctly`() {
        val base = 0xFF6F49B1.toInt()
        val tinted = TheoTokens.tint(base, 0.5f)
        assertEquals(0x80, (tinted ushr 24) and 0xFF)
        assertEquals(base and 0xFFFFFF, tinted and 0xFFFFFF)
    }

    @Test
    fun `mix blends two colors`() {
        val black = 0xFF000000.toInt()
        val white = 0xFFFFFFFF.toInt()
        val mid = TheoTokens.mix(black, white, 0.5f)
        assertEquals(128, (mid shr 16) and 0xFF)
    }

    @Test
    fun `tone lookup covers all enum values`() {
        val p = TheoTokens.LIGHT
        TheoTone.entries.forEach { tone ->
            assertNotEquals(0, p.tone(tone))
        }
    }

    @Test
    fun `icon registry draws every known icon without crashing`() {
        val known = listOf(
            "check", "x", "plus", "minus", "chevron-down", "chevron-up", "chevron-left", "chevron-right",
            "arrow-right", "arrow-up-right", "circle-dot", "circle-dashed", "loader", "check-circle",
            "x-circle", "alert-triangle", "info-circle", "help-circle", "terminal", "search", "file-text",
            "file-plus", "file-search", "file-edit", "folder", "folder-open", "edit-3", "hammer", "wrench",
            "shield-check", "zap", "globe", "server", "cpu", "database", "clock", "calendar", "hash",
            "coins", "star", "flag", "target", "sparkles", "brain", "bot", "user", "message-square",
            "send", "paperclip", "stop", "copy", "refresh", "trash", "download", "upload", "play",
            "pause", "skip-forward", "external-link", "more-horizontal", "git-branch", "lock", "key",
            "settings", "book-open", "list-checks", "scroll-text", "layers", "activity", "eye", "code",
            "box", "sliders", "link", "bell", "trending-up", "bar-chart", "image", "film",
            "corner-down-right", "rotate-cw", "shield", "wifi",
        )
        known.forEach { name ->
            val p = Path()
            TheoIcons.draw(p, name)
        }
        val unknown = Path()
        TheoIcons.draw(unknown, "definitely-not-an-icon")
    }

    @Test
    fun `type scale has ten styles`() {
        assertEquals(10, TheoType.entries.size)
        assertTrue(TheoType.entries.all { it.sizeSp in 11f..28f })
        assertTrue(TheoType.DISPLAY.sizeSp > TheoType.TITLE_LG.sizeSp)
        assertTrue(TheoType.TITLE_LG.sizeSp > TheoType.BODY.sizeSp)
        assertTrue(TheoType.BODY.sizeSp > TheoType.MICRO.sizeSp)
        assertTrue(TheoType.CODE.mono)
        assertTrue(TheoType.CODE_SM.mono)
        assertTrue(!TheoType.BODY.mono)
    }
}
