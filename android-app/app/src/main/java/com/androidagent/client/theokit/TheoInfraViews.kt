package com.androidagent.client.theokit

import android.content.Context
import android.graphics.Color
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout

/**
 * Session, context, models, infrastructure, rules/skills, panels & slides —
 * the remaining theokit component families.
 */

fun Context.theoSessionListItem(title: String, subtitle: String, time: String, active: Boolean = false): View {
    val p = theoPalette()
    val card = theoCard(
        bg = if (active) TheoTokens.tint(p.primary, 0.08f) else p.card,
        stroke = active,
        paddingUnits = 3f,
    )
    if (active) {
        card.background = TheoUi.roundedBg(
            TheoTokens.tint(p.primary, 0.08f),
            TheoTokens.RADIUS_LG,
            p.primary,
        )
    }
    val head = theoRow(gap = 2f)
    val t = theoText(title, TheoType.BODY_SM, p.foreground, maxLines = 1)
    t.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(t)
    head.addView(theoMono(time, p.mutedForeground, TheoType.CODE_SM))
    card.addView(head)
    card.addView(theoText(subtitle, TheoType.MICRO, p.mutedForeground, maxLines = 1))
    return card
}

data class TheoSessionEvent(val time: String, val title: String, val tone: TheoTone = TheoTone.MUTED)

fun Context.theoSessionTimeline(events: List<TheoSessionEvent>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    card.addView(theoText("Session", TheoType.LABEL, p.mutedForeground))
    events.forEach { ev ->
        val row = theoRow(gap = 2f)
        row.addView(theoMono(ev.time, p.mutedForeground, TheoType.CODE_SM))
        val dot = View(this)
        dot.background = TheoUi.roundedBg(p.tone(ev.tone), TheoTokens.RADIUS_FULL)
        dot.layoutParams = LinearLayout.LayoutParams(TheoUi.dp(this, 6f), TheoUi.dp(this, 6f))
        row.addView(dot)
        row.addView(theoText(ev.title, TheoType.BODY_SM, p.foreground))
        card.addView(row)
    }
    TheoRowGap.apply(card, this)
    return card
}

data class TheoContextItem(val label: String, val value: String, val iconName: String = "file-text")

fun Context.theoContextCard(title: String, items: List<TheoContextItem>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    card.addView(theoText(title, TheoType.TITLE_SM, p.foreground))
    val grid = theoColumn(gap = 1.5f)
    items.forEach { item ->
        val row = theoRow(gap = 2f)
        row.addView(theoIcon(item.iconName, p.mutedForeground, 14f))
        val l = theoText(item.label, TheoType.BODY_SM, p.mutedForeground)
        l.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        row.addView(l)
        row.addView(theoMono(item.value, p.foreground, TheoType.CODE_SM))
        grid.addView(row)
    }
    card.addView(grid)
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoContextWindowBar(usedPercent: Int, label: String = "context"): View {
    val p = theoPalette()
    val col = theoColumn(gap = 1f)
    val head = theoRow(gap = 2f)
    val l = theoText("$label $usedPercent%", TheoType.MICRO, p.mutedForeground)
    l.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(l)
    head.addView(theoMono("${usedPercent}/100", p.mutedForeground, TheoType.CODE_SM))
    col.addView(head)
    val tone = when {
        usedPercent >= 90 -> TheoTone.DESTRUCTIVE
        usedPercent >= 75 -> TheoTone.WARNING
        else -> TheoTone.PRIMARY
    }
    col.addView(theoProgressBar(usedPercent / 100f, tone))
    return col
}

fun Context.theoFolderContextCard(path: String, fileCount: Int, sizeLabel: String): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("folder-open", p.primary, 16f))
    head.addView(theoMono(path, p.foreground, TheoType.CODE_SM))
    card.addView(head)
    val stats = theoRow(gap = 3f)
    stats.addView(theoBadge("$fileCount files", TheoTone.MUTED))
    stats.addView(theoBadge(sizeLabel, TheoTone.MUTED))
    card.addView(stats)
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoFolderSelector(folders: List<String>, selected: Int = 0): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    card.addView(theoText("Folders", TheoType.LABEL, p.mutedForeground))
    folders.forEachIndexed { i, f ->
        val row = theoRow(gap = 2f)
        row.addView(theoIcon(if (i == selected) "folder-open" else "folder", if (i == selected) p.primary else p.mutedForeground, 14f))
        row.addView(theoText(f, TheoType.BODY_SM, if (i == selected) p.foreground else p.mutedForeground))
        card.addView(row)
    }
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoRecentFoldersList(folders: List<Pair<String, String>>): View {
    val p = theoPalette()
    val col = theoColumn(gap = 1.5f)
    folders.forEach { (name, path) ->
        val row = theoRow(gap = 2f)
        row.addView(theoIcon("folder", p.mutedForeground, 14f))
        val mid = theoColumn(gap = 0.5f)
        mid.addView(theoText(name, TheoType.BODY_SM, p.foreground))
        mid.addView(theoMono(path, p.mutedForeground, TheoType.CODE_SM, maxLines = 1))
        row.addView(mid)
        col.addView(row)
    }
    return col
}

