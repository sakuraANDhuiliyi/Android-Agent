package com.androidagent.client.theokit

import android.content.Context
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ScrollView

/**
 * Tools, diff & approval family — ToolCall*, ToolsList, DiffViewer, CodeBlock,
 * CodeReviewPanel, TerminalPanel, BuildLogStream, CreatedFilesCard,
 * ArtifactPreview, stream parts, ApprovalCard, PermissionMatrix, AuditLogEntry.
 */

object TheoDiff {

    enum class Kind { CONTEXT, ADD, DEL, HUNK_HEADER }

    data class Line(val kind: Kind, val text: String)

    data class Hunk(val header: String, val lines: List<Line>) {
        val added get() = lines.count { it.kind == Kind.ADD }
        val deleted get() = lines.count { it.kind == Kind.DEL }
    }

    /** Parse a unified diff into hunks (mirrors desktop parseUnifiedDiffToHunks). */
    fun parse(diff: String): List<Hunk> {
        val hunks = mutableListOf<Hunk>()
        var header: String? = null
        var lines = mutableListOf<Line>()
        fun flush() {
            if (header != null && lines.isNotEmpty()) hunks += Hunk(header!!, lines.toList())
            header = null
            lines = mutableListOf()
        }
        diff.lines().forEach { raw ->
            val line = raw.trimEnd('\r')
            when {
                line.startsWith("@@") -> {
                    flush()
                    header = line
                }
                header != null && line.startsWith("+") -> lines += Line(Kind.ADD, line)
                header != null && line.startsWith("-") -> lines += Line(Kind.DEL, line)
                header != null && line.startsWith(" ") -> lines += Line(Kind.CONTEXT, line)
                header != null && line.isEmpty() -> lines += Line(Kind.CONTEXT, line)
            }
        }
        flush()
        return hunks
    }

    fun totalAdded(hunks: List<Hunk>) = hunks.sumOf { it.added }
    fun totalDeleted(hunks: List<Hunk>) = hunks.sumOf { it.deleted }
}

fun Context.theoToolCall(tool: String, args: String? = null, status: TheoRunStatus = TheoRunStatus.SUCCEEDED): View {
    val p = theoPalette()
    val row = theoRow(gap = 2f)
    row.addView(theoIcon("wrench", p.mutedForeground, 14f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoMono(tool, p.foreground, TheoType.CODE_SM))
    if (args != null) mid.addView(theoMono(args, p.mutedForeground, TheoType.CODE_SM, maxLines = 1))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    row.addView(mid)
    row.addView(theoRunStatusPill(status))
    return row
}

fun Context.theoToolCallCard(tool: String, target: String, status: TheoRunStatus = TheoRunStatus.RUNNING, output: String? = null): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    val icon = when (tool.lowercase()) {
        "terminal", "shell", "bash" -> "terminal"
        "read", "read_file" -> "file-search"
        "write", "write_file" -> "file-plus"
        "edit", "edit_file" -> "edit-3"
        "search", "grep" -> "search"
        "browser" -> "globe"
        else -> "wrench"
    }
    head.addView(theoIcon(icon, p.primary, 16f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoMono(tool, p.foreground, TheoType.CODE_SM))
    mid.addView(theoText(target, TheoType.BODY_SM, p.mutedForeground))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(mid)
    head.addView(theoRunStatusPill(status))
    card.addView(head)
    if (output != null) {
        val pre = theoCard(bg = p.muted, radiusDp = TheoTokens.RADIUS_MD, stroke = false, paddingUnits = 2.5f)
        pre.addView(theoMono(output, p.mutedForeground, TheoType.CODE_SM, maxLines = 4))
        card.addView(pre)
    }
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoToolResult(tool: String, output: String, ok: Boolean = true, durationMs: Long? = null): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon(if (ok) "check-circle" else "x-circle", if (ok) p.success else p.failed, 14f))
    head.addView(theoMono(tool, p.foreground, TheoType.CODE_SM))
    if (durationMs != null) head.addView(theoMono("${durationMs}ms", p.mutedForeground, TheoType.CODE_SM))
    card.addView(head)
    card.addView(theoMono(output, p.mutedForeground, TheoType.CODE_SM, maxLines = 3))
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoToolCallPart(tool: String, args: String): View {
    val p = theoPalette()
    val col = theoColumn(gap = 0.5f)
    col.addView(theoMono("tool: $tool", p.primary, TheoType.CODE_SM))
    col.addView(theoMono(args, p.mutedForeground, TheoType.CODE_SM, maxLines = 2))
    return col
}

fun Context.theoToolsList(tools: List<Pair<String, String>>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("settings", p.primary, 14f))
    head.addView(theoText("Tools (${tools.size})", TheoType.LABEL, p.mutedForeground))
    card.addView(head)
    tools.forEach { (name, desc) ->
        val row = theoRow(gap = 2f)
        row.addView(theoMono(name, p.foreground, TheoType.CODE_SM))
        val d = theoText(desc, TheoType.MICRO, p.mutedForeground, maxLines = 1)
        d.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        row.addView(d)
        card.addView(row)
    }
    TheoRowGap.apply(card, this)
    return card
}

