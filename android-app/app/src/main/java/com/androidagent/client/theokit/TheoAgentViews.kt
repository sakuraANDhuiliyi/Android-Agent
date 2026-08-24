package com.androidagent.client.theokit

import android.content.Context
import android.graphics.Color
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ScrollView

/**
 * Agent status & events family — Kotlin migration of theokit primitives:
 * AgentErrorCard, AgentEvent, AgentStartingState, AgentStreaming, AgentStream,
 * AgentTimeline, AgentToolRenderer, AgentProfile, AgentHandoff, SubAgentDispatch,
 * CapabilityIndicator, RunStatusPill, RunStats, TaskPlan, TaskNode,
 * ProgressChecklist, StepsRail, WorkLog, RunningTasksPanel, LaneBoard.
 */

fun Context.theoAgentErrorCard(
    title: String,
    detail: String? = null,
    kind: String = "unknown",
    timestamp: String? = null,
    actions: List<Pair<String, TheoButtonVariant>> = emptyList(),
): View {
    val p = theoPalette()
    val card = theoCard(bg = TheoTokens.tint(p.destructive, 0.06f), paddingUnits = 4f)
    val row = theoRow(gap = 3f, gravity = Gravity.TOP)
    row.addView(theoIcon("alert-triangle", p.destructive, 16f))
    val body = theoColumn(gap = 1f)
    val head = theoRow(gap = 2f)
    val titleColor = p.destructive
    head.addView(theoText(title, TheoType.TITLE_SM, titleColor))
    if (timestamp != null) {
        val ts = theoMono(timestamp, p.mutedForeground, TheoType.CODE_SM)
        head.addView(ts)
        (ts.layoutParams as LinearLayout.LayoutParams).gravity = Gravity.BOTTOM
    }
    body.addView(head)
    if (detail != null) body.addView(theoMono(detail, p.mutedForeground, TheoType.CODE_SM, maxLines = 3))
    row.addView(body)
    card.addView(row)
    if (actions.isNotEmpty()) {
        val btns = theoRow(gap = 2f, gravity = Gravity.END)
        actions.forEach { (label, variant) -> btns.addView(theoButton(label, variant, small = true)) }
        card.addView(btns)
    }
    TheoRowGap.apply(card, this)
    return card
}

enum class TheoEventStatus { PENDING, RUNNING, SUCCESS, FAILED }

fun Context.theoAgentEvent(
    title: String,
    status: TheoEventStatus = TheoEventStatus.SUCCESS,
    iconName: String = "terminal",
    detail: String? = null,
    timestamp: String? = null,
): View {
    val p = theoPalette()
    val (icon, color) = when (status) {
        TheoEventStatus.PENDING -> "circle-dot" to p.mutedForeground
        TheoEventStatus.RUNNING -> "loader" to p.running
        TheoEventStatus.SUCCESS -> "check-circle" to p.success
        TheoEventStatus.FAILED -> "alert-triangle" to p.failed
    }
    val row = theoRow(gap = 2.5f)
    row.addView(theoIcon(icon, color, 14f))
    val mid = theoColumn(gap = 0.5f)
    val head = theoRow(gap = 2f)
    head.addView(theoText(title, TheoType.BODY_SM, p.foreground))
    if (timestamp != null) head.addView(theoMono(timestamp, p.mutedForeground, TheoType.CODE_SM))
    mid.addView(head)
    if (detail != null) mid.addView(theoMono(detail, p.mutedForeground, TheoType.CODE_SM, maxLines = 2))
    val midLp = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    mid.layoutParams = midLp
    row.addView(mid)
    row.addView(theoIcon("chevron-down", p.mutedForeground, 14f))
    return row
}

fun Context.theoAgentStartingState(title: String, subtitle: String, steps: List<String>): View {
    val p = theoPalette()
    val col = theoColumn(gap = 3f)
    val icon = theoIcon("sparkles", p.primary, 28f)
    icon.layoutParams = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
    )
    col.addView(icon)
    col.addView(theoText(title, TheoType.TITLE_MD, p.foreground))
    col.addView(theoText(subtitle, TheoType.BODY_SM, p.mutedForeground))
    if (steps.isNotEmpty()) {
        val list = theoColumn(gap = 1.5f)
        steps.forEach { step ->
            val r = theoRow(gap = 2f)
            r.addView(theoIcon("loader", p.primary, 14f))
            r.addView(theoText(step, TheoType.BODY_SM, p.mutedForeground))
            list.addView(r)
        }
        col.addView(list)
    }
    return col
}