fun Context.theoProjectSwitcher(project: String, role: String = "owner"): View {
    val p = theoPalette()
    val row = theoRow(gap = 2f)
    row.addView(theoIcon("box", p.primary, 16f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoText(project, TheoType.BODY, p.foreground))
    mid.addView(theoText(role, TheoType.MICRO, p.mutedForeground))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    row.addView(mid)
    row.addView(theoIcon("chevron-down", p.mutedForeground, 14f))
    return row
}

fun Context.theoChannelCard(name: String, kind: String, connected: Boolean): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    val icon = when (kind) {
        "web" -> "globe"
        "cli" -> "terminal"
        "api" -> "link"
        "mobile" -> "message-square"
        else -> "wifi"
    }
    head.addView(theoIcon(icon, p.primary, 16f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoText(name, TheoType.BODY_SM, p.foreground))
    mid.addView(theoText(kind, TheoType.MICRO, p.mutedForeground))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(mid)
    head.addView(theoBadge(if (connected) "connected" else "offline", if (connected) TheoTone.SUCCESS else TheoTone.MUTED, filled = connected))
    card.addView(head)
    return card
}

fun Context.theoGatewayStatusIndicator(status: String, latencyMs: Int? = null): View {
    val p = theoPalette()
    val tone = when (status.lowercase()) {
        "healthy", "up" -> TheoTone.SUCCESS
        "degraded" -> TheoTone.WARNING
        "down", "error" -> TheoTone.DESTRUCTIVE
        else -> TheoTone.MUTED
    }
    val row = theoRow(gap = 1.5f)
    val dot = View(this)
    dot.background = TheoUi.roundedBg(p.tone(tone), TheoTokens.RADIUS_FULL)
    dot.layoutParams = LinearLayout.LayoutParams(TheoUi.dp(this, 8f), TheoUi.dp(this, 8f))
    row.addView(dot)
    row.addView(theoText("gateway: $status", TheoType.MICRO, p.mutedForeground))
    if (latencyMs != null) row.addView(theoMono("${latencyMs}ms", p.mutedForeground, TheoType.CODE_SM))
    return row
}

data class TheoModelInfo(
    val name: String,
    val provider: String,
    val contextWindow: String,
    val capabilities: List<String> = emptyList(),
)

fun Context.theoModelCard(model: TheoModelInfo, selected: Boolean = false): View {
    val p = theoPalette()
    val card = theoCard(
        bg = if (selected) TheoTokens.tint(p.primary, 0.06f) else p.card,
        paddingUnits = 3f,
    )
    if (selected) card.background = TheoUi.roundedBg(TheoTokens.tint(p.primary, 0.06f), TheoTokens.RADIUS_LG, p.primary)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("cpu", p.primary, 16f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoText(model.name, TheoType.BODY, p.foreground))
    mid.addView(theoText(model.provider, TheoType.MICRO, p.mutedForeground))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(mid)
    if (selected) head.addView(theoIcon("check-circle", p.primary, 16f))
    card.addView(head)
    card.addView(theoMono("context ${model.contextWindow}", p.mutedForeground, TheoType.CODE_SM))
    if (model.capabilities.isNotEmpty()) {
        val chips = theoRow(gap = 1.5f)
        model.capabilities.forEach { c -> chips.addView(theoBadge(c, TheoTone.MUTED)) }
        card.addView(chips)
    }
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoModelSelector(models: List<TheoModelInfo>, selectedIndex: Int = 0): View {
    val col = theoColumn(gap = 2f)
    models.forEachIndexed { i, m -> col.addView(theoModelCard(m, i == selectedIndex)) }
    return col
}

/** TokenUsageChart: simple stacked bars from per-day usage. */
fun Context.theoTokenUsageChart(data: List<Pair<String, Int>>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    card.addView(theoText("Token usage (7d)", TheoType.TITLE_SM, p.foreground))
    val max = (data.maxOfOrNull { it.second } ?: 1).coerceAtLeast(1)
    val bars = theoRow(gap = 1.5f, gravity = Gravity.BOTTOM)
    data.forEach { (day, value) ->
        val col = theoColumn(gap = 0.5f)
        col.gravity = Gravity.CENTER_HORIZONTAL
        val bar = View(this)
        val h = Math.max(4f, 64f * value / max)
        bar.background = TheoUi.roundedBg(p.primary, TheoTokens.RADIUS_SM)
        bar.layoutParams = LinearLayout.LayoutParams(TheoUi.dp(this, 18f), TheoUi.dp(this, h))
        col.addView(bar)
        col.addView(theoText(day, TheoType.MICRO, p.mutedForeground))
        bars.addView(col)
    }
    card.addView(bars)
    return card
}

fun Context.theoCostMeter(spentCents: Int, budgetCents: Int): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("coins", p.warning, 14f))
    val l = theoText("$${"%.2f".format(spentCents / 100f)} / $${"%.2f".format(budgetCents / 100f)}", TheoType.BODY_SM, p.foreground)
    l.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(l)
    head.addView(theoBadge("${spentCents * 100 / budgetCents.coerceAtLeast(1)}%", TheoTone.WARNING))
    card.addView(head)
    card.addView(theoProgressBar(spentCents.toFloat() / budgetCents.coerceAtLeast(1), TheoTone.WARNING))
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoUsageMeter(usedPercent: Int, usedLabel: String, totalLabel: String): View {
    val p = theoPalette()
    val col = theoColumn(gap = 1f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("activity", p.primary, 14f))
    val l = theoText("$usedLabel / $totalLabel", TheoType.BODY_SM, p.foreground)
    l.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(l)
    head.addView(theoMono("$usedPercent%", p.mutedForeground, TheoType.CODE_SM))
    col.addView(head)
    col.addView(theoProgressBar(usedPercent / 100f))
    return col
}

data class TheoMcpServer(val name: String, val transport: String, val tools: Int, val connected: Boolean)

fun Context.theoMcpServerCard(server: TheoMcpServer): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("server", p.primary, 16f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoText(server.name, TheoType.BODY, p.foreground))
    mid.addView(theoText(server.transport, TheoType.MICRO, p.mutedForeground))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(mid)
    head.addView(theoBadge(if (server.connected) "connected" else "offline", if (server.connected) TheoTone.SUCCESS else TheoTone.MUTED, filled = server.connected))
    card.addView(head)
    card.addView(theoText("${server.tools} tools", TheoType.BODY_SM, p.mutedForeground))
    return card
}