/** DiffViewer: unified diff with colored add/del lines and +N/−M stats. */
fun Context.theoDiffViewer(title: String, diff: String): View {
    val p = theoPalette()
    val hunks = TheoDiff.parse(diff)
    val card = theoCard(paddingUnits = 0f)
    val head = theoRow(gap = 2f)
    head.setPadding(TheoUi.dp(this, 12f), TheoUi.dp(this, 10f), TheoUi.dp(this, 12f), TheoUi.dp(this, 10f))
    head.addView(theoIcon("code", p.primary, 14f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoMono(title, p.foreground, TheoType.CODE_SM))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(mid)
    val stats = theoRow(gap = 1.5f)
    stats.addView(theoMono("+${TheoDiff.totalAdded(hunks)}", p.success, TheoType.CODE_SM))
    stats.addView(theoMono("−${TheoDiff.totalDeleted(hunks)}", p.failed, TheoType.CODE_SM))
    head.addView(stats)
    card.addView(head)
    card.addView(theoDivider())

    val scroll = ScrollView(this)
    val lines = theoColumn(gap = 0f)
    hunks.forEach { hunk ->
        val hh = theoMono(hunk.header, p.mutedForeground, TheoType.CODE_SM)
        hh.setBackgroundColor(TheoTokens.tint(p.primary, 0.08f))
        hh.setPadding(TheoUi.dp(this, 12f), TheoUi.dp(this, 4f), TheoUi.dp(this, 12f), TheoUi.dp(this, 4f))
        lines.addView(hh)
        hunk.lines.forEach { line ->
            val tv = theoMono(if (line.text.isEmpty()) " " else line.text, p.foreground, TheoType.CODE_SM)
            when (line.kind) {
                TheoDiff.Kind.ADD -> tv.setTextColor(p.success)
                TheoDiff.Kind.DEL -> tv.setTextColor(p.failed)
                else -> tv.setTextColor(p.mutedForeground)
            }
            tv.setBackgroundColor(
                when (line.kind) {
                    TheoDiff.Kind.ADD -> TheoTokens.tint(p.success, 0.10f)
                    TheoDiff.Kind.DEL -> TheoTokens.tint(p.failed, 0.10f)
                    else -> android.graphics.Color.TRANSPARENT
                },
            )
            tv.setPadding(TheoUi.dp(this, 12f), TheoUi.dp(this, 1f), TheoUi.dp(this, 12f), TheoUi.dp(this, 1f))
            lines.addView(tv)
        }
    }
    scroll.addView(lines)
    card.addView(
        scroll,
        LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, TheoUi.dp(this, 200f)),
    )
    return card
}