fun Context.theoAgentStreaming(label: String = "Thinking"): View {
    val p = theoPalette()
    val row = theoRow(gap = 2f)
    row.addView(theoIcon("loader", p.primary, 14f))
    row.addView(theoText(label, TheoType.BODY_SM, p.primary))
    return row
}

/** AgentStream: streaming text with a trailing cursor. */
fun Context.theoAgentStream(text: String, streaming: Boolean = true): View {
    val p = theoPalette()
    val col = theoColumn(gap = 1f)
    col.addView(theoText(text, TheoType.BODY, p.foreground))
    if (streaming) {
        val cursor = theoMono("▌", p.primary, TheoType.CODE)
        col.addView(cursor)
    }
    return col
}

data class TheoTimelineEntry(
    val title: String,
    val status: TheoEventStatus,
    val iconName: String,
    val detail: String? = null,
    val timestamp: String? = null,
)

/** AgentTimeline: vertical rail of events with status dots. */
fun Context.theoAgentTimeline(entries: List<TheoTimelineEntry>): View {
    val p = theoPalette()
    val col = theoColumn(gap = 0f)
    entries.forEachIndexed { idx, entry ->
        val item = theoRow(gap = 3f)
        // rail column: dot + connector
        val rail = LinearLayout(this)
        rail.orientation = LinearLayout.VERTICAL
        rail.gravity = Gravity.CENTER_HORIZONTAL
        val dotColor = when (entry.status) {
            TheoEventStatus.PENDING -> p.mutedForeground
            TheoEventStatus.RUNNING -> p.running
            TheoEventStatus.SUCCESS -> p.success
            TheoEventStatus.FAILED -> p.failed
        }
        val dot = View(this)
        dot.background = TheoUi.roundedBg(dotColor, TheoTokens.RADIUS_FULL)
        dot.layoutParams = LinearLayout.LayoutParams(TheoUi.dp(this, 8f), TheoUi.dp(this, 8f))
        rail.addView(dot)
        if (idx < entries.size - 1) {
            val connector = View(this)
            connector.setBackgroundColor(p.border)
            connector.layoutParams = LinearLayout.LayoutParams(
                Math.max(1, TheoUi.dp(this, 1f)),
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
            rail.addView(connector)
        }
        item.addView(rail)
        item.addView(theoAgentEvent(entry.title, entry.status, entry.iconName, entry.detail, entry.timestamp))
        col.addView(item)
    }
    return col
}

fun Context.theoAgentToolRenderer(tool: String, input: String): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("wrench", p.primary, 14f))
    head.addView(theoMono(tool, p.foreground, TheoType.CODE_SM))
    card.addView(head)
    card.addView(theoMono(input, p.mutedForeground, TheoType.CODE_SM, maxLines = 2))
    return card
}

fun Context.theoAgentProfile(name: String, role: String, model: String? = null): View {
    val p = theoPalette()
    val row = theoRow(gap = 3f)
    val avatar = LinearLayout(this)
    avatar.background = TheoUi.roundedBg(p.primary, TheoTokens.RADIUS_FULL)
    val av = theoText(name.take(1).uppercase(), TheoType.BODY, p.primaryForeground)
    av.gravity = Gravity.CENTER
    avatar.addView(av)
    avatar.layoutParams = LinearLayout.LayoutParams(TheoUi.dp(this, 36f), TheoUi.dp(this, 36f))
    row.addView(avatar)
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoText(name, TheoType.BODY, p.foreground))
    mid.addView(theoText(role, TheoType.MICRO, p.mutedForeground))
    if (model != null) mid.addView(theoMono(model, p.mutedForeground, TheoType.CODE_SM))
    row.addView(mid)
    return row
}

fun Context.theoAgentHandoff(from: String, to: String, reason: String? = null): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("corner-down-right", p.accent, 14f))
    val label = theoText("$from → $to", TheoType.BODY_SM, p.foreground)
    head.addView(label)
    card.addView(head)
    if (reason != null) card.addView(theoText(reason, TheoType.BODY_SM, p.mutedForeground))
    return card
}