fun Context.theoMcpServerList(servers: List<TheoMcpServer>): View {
    val col = theoColumn(gap = 2f)
    servers.forEach { col.addView(theoMcpServerCard(it)) }
    return col
}

data class TheoCronJob(val name: String, val schedule: String, val nextRun: String, val enabled: Boolean = true)

fun Context.theoCronJobCard(job: TheoCronJob): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("calendar", p.primary, 14f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoText(job.name, TheoType.BODY, p.foreground))
    mid.addView(theoMono(job.schedule, p.mutedForeground, TheoType.CODE_SM))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(mid)
    head.addView(theoBadge(if (job.enabled) "on" else "off", if (job.enabled) TheoTone.SUCCESS else TheoTone.MUTED))
    card.addView(head)
    card.addView(theoText("next: ${job.nextRun}", TheoType.MICRO, p.mutedForeground))
    return card
}

fun Context.theoCronJobsList(jobs: List<TheoCronJob>): View {
    val col = theoColumn(gap = 2f)
    jobs.forEach { col.addView(theoCronJobCard(it)) }
    return col
}

data class TheoHook(val event: String, val command: String, val enabled: Boolean = true)

fun Context.theoHookConfig(hooks: List<TheoHook>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    card.addView(theoText("Hooks", TheoType.TITLE_SM, p.foreground))
    hooks.forEach { hook ->
        val row = theoRow(gap = 2f)
        row.addView(theoIcon(if (hook.enabled) "zap" else "minus", if (hook.enabled) p.warning else p.mutedForeground, 12f))
        val mid = theoColumn(gap = 0.5f)
        mid.addView(theoMono(hook.event, p.foreground, TheoType.CODE_SM))
        mid.addView(theoMono(hook.command, p.mutedForeground, TheoType.CODE_SM, maxLines = 1))
        mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        row.addView(mid)
        card.addView(row)
    }
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoHookEventLog(entries: List<Triple<String, String, Boolean>>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    card.addView(theoText("Hook events", TheoType.LABEL, p.mutedForeground))
    entries.forEach { (event, time, ok) ->
        val row = theoRow(gap = 2f)
        row.addView(theoIcon(if (ok) "check-circle" else "x-circle", if (ok) p.success else p.failed, 12f))
        row.addView(theoMono(event, p.foreground, TheoType.CODE_SM))
        val t = theoMono(time, p.mutedForeground, TheoType.CODE_SM)
        t.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        t.gravity = Gravity.END
        row.addView(t)
        card.addView(row)
    }
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoBrowserControls(url: String, loading: Boolean = false): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val bar = theoRow(gap = 2f)
    bar.addView(theoIcon("chevron-left", p.mutedForeground, 14f))
    bar.addView(theoIcon("chevron-right", p.mutedForeground, 14f))
    bar.addView(theoIcon("refresh", p.mutedForeground, 14f))
    val u = theoMono(url, p.foreground, TheoType.CODE_SM, maxLines = 1)
    u.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    bar.addView(u)
    if (loading) bar.addView(theoIcon("loader", p.primary, 14f))
    card.addView(bar)
    return card
}

/** StabilityBundleViewer: manifest of bundle parts with status. */
fun Context.theoStabilityBundleViewer(bundle: String, parts: List<Triple<String, String, Boolean>>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("box", p.primary, 16f))
    head.addView(theoText(bundle, TheoType.TITLE_SM, p.foreground))
    card.addView(head)
    parts.forEach { (name, hash, ok) ->
        val row = theoRow(gap = 2f)
        row.addView(theoIcon(if (ok) "check" else "alert-triangle", if (ok) p.success else p.failed, 12f))
        row.addView(theoText(name, TheoType.BODY_SM, p.foreground))
        val h = theoMono(hash.take(12), p.mutedForeground, TheoType.CODE_SM)
        h.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        h.gravity = Gravity.END
        row.addView(h)
        card.addView(row)
    }
    TheoRowGap.apply(card, this)
    return card
}