fun Context.theoCodeBlock(language: String, code: String): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 0f)
    val head = theoRow(gap = 2f)
    head.setPadding(TheoUi.dp(this, 10f), TheoUi.dp(this, 8f), TheoUi.dp(this, 10f), TheoUi.dp(this, 8f))
    head.addView(theoIcon("code", p.mutedForeground, 12f))
    val lang = theoMono(language, p.mutedForeground, TheoType.CODE_SM)
    lang.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(lang)
    head.addView(theoIcon("copy", p.mutedForeground, 12f))
    card.addView(head)
    card.addView(theoDivider())
    val scroll = ScrollView(this)
    val body = theoColumn(gap = 0f)
    body.setPadding(TheoUi.dp(this, 12f), TheoUi.dp(this, 10f), TheoUi.dp(this, 12f), TheoUi.dp(this, 10f))
    code.lines().forEach { line ->
        body.addView(theoMono(if (line.isEmpty()) " " else line, p.foreground, TheoType.CODE_SM))
    }
    scroll.addView(body)
    card.addView(scroll)
    return card
}

data class TheoReviewComment(val file: String, val line: Int, val comment: String, val severity: TheoTone = TheoTone.WARNING)

fun Context.theoCodeReviewPanel(files: List<String>, comments: List<TheoReviewComment>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("scroll-text", p.primary, 16f))
    head.addView(theoText("Code review · ${files.size} files, ${comments.size} comments", TheoType.TITLE_SM, p.foreground))
    card.addView(head)
    val fileList = theoColumn(gap = 1f)
    files.forEach { f ->
        val row = theoRow(gap = 2f)
        row.addView(theoIcon("file-text", p.mutedForeground, 12f))
        row.addView(theoMono(f, p.foreground, TheoType.CODE_SM))
        fileList.addView(row)
    }
    card.addView(fileList)
    comments.forEach { c ->
        val item = theoCard(bg = TheoTokens.tint(p.tone(c.severity), 0.06f), paddingUnits = 2.5f)
        val headRow = theoRow(gap = 2f)
        headRow.addView(theoBadge(c.severity.name, c.severity))
        headRow.addView(theoMono("${c.file}:${c.line}", p.mutedForeground, TheoType.CODE_SM))
        item.addView(headRow)
        item.addView(theoText(c.comment, TheoType.BODY_SM, p.foreground))
        card.addView(item)
    }
    TheoRowGap.apply(card, this)
    return card
}

data class TheoTerminalLine(val text: String, val tone: TheoTone = TheoTone.FOREGROUND)

fun Context.theoTerminalPanel(lines: List<TheoTerminalLine>, title: String = "Terminal"): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 0f)
    val head = theoRow(gap = 2f)
    head.setPadding(TheoUi.dp(this, 10f), TheoUi.dp(this, 8f), TheoUi.dp(this, 10f), TheoUi.dp(this, 8f))
    head.addView(theoIcon("terminal", p.success, 14f))
    val t = theoText(title, TheoType.LABEL, p.mutedForeground)
    t.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(t)
    head.addView(theoIcon("x", p.mutedForeground, 12f))
    card.addView(head)
    val body = theoColumn(gap = 0f)
    body.setBackgroundColor(TheoTokens.DARK.background)
    body.setPadding(TheoUi.dp(this, 12f), TheoUi.dp(this, 10f), TheoUi.dp(this, 12f), TheoUi.dp(this, 10f))
    lines.forEach { line ->
        val color = if (line.tone == TheoTone.FOREGROUND) TheoTokens.DARK.foreground else p.tone(line.tone)
        body.addView(theoMono(if (line.text.isEmpty()) " " else line.text, color, TheoType.CODE_SM))
    }
    card.addView(body)
    return card
}

fun Context.theoBuildLogStream(lines: List<Pair<String, TheoRunStatus>>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("hammer", p.primary, 14f))
    head.addView(theoText("Build log", TheoType.LABEL, p.mutedForeground))
    card.addView(head)
    val scroll = ScrollView(this)
    val col = theoColumn(gap = 0.5f)
    lines.forEach { (text, status) ->
        val row = theoRow(gap = 1.5f, gravity = Gravity.TOP)
        val color = when (status) {
            TheoRunStatus.SUCCEEDED -> p.success
            TheoRunStatus.FAILED -> p.failed
            TheoRunStatus.RUNNING -> p.running
            else -> p.mutedForeground
        }
        row.addView(theoIcon(
            when (status) {
                TheoRunStatus.SUCCEEDED -> "check"
                TheoRunStatus.FAILED -> "x"
                TheoRunStatus.RUNNING -> "loader"
                else -> "circle-dashed"
            },
            color, 12f,
        ))
        row.addView(theoMono(text, color, TheoType.CODE_SM))
        col.addView(row)
    }
    scroll.addView(col)
    card.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, TheoUi.dp(this, 120f)))
    return card
}