fun Context.theoSubAgentDispatch(agent: String, task: String, status: TheoRunStatus = TheoRunStatus.RUNNING): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("bot", p.primary, 14f))
    val mid = theoColumn(gap = 0.5f)
    mid.addView(theoText(agent, TheoType.BODY_SM, p.foreground))
    mid.addView(theoText(task, TheoType.BODY_SM, p.mutedForeground))
    head.addView(mid)
    card.addView(head)
    card.addView(theoRunStatusPill(status))
    return card
}

fun Context.theoCapabilityIndicator(capabilities: List<Pair<String, Boolean>>): View {
    val p = theoPalette()
    val row = theoRow(gap = 2f)
    capabilities.forEach { (name, enabled) ->
        val chip = theoBadge(name, if (enabled) TheoTone.SUCCESS else TheoTone.MUTED, iconName = if (enabled) "check" else "x")
        row.addView(chip)
    }
    return row
}

fun Context.theoRunStatusPill(status: TheoRunStatus): View {
    val p = theoPalette()
    return theoBadge(status.label, status.tone, iconName = status.icon)
}

fun Context.theoRunStats(duration: String? = null, tokens: String? = null, filesChanged: Int? = null): View {
    val p = theoPalette()
    val row = theoRow(gap = 3f)
    if (duration != null) {
        val r = theoRow(gap = 1f)
        r.addView(theoIcon("clock", p.mutedForeground, 12f))
        r.addView(theoMono(duration, p.mutedForeground, TheoType.CODE_SM))
        row.addView(r)
    }
    if (tokens != null) {
        val r = theoRow(gap = 1f)
        r.addView(theoIcon("coins", p.mutedForeground, 12f))
        r.addView(theoMono("$tokens tokens", p.mutedForeground, TheoType.CODE_SM))
        row.addView(r)
    }
    if (filesChanged != null) {
        val r = theoRow(gap = 1f)
        r.addView(theoIcon("file-edit", p.mutedForeground, 12f))
        r.addView(theoMono("$filesChanged files", p.mutedForeground, TheoType.CODE_SM))
        row.addView(r)
    }
    return row
}

data class TheoTaskNodeData(
    val title: String,
    val status: TheoRunStatus = TheoRunStatus.PENDING,
    val children: List<TheoTaskNodeData> = emptyList(),
    val depth: Int = 0,
)

private fun renderTaskNode(ctx: Context, node: TheoTaskNodeData, out: LinearLayout) {
    val p = ctx.theoPalette()
    val row = ctx.theoRow(gap = 2f)
    row.setPadding(TheoUi.dp(ctx, 1.5f * node.depth * 4f), 0, 0, 0)
    val (icon, color) = when (node.status) {
        TheoRunStatus.SUCCEEDED -> "check-circle" to p.success
        TheoRunStatus.FAILED -> "x-circle" to p.failed
        TheoRunStatus.RUNNING -> "loader" to p.running
        TheoRunStatus.PAUSED, TheoRunStatus.INTERRUPTED -> "pause" to p.warning
        else -> "circle-dot" to p.mutedForeground
    }
    row.addView(ctx.theoIcon(icon, color, 14f))
    row.addView(ctx.theoText(node.title, TheoType.BODY_SM, if (node.status == TheoRunStatus.SUCCEEDED) p.mutedForeground else p.foreground))
    out.addView(row)
    node.children.forEach { renderTaskNode(ctx, it.copy(depth = node.depth + 1), out) }
}