/** Whiteboard: canvas placeholder with shape legend (static preview). */
fun Context.theoWhiteboard(elements: List<Pair<String, String>>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("layers", p.primary, 16f))
    head.addView(theoText("Whiteboard · ${elements.size} elements", TheoType.LABEL, p.foreground))
    card.addView(head)
    val canvas = LinearLayout(this)
    canvas.orientation = LinearLayout.VERTICAL
    canvas.minimumHeight = TheoUi.dp(this, 120f)
    canvas.background = TheoUi.roundedBg(p.muted, TheoTokens.RADIUS_MD)
    canvas.setPadding(TheoUi.dp(this, 12f), TheoUi.dp(this, 12f), TheoUi.dp(this, 12f), TheoUi.dp(this, 12f))
    elements.take(4).forEach { (kind, label) ->
        val row = theoRow(gap = 2f)
        val shape = View(this)
        shape.background = when (kind) {
            "circle" -> TheoUi.roundedBg(p.accent, TheoTokens.RADIUS_FULL)
            "diamond" -> TheoUi.roundedBg(p.success, 4f)
            else -> TheoUi.roundedBg(p.primary, TheoTokens.RADIUS_SM)
        }
        shape.layoutParams = LinearLayout.LayoutParams(TheoUi.dp(this, 10f), TheoUi.dp(this, 10f))
        row.addView(shape)
        row.addView(theoText(label, TheoType.BODY_SM, p.foreground))
        canvas.addView(row)
    }
    card.addView(canvas)
    TheoRowGap.apply(card, this)
    return card
}