fun Context.theoCreatedFilesCard(files: List<Pair<String, String>>): View {
    val p = theoPalette()
    val card = theoCard(bg = TheoTokens.tint(p.success, 0.05f), paddingUnits = 4f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("file-plus", p.success, 16f))
    head.addView(theoText("Created ${files.size} files", TheoType.TITLE_SM, p.foreground))
    card.addView(head)
    files.forEach { (name, size) ->
        val row = theoRow(gap = 2f)
        row.addView(theoIcon("file-text", p.success, 12f))
        val n = theoMono(name, p.foreground, TheoType.CODE_SM)
        n.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        row.addView(n)
        row.addView(theoMono(size, p.mutedForeground, TheoType.CODE_SM))
        card.addView(row)
    }
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoArtifactPreview(name: String, kind: String, url: String? = null): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val icon = when (kind) {
        "image" -> "image"
        "video" -> "film"
        "code" -> "code"
        "archive" -> "box"
        else -> "file-text"
    }
    val head = theoRow(gap = 2f)
    head.addView(theoIcon(icon, p.primary, 16f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoText(name, TheoType.BODY, p.foreground))
    if (url != null) mid.addView(theoMono(url, p.mutedForeground, TheoType.CODE_SM, maxLines = 1))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(mid)
    head.addView(theoIcon("external-link", p.mutedForeground, 14f))
    card.addView(head)
    return card
}

/* ── stream parts ─────────────────────────────────────────────────── */

fun Context.theoTextPart(text: String): View {
    val p = theoPalette()
    return theoText(text, TheoType.BODY, p.foreground)
}

fun Context.theoReasoningPart(text: String, thinking: Boolean = false): View {
    val p = theoPalette()
    val col = theoColumn(gap = 1f)
    val head = theoRow(gap = 1.5f)
    head.addView(theoIcon("brain", p.mutedForeground, 12f))
    head.addView(theoText("Reasoning", TheoType.MICRO, p.mutedForeground))
    col.addView(head)
    col.addView(theoText(text, TheoType.BODY_SM, p.mutedForeground))
    return col
}

fun Context.theoDataPart(json: String): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    card.addView(theoBadge("data", TheoTone.MUTED, iconName = "database"))
    card.addView(theoMono(json, p.mutedForeground, TheoType.CODE_SM, maxLines = 4))
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoFilePart(name: String, mime: String, size: String? = null): View {
    val p = theoPalette()
    val row = theoRow(gap = 2f)
    row.addView(theoIcon("file-text", p.primary, 14f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoText(name, TheoType.BODY_SM, p.foreground))
    mid.addView(theoText(mime + (size?.let { " · $it" } ?: ""), TheoType.MICRO, p.mutedForeground))
    row.addView(mid)
    row.addView(theoIcon("download", p.mutedForeground, 14f))
    return row
}

fun Context.theoSourceDocumentPart(title: String, snippet: String): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("book-open", p.accent, 14f))
    head.addView(theoText(title, TheoType.BODY_SM, p.foreground))
    card.addView(head)
    card.addView(theoText(snippet, TheoType.BODY_SM, p.mutedForeground, maxLines = 3))
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoSourceUrlPart(title: String, url: String): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("globe", p.primary, 14f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoText(title, TheoType.BODY_SM, p.foreground))
    mid.addView(theoMono(url, p.mutedForeground, TheoType.CODE_SM, maxLines = 1))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(mid)
    head.addView(theoIcon("external-link", p.mutedForeground, 14f))
    card.addView(head)
    return card
}

/* ── approvals & permissions ─────────────────────────────────────── */