fun Context.theoTaskPlan(title: String, nodes: List<TheoTaskNodeData>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    card.addView(theoText(title, TheoType.TITLE_SM, p.foreground))
    val list = theoColumn(gap = 1.5f)
    nodes.forEach { renderTaskNode(this, it, list) }
    card.addView(list)
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoTaskNode(title: String, status: TheoRunStatus = TheoRunStatus.PENDING): View {
    val p = theoPalette()
    val row = theoRow(gap = 2f)
    val (icon, color) = when (status) {
        TheoRunStatus.SUCCEEDED -> "check-circle" to p.success
        TheoRunStatus.FAILED -> "x-circle" to p.failed
        TheoRunStatus.RUNNING -> "loader" to p.running
        else -> "circle-dot" to p.mutedForeground
    }
    row.addView(theoIcon(icon, color, 14f))
    row.addView(theoText(title, TheoType.BODY_SM, p.foreground))
    return row
}

fun Context.theoProgressChecklist(items: List<Pair<String, Boolean>>): View {
    val p = theoPalette()
    val col = theoColumn(gap = 2f)
    items.forEach { (label, done) ->
        val row = theoRow(gap = 2f)
        row.addView(theoIcon(if (done) "check-circle" else "circle-dashed", if (done) p.success else p.mutedForeground, 14f))
        row.addView(theoText(label, TheoType.BODY_SM, if (done) p.mutedForeground else p.foreground))
        col.addView(row)
    }
    return col
}

/** StepsRail: horizontal numbered steps with current index highlighted. */
fun Context.theoStepsRail(steps: List<String>, current: Int): View {
    val p = theoPalette()
    val row = theoRow(gap = 1.5f)
    steps.forEachIndexed { i, step ->
        val active = i == current
        val done = i < current
        val color = when {
            active -> p.primary
            done -> p.success
            else -> p.mutedForeground
        }
        val chip = LinearLayout(this)
        chip.orientation = LinearLayout.HORIZONTAL
        chip.gravity = Gravity.CENTER_VERTICAL
        val n = theoText("${i + 1}", TheoType.MICRO, if (active) p.primaryForeground else color)
        n.gravity = Gravity.CENTER
        val dot = LinearLayout(this)
        dot.addView(n)
        dot.gravity = Gravity.CENTER
        dot.background = if (active) TheoUi.pillBg(p.primary) else TheoUi.pillBg(TheoTokens.tint(color, 0.12f))
        dot.layoutParams = LinearLayout.LayoutParams(TheoUi.dp(this, 20f), TheoUi.dp(this, 20f))
        chip.addView(dot)
        chip.addView(theoText(step, TheoType.MICRO, color))
        chip.setPadding(TheoUi.dp(this, 2f), TheoUi.dp(this, 2f), TheoUi.dp(this, 8f), TheoUi.dp(this, 2f))
        chip.background = if (active) TheoUi.pillBg(TheoTokens.tint(p.primary, 0.10f)) else null
        row.addView(chip)
    }
    return row
}

data class TheoWorkLogEntry(val time: String, val message: String, val status: TheoEventStatus = TheoEventStatus.SUCCESS)

fun Context.theoWorkLog(entries: List<TheoWorkLogEntry>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    card.addView(theoText("Work log", TheoType.LABEL, p.mutedForeground))
    entries.forEach { entry ->
        val row = theoRow(gap = 2f)
        row.addView(theoMono(entry.time, p.mutedForeground, TheoType.CODE_SM))
        val color = when (entry.status) {
            TheoEventStatus.FAILED -> p.failed
            TheoEventStatus.RUNNING -> p.running
            else -> p.foreground
        }
        val msg = theoText(entry.message, TheoType.CODE_SM, color, maxLines = 2)
        msg.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        row.addView(msg)
        card.addView(row)
    }
    TheoRowGap.apply(card, this)
    return card
}

data class TheoRunningTask(val name: String, val status: TheoRunStatus, val progress: Float? = null)

fun Context.theoRunningTasksPanel(tasks: List<TheoRunningTask>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("activity", p.primary, 16f))
    head.addView(theoText("Running tasks (${tasks.size})", TheoType.TITLE_SM, p.foreground))
    card.addView(head)
    val list = theoColumn(gap = 2.5f)
    tasks.forEach { task ->
        val item = theoColumn(gap = 1f)
        val r = theoRow(gap = 2f)
        r.addView(theoText(task.name, TheoType.BODY_SM, p.foreground))
        r.addView(theoRunStatusPill(task.status))
        item.addView(r)
        if (task.progress != null) item.addView(theoProgressBar(task.progress))
        list.addView(item)
    }
    card.addView(list)
    TheoRowGap.apply(card, this)
    return card
}

data class TheoLane(val title: String, val cards: List<String>)

fun Context.theoLaneBoard(lanes: List<TheoLane>): View {
    val p = theoPalette()
    val row = theoRow(gap = 3f, gravity = Gravity.TOP)
    lanes.forEach { lane ->
        val laneCol = theoColumn(gap = 2f)
        laneCol.addView(theoBadge(lane.title, TheoTone.MUTED))
        lane.cards.forEach { cardText ->
            val c = theoCard(paddingUnits = 3f)
            c.addView(theoText(cardText, TheoType.BODY_SM, p.foreground))
            laneCol.addView(c)
        }
        laneCol.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        row.addView(laneCol)
    }
    return row
}