/** PreviewPanel: side-by-side preview with device selector. */
fun Context.theoPreviewPanel(title: String, mode: String = "desktop"): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("eye", p.primary, 16f))
    val t = theoText(title, TheoType.BODY, p.foreground)
    t.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(t)
    head.addView(theoBadge(mode, TheoTone.MUTED))
    card.addView(head)
    val frame = View(this)
    frame.minimumHeight = TheoUi.dp(this, 96f)
    frame.background = TheoUi.roundedBg(p.muted, TheoTokens.RADIUS_MD)
    card.addView(frame)
    TheoRowGap.apply(card, this)
    return card
}

/* ── editors: memory / system prompt / rules / skills ─────────────── */

fun Context.theoMemoryEditor(entries: List<Pair<String, String>>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    card.addView(theoText("Memory", TheoType.TITLE_SM, p.foreground))
    entries.forEach { (key, value) ->
        val row = theoRow(gap = 2f)
        row.addView(theoIcon("brain", p.mutedForeground, 12f))
        row.addView(theoMono(key, p.foreground, TheoType.CODE_SM))
        val v = theoText(value, TheoType.BODY_SM, p.mutedForeground, maxLines = 2)
        v.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        row.addView(v)
        card.addView(row)
    }
    card.addView(theoButton("Add memory", TheoButtonVariant.OUTLINE, iconName = "plus", small = true))
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoSystemPromptEditor(prompt: String): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("sparkles", p.primary, 14f))
    head.addView(theoText("System prompt", TheoType.TITLE_SM, p.foreground))
    card.addView(head)
    val body = theoCard(bg = p.muted, radiusDp = TheoTokens.RADIUS_MD, stroke = false, paddingUnits = 3f)
    body.addView(theoMono(prompt, p.foreground, TheoType.CODE_SM))
    card.addView(body)
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoRuleCard(name: String, pattern: String, enabled: Boolean = true): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("shield-check", if (enabled) p.success else p.mutedForeground, 14f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoText(name, TheoType.BODY_SM, p.foreground))
    mid.addView(theoMono(pattern, p.mutedForeground, TheoType.CODE_SM, maxLines = 1))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(mid)
    head.addView(theoBadge(if (enabled) "on" else "off", if (enabled) TheoTone.SUCCESS else TheoTone.MUTED))
    card.addView(head)
    return card
}

fun Context.theoRuleEditor(rules: List<Pair<String, String>>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    card.addView(theoText("Rules", TheoType.TITLE_SM, p.foreground))
    rules.forEach { (name, pattern) -> card.addView(theoRuleCard(name, pattern)) }
    card.addView(theoButton("Add rule", TheoButtonVariant.OUTLINE, iconName = "plus", small = true))
    TheoRowGap.apply(card, this)
    return card
}

data class TheoSkill(val name: String, val description: String, val triggers: List<String>)