enum class TheoRiskLevel(val label: String, val tone: TheoTone) {
    DESTRUCTIVE("destructive", TheoTone.DESTRUCTIVE),
    PROCESS("process", TheoTone.ACCENT),
    NETWORK("network", TheoTone.PRIMARY),
    WORKSPACE_WRITE("workspace_write", TheoTone.WARNING),
    READ("read", TheoTone.SUCCESS),
    LOW("low", TheoTone.SUCCESS),
    MEDIUM("medium", TheoTone.WARNING),
    HIGH("high", TheoTone.DESTRUCTIVE);

    companion object {
        fun from(value: String?): TheoRiskLevel = when (value?.lowercase()) {
            "destructive" -> DESTRUCTIVE
            "process" -> PROCESS
            "network" -> NETWORK
            "workspace_write" -> WORKSPACE_WRITE
            "read" -> READ
            "high" -> HIGH
            "medium" -> MEDIUM
            "low" -> LOW
            else -> MEDIUM
        }
    }
}

fun Context.theoApprovalCard(
    title: String,
    detail: String,
    risk: TheoRiskLevel = TheoRiskLevel.MEDIUM,
    command: String? = null,
    onApprove: (() -> Unit)? = null,
    onDeny: (() -> Unit)? = null,
): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    // risk rail: tinted background + colored left stripe via padding trick
    card.background = TheoUi.roundedBg(
        when (risk) {
            TheoRiskLevel.DESTRUCTIVE -> TheoTokens.tint(p.destructive, 0.08f)
            else -> p.card
        },
        TheoTokens.RADIUS_LG,
        when (risk) {
            TheoRiskLevel.DESTRUCTIVE -> p.destructive
            else -> p.border
        },
        if (risk == TheoRiskLevel.DESTRUCTIVE) 2f else 1f,
    )
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("shield-check", p.tone(risk.tone), 16f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoText(title, TheoType.TITLE_SM, p.foreground))
    mid.addView(theoText(detail, TheoType.BODY_SM, p.mutedForeground))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(mid)
    head.addView(theoBadge(risk.label, risk.tone))
    card.addView(head)
    if (command != null) {
        val pre = theoCard(bg = p.muted, radiusDp = TheoTokens.RADIUS_MD, stroke = false, paddingUnits = 2.5f)
        pre.addView(theoMono(command, p.foreground, TheoType.CODE_SM))
        card.addView(pre)
    }
    val btns = theoRow(gap = 2f, gravity = Gravity.END)
    btns.addView(theoButton("Deny", TheoButtonVariant.OUTLINE, small = true, onClick = onDeny))
    btns.addView(theoButton("Approve", TheoButtonVariant.PRIMARY, small = true, onClick = onApprove))
    card.addView(btns)
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoPermissionMatrix(permissions: List<Triple<String, Boolean, Boolean>>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    card.addView(theoText("Permissions", TheoType.TITLE_SM, p.foreground))
    val header = theoRow(gap = 2f)
    val name = theoText("scope", TheoType.MICRO, p.mutedForeground)
    name.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    header.addView(name)
    header.addView(theoText("ask", TheoType.MICRO, p.mutedForeground))
    header.addView(theoText("allow", TheoType.MICRO, p.mutedForeground))
    card.addView(header)
    permissions.forEach { (scope, ask, allow) ->
        val row = theoRow(gap = 2f)
        val n = theoMono(scope, p.foreground, TheoType.CODE_SM)
        n.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        row.addView(n)
        row.addView(theoIcon(if (ask) "check" else "x", if (ask) p.success else p.mutedForeground, 12f))
        row.addView(theoIcon(if (allow) "check" else "x", if (allow) p.success else p.mutedForeground, 12f))
        card.addView(row)
    }
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoAuditLogEntry(time: String, actor: String, action: String, ok: Boolean = true): View {
    val p = theoPalette()
    val row = theoRow(gap = 2f)
    row.addView(theoMono(time, p.mutedForeground, TheoType.CODE_SM))
    row.addView(theoIcon(if (ok) "check-circle" else "x-circle", if (ok) p.success else p.failed, 12f))
    val mid = theoRow(gap = 1f)
    mid.addView(theoText(actor, TheoType.BODY_SM, p.foreground))
    mid.addView(theoText(action, TheoType.BODY_SM, p.mutedForeground))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    row.addView(mid)
    return row
}