fun Context.theoSkillCard(skill: TheoSkill): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("sparkles", p.primary, 16f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoText(skill.name, TheoType.BODY, p.foreground))
    mid.addView(theoText(skill.description, TheoType.MICRO, p.mutedForeground, maxLines = 2))
    mid.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    head.addView(mid)
    card.addView(head)
    if (skill.triggers.isNotEmpty()) {
        val chips = theoRow(gap = 1.5f)
        skill.triggers.forEach { t -> chips.addView(theoBadge(t, TheoTone.PRIMARY)) }
        card.addView(chips)
    }
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoSkillEditor(skill: TheoSkill): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    card.addView(theoText(skill.name, TheoType.TITLE_SM, p.foreground))
    card.addView(theoText(skill.description, TheoType.BODY_SM, p.mutedForeground))
    val chips = theoRow(gap = 1.5f)
    skill.triggers.forEach { t ->
        val chip = theoRow(gap = 1f)
        chip.addView(theoText(t, TheoType.MICRO, p.primary))
        chip.addView(theoIcon("x", p.mutedForeground, 10f))
        chip.setPadding(TheoUi.dp(this, 8f), TheoUi.dp(this, 3f), TheoUi.dp(this, 8f), TheoUi.dp(this, 3f))
        chip.background = TheoUi.pillBg(TheoTokens.tint(p.primary, 0.10f))
        chips.addView(chip)
    }
    card.addView(chips)
    card.addView(theoButton("Save skill", TheoButtonVariant.PRIMARY, small = true))
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoSkillsList(skills: List<TheoSkill>): View {
    val col = theoColumn(gap = 2f)
    skills.forEach { col.addView(theoSkillCard(it)) }
    return col
}

/* ── slides ───────────────────────────────────────────────────────── */

object TheoSlides {

    data class Slide(val title: String, val subtitle: String?, val bullets: List<String>)

    /** Split markdown into slides on `---`; first `#` is title, `##` subtitle. */
    fun parse(markdown: String): List<Slide> {
        val slides = mutableListOf<Slide>()
        markdown.split(Regex("(?m)^---\\s*$")).forEach { block ->
            val lines = block.lines().map { it.trim() }.filter { it.isNotEmpty() }
            if (lines.isEmpty()) return@forEach
            var title = ""
            var subtitle: String? = null
            val bullets = mutableListOf<String>()
            lines.forEach { line ->
                when {
                    line.startsWith("# ") && title.isEmpty() -> title = line.removePrefix("# ").trim()
                    line.startsWith("## ") && subtitle == null -> subtitle = line.removePrefix("## ").trim()
                    line.startsWith("- ") || line.startsWith("* ") -> bullets += line.drop(2).trim()
                    title.isEmpty() -> title = line
                    else -> bullets += line
                }
            }
            if (title.isNotEmpty() || bullets.isNotEmpty()) {
                slides += Slide(title.ifEmpty { "Untitled" }, subtitle, bullets)
            }
        }
        return slides
    }
}

fun Context.theoSlide(slide: TheoSlides.Slide): View {
    val p = theoPalette()
    val card = theoCard(radiusDp = TheoTokens.RADIUS_LG, paddingUnits = 6f)
    card.minimumHeight = TheoUi.dp(this, 140f)
    val head = theoColumn(gap = 1f)
    head.addView(theoText(slide.title, TheoType.DISPLAY, p.foreground))
    if (slide.subtitle != null) head.addView(theoText(slide.subtitle, TheoType.BODY, p.mutedForeground))
    card.addView(head)
    if (slide.bullets.isNotEmpty()) {
        val list = theoColumn(gap = 1.5f)
        slide.bullets.forEach { b ->
            val row = theoRow(gap = 2f)
            row.addView(theoIcon("chevron-right", p.primary, 12f))
            row.addView(theoText(b, TheoType.BODY, p.foreground))
            list.addView(row)
        }
        card.addView(list)
    }
    return card
}

fun Context.theoSlideDeck(markdown: String): View {
    val p = theoPalette()
    val slides = TheoSlides.parse(markdown)
    val col = theoColumn(gap = 2f)
    col.addView(theoBadge("${slides.size} slides", TheoTone.PRIMARY, iconName = "film"))
    slides.firstOrNull()?.let { col.addView(theoSlide(it)) }
    if (slides.size > 1) {
        val nav = theoRow(gap = 2f)
        nav.addView(theoButton("‹ Prev", TheoButtonVariant.GHOST, small = true))
        nav.addView(theoMono("1 / ${slides.size}", p.mutedForeground, TheoType.CODE_SM))
        nav.addView(theoButton("Next ›", TheoButtonVariant.SECONDARY, small = true))
        col.addView(nav)
    }
    return col
}
