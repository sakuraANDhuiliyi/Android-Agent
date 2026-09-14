package com.androidagent.client.theokit

import android.content.Context
import android.graphics.Color
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout

/**
 * Chat & composer family — ChatMessage*, ChatMessageBranch*, ChatThread,
 * ChatComposer, AgentComposer, AgentEditor, prompts (Choice/Confirm/Text/
 * MultiSelect/PermissionModal), selectors, chips and notices.
 */

/** ChatMessageRoot: role-labelled message bubble (user / assistant / system). */
fun Context.theoChatMessageRoot(role: String, content: View, avatarIcon: String? = null): View {
    val p = theoPalette()
    val isUser = role == "user"
    val col = theoColumn(gap = 1.5f)
    val head = theoRow(gap = 2f)
    head.addView(theoBadge(role, if (isUser) TheoTone.PRIMARY else TheoTone.MUTED, iconName = avatarIcon ?: if (isUser) "user" else "bot"))
    col.addView(head)
    val bubble = theoCard(
        bg = if (isUser) TheoTokens.tint(p.primary, 0.06f) else p.card,
        paddingUnits = 3.5f,
    )
    bubble.addView(content)
    col.addView(bubble)
    TheoRowGap.apply(col, this)
    return col
}

fun Context.theoChatMessageContent(text: String): View {
    val p = theoPalette()
    return theoText(text, TheoType.BODY, p.foreground)
}

fun Context.theoChatMessageResponse(text: String, streaming: Boolean = false): View {
    val p = theoPalette()
    val col = theoColumn(gap = 1f)
    col.addView(theoText(text, TheoType.BODY, p.foreground))
    if (streaming) col.addView(theoAgentStreaming())
    return col
}

fun Context.theoChatMessageActions(actions: List<String>): View {
    val p = theoPalette()
    val row = theoRow(gap = 1.5f)
    val iconMap = mapOf(
        "copy" to "copy", "retry" to "refresh", "edit" to "edit-3",
        "branch" to "git-branch", "delete" to "trash", "like" to "star", "share" to "external-link",
    )
    actions.forEach { label ->
        val btn = LinearLayout(this)
        btn.gravity = Gravity.CENTER
        btn.addView(theoIcon(iconMap[label] ?: "more-horizontal", p.mutedForeground, 14f))
        btn.setPadding(TheoUi.dp(this, 6f), TheoUi.dp(this, 6f), TheoUi.dp(this, 6f), TheoUi.dp(this, 6f))
        btn.background = TheoUi.roundedBg(this, p.muted, TheoTokens.RADIUS_MD)
        row.addView(btn)
    }
    return row
}

fun Context.theoChatMessageAction(label: String, iconName: String = "more-horizontal"): View {
    val p = theoPalette()
    val row = theoRow(gap = 1f)
    row.addView(theoIcon(iconName, p.mutedForeground, 14f))
    row.addView(theoText(label, TheoType.MICRO, p.mutedForeground))
    return row
}

fun Context.theoChatMessageToolbar(time: String, tokens: String? = null): View {
    val p = theoPalette()
    val row = theoRow(gap = 2f)
    row.addView(theoMono(time, p.mutedForeground, TheoType.CODE_SM))
    if (tokens != null) row.addView(theoMono("· $tokens", p.mutedForeground, TheoType.CODE_SM))
    return row
}

/** BranchIndicator: "edit 3 of 5" style pill. */
fun Context.theoBranchIndicator(count: Int, current: Int = 0): View {
    val p = theoPalette()
    val row = theoRow(gap = 1f)
    row.addView(theoIcon("git-branch", p.mutedForeground, 12f))
    row.addView(theoMono("${current + 1}/$count", p.mutedForeground, TheoType.CODE_SM))
    return row
}

/**
 * ChatMessageBranch family: renders each branch, shows only the current one,
 * with previous/next buttons + page label ("1 of N") + selector chips.
 */
fun Context.theoChatMessageBranch(
    branches: List<() -> View>,
    onBranchChange: ((Int) -> Unit)? = null,
): View {
    val p = theoPalette()
    val col = theoColumn(gap = 2f)
    if (branches.size <= 1 && branches.isNotEmpty()) {
        col.addView(branches[0]())
        return col
    }
    val stage = theoColumn()
    val branchViews = branches.map { it() }
    branchViews.forEach { stage.addView(it) }
    col.addView(stage)

    val nav = theoRow(gap = 1.5f)
    val prev = theoButton("‹", TheoButtonVariant.GHOST, small = true)
    val page = theoMono("1 of ${branchViews.size}", p.mutedForeground, TheoType.CODE_SM)
    val next = theoButton("›", TheoButtonVariant.GHOST, small = true)
    nav.addView(prev)
    nav.addView(page)
    nav.addView(next)

    val selector = theoRow(gap = 1f)
    val dots = branchViews.map { i ->
        View(this).apply {
            layoutParams = LinearLayout.LayoutParams(TheoUi.dp(this@theoChatMessageBranch, 7f), TheoUi.dp(this@theoChatMessageBranch, 7f))
        }
    }
    dots.forEach { selector.addView(it) }

    var current = 0
    fun apply() {
        branchViews.forEachIndexed { i, v -> v.visibility = if (i == current) View.VISIBLE else View.GONE }
        page.text = "${current + 1} of ${branchViews.size}"
        dots.forEachIndexed { i, d ->
            d.background = TheoUi.roundedBg(this, if (i == current) p.primary else p.border, TheoTokens.RADIUS_FULL)
        }
    }
    prev.setOnClickListener {
        current = (current - 1 + branchViews.size) % branchViews.size
        apply()
        onBranchChange?.invoke(current)
    }
    next.setOnClickListener {
        current = (current + 1) % branchViews.size
        apply()
        onBranchChange?.invoke(current)
    }
    dots.forEachIndexed { i, d ->
        d.setOnClickListener {
            current = i
            apply()
            onBranchChange?.invoke(current)
        }
    }
    col.addView(nav)
    col.addView(selector)
    apply()
    return col
}

fun Context.theoChatThread(messages: List<View>): View {
    val col = theoColumn(gap = 4f)
    messages.forEach { col.addView(it) }
    return col
}

/** ChatComposer: attachment + input + send. */
fun Context.theoChatComposer(hint: String = "Message…", onSend: ((String) -> Unit)? = null): View {
    val p = theoPalette()
    val card = theoCard(radiusDp = TheoTokens.RADIUS_XL, paddingUnits = 2f)
    val row = theoRow(gap = 2f)
    row.addView(theoIcon("paperclip", p.mutedForeground, 18f))
    val input = EditText(this)
    input.hint = hint
    input.setTextColor(p.foreground)
    input.setHintTextColor(p.mutedForeground)
    input.background = null
    input.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
    input.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    row.addView(input)
    val send = LinearLayout(this)
    send.gravity = Gravity.CENTER
    send.addView(theoIcon("send", p.primaryForeground, 16f))
    send.background = TheoUi.roundedBg(this, p.primary, TheoTokens.RADIUS_MD)
    send.setPadding(TheoUi.dp(this, 8f), TheoUi.dp(this, 8f), TheoUi.dp(this, 8f), TheoUi.dp(this, 8f))
    row.addView(send)
    if (onSend != null) send.setOnClickListener { onSend(input.text.toString()) }
    card.addView(row)
    return card
}

/** AgentComposer: composer + toolbar row (model, effort, approval mode). */
fun Context.theoAgentComposer(
    quickActions: List<String> = emptyList(),
    toolbarViews: List<View> = emptyList(),
    onSend: ((String) -> Unit)? = null,
): View {
    val col = theoColumn(gap = 2f)
    if (quickActions.isNotEmpty()) col.addView(theoQuickActionChips(quickActions))
    if (toolbarViews.isNotEmpty()) {
        val bar = theoRow(gap = 2f)
        toolbarViews.forEach { bar.addView(it) }
        col.addView(bar)
    }
    col.addView(theoChatComposer(onSend = onSend))
    return col
}

/** AgentEditor: multi-line editor with action bar. */
fun Context.theoAgentEditor(text: String = "", onRun: (() -> Unit)? = null): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    val input = EditText(this)
    input.setText(text)
    input.setTextColor(p.foreground)
    input.background = null
    input.minLines = 4
    input.gravity = Gravity.TOP or Gravity.START
    input.typeface = android.graphics.Typeface.MONOSPACE
    input.textSize = 13f
    card.addView(input)
    val bar = theoRow(gap = 2f, gravity = Gravity.END)
    bar.addView(theoButton("Run", TheoButtonVariant.PRIMARY, iconName = "play", small = true, onClick = onRun))
    card.addView(bar)
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoQuickActionChips(chips: List<String>): View {
    val row = theoRow(gap = 2f)
    chips.forEach { chip -> row.addView(theoBadge(chip, TheoTone.MUTED)) }
    return row
}

fun Context.theoAttachmentChip(name: String, size: String? = null): View {
    val p = theoPalette()
    val row = theoRow(gap = 1.5f)
    row.addView(theoIcon("file-text", p.primary, 12f))
    row.addView(theoText(name, TheoType.MICRO, p.foreground))
    if (size != null) row.addView(theoMono(size, p.mutedForeground, TheoType.CODE_SM))
    row.setPadding(TheoUi.dp(this, 8f), TheoUi.dp(this, 4f), TheoUi.dp(this, 8f), TheoUi.dp(this, 4f))
    row.background = TheoUi.pillBg(p.muted)
    return row
}

/** MentionMenu: filtered popup list. */
fun Context.theoMentionMenu(items: List<Pair<String, String>>): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 2f)
    items.forEach { (label, sub) ->
        val row = theoRow(gap = 2f)
        row.addView(theoIcon("user", p.mutedForeground, 14f))
        val mid = theoColumn(gap = 0.5f)
        mid.addView(theoText(label, TheoType.BODY_SM, p.foreground))
        mid.addView(theoText(sub, TheoType.MICRO, p.mutedForeground))
        row.addView(mid)
        card.addView(row)
    }
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoIntentSelector(intents: List<String>, selected: Int = 0): View {
    val row = theoRow(gap = 2f)
    intents.forEachIndexed { i, intent ->
        row.addView(theoBadge(intent, if (i == selected) TheoTone.PRIMARY else TheoTone.MUTED, filled = i == selected))
    }
    return row
}

fun Context.theoThinkingLevelSelector(levels: List<String>, selected: Int = 1): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    card.addView(theoText("Thinking", TheoType.LABEL, p.mutedForeground))
    val row = theoRow(gap = 1.5f)
    levels.forEachIndexed { i, level ->
        row.addView(theoBadge(level, if (i == selected) TheoTone.PRIMARY else TheoTone.MUTED, filled = i == selected))
    }
    card.addView(row)
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoApprovalModeSelector(modes: List<String>, selected: Int = 0): View {
    val p = theoPalette()
    val row = theoRow(gap = 1.5f)
    modes.forEachIndexed { i, mode ->
        row.addView(theoBadge(mode, if (i == selected) TheoTone.SUCCESS else TheoTone.MUTED, filled = i == selected, iconName = if (i == selected) "check" else null))
    }
    return row
}

fun Context.theoModelEffortPicker(efforts: List<String>, selected: Int = 1): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 3f)
    card.addView(theoText("Effort", TheoType.LABEL, p.mutedForeground))
    val row = theoRow(gap = 1.5f)
    efforts.forEachIndexed { i, e ->
        row.addView(theoBadge(e, if (i == selected) TheoTone.PRIMARY else TheoTone.MUTED, filled = i == selected))
    }
    card.addView(row)
    TheoRowGap.apply(card, this)
    return card
}

/** ChoicePrompt: single-select option list with pick callback. */
fun Context.theoChoicePrompt(
    question: String,
    options: List<String>,
    onPick: ((Int) -> Unit)? = null,
): View {
    val p = theoPalette()
    val card = theoCard(bg = TheoTokens.tint(p.primary, 0.04f), paddingUnits = 4f)
    card.addView(theoText(question, TheoType.TITLE_SM, p.foreground))
    val list = theoColumn(gap = 1.5f)
    options.forEachIndexed { i, opt ->
        val row = theoRow(gap = 2f)
        row.addView(theoIcon("circle-dot", p.primary, 14f))
        row.addView(theoText(opt, TheoType.BODY, p.foreground))
        row.setPadding(TheoUi.dp(this, 8f), TheoUi.dp(this, 8f), TheoUi.dp(this, 8f), TheoUi.dp(this, 8f))
        row.background = TheoUi.roundedBg(this, p.background, TheoTokens.RADIUS_MD, p.border)
        row.isClickable = true
        row.setOnClickListener { onPick?.invoke(i) }
        list.addView(row)
    }
    card.addView(list)
    TheoRowGap.apply(card, this)
    return card
}

/** ConfirmPrompt: yes/no confirm card. */
fun Context.theoConfirmPrompt(
    question: String,
    detail: String? = null,
    onConfirm: (() -> Unit)? = null,
    onCancel: (() -> Unit)? = null,
): View {
    val p = theoPalette()
    val card = theoCard(bg = TheoTokens.tint(p.warning, 0.05f), paddingUnits = 4f)
    card.addView(theoText(question, TheoType.TITLE_SM, p.foreground))
    if (detail != null) card.addView(theoText(detail, TheoType.BODY_SM, p.mutedForeground))
    val btns = theoRow(gap = 2f, gravity = Gravity.END)
    btns.addView(theoButton("Cancel", TheoButtonVariant.GHOST, small = true, onClick = onCancel))
    btns.addView(theoButton("Confirm", TheoButtonVariant.PRIMARY, small = true, onClick = onConfirm))
    card.addView(btns)
    TheoRowGap.apply(card, this)
    return card
}

/** TextPrompt: input + submit. */
fun Context.theoTextPrompt(question: String, placeholder: String = "", onSubmit: ((String) -> Unit)? = null): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    card.addView(theoText(question, TheoType.TITLE_SM, p.foreground))
    val input = EditText(this)
    input.hint = placeholder
    input.setTextColor(p.foreground)
    input.setHintTextColor(p.mutedForeground)
    input.background = TheoUi.roundedBg(this, p.background, TheoTokens.RADIUS_MD, p.input)
    input.setPadding(TheoUi.dp(this, 10f), TheoUi.dp(this, 10f), TheoUi.dp(this, 10f), TheoUi.dp(this, 10f))
    card.addView(input)
    card.addView(theoButton("Submit", TheoButtonVariant.PRIMARY, small = true, onClick = { onSubmit?.invoke(input.text.toString()) }))
    TheoRowGap.apply(card, this)
    return card
}

/** MultiSelectPrompt: checkbox list with Done button. */
fun Context.theoMultiSelectPrompt(
    question: String,
    options: List<String>,
    checked: Set<Int> = emptySet(),
    onDone: ((Set<Int>) -> Unit)? = null,
): View {
    val p = theoPalette()
    val card = theoCard(paddingUnits = 4f)
    card.addView(theoText(question, TheoType.TITLE_SM, p.foreground))
    val state = checked.toMutableSet()
    val list = theoColumn(gap = 1.5f)
    options.forEachIndexed { i, opt ->
        val row = theoRow(gap = 2f)
        val box = LinearLayout(this)
        box.gravity = Gravity.CENTER
        val mark = theoIcon("check", p.primaryForeground, 10f)
        mark.visibility = if (i in state) View.VISIBLE else View.GONE
        box.addView(mark)
        box.background = if (i in state) TheoUi.roundedBg(this, p.primary, TheoTokens.RADIUS_SM) else
            TheoUi.roundedBg(this, Color.TRANSPARENT, TheoTokens.RADIUS_SM, p.border)
        box.layoutParams = LinearLayout.LayoutParams(TheoUi.dp(this, 16f), TheoUi.dp(this, 16f))
        row.addView(box)
        row.addView(theoText(opt, TheoType.BODY, p.foreground))
        row.isClickable = true
        row.setOnClickListener {
            if (!state.add(i)) state.remove(i)
            mark.visibility = if (i in state) View.VISIBLE else View.GONE
            box.background = if (i in state) TheoUi.roundedBg(this, p.primary, TheoTokens.RADIUS_SM) else
                TheoUi.roundedBg(this, Color.TRANSPARENT, TheoTokens.RADIUS_SM, p.border)
        }
        list.addView(row)
    }
    card.addView(list)
    card.addView(theoButton("Done (${state.size})", TheoButtonVariant.PRIMARY, small = true, onClick = { onDone?.invoke(state) }))
    TheoRowGap.apply(card, this)
    return card
}

/** PermissionModal: dialog card requesting a scoped permission. */
fun Context.theoPermissionModal(
    title: String,
    detail: String,
    scopes: List<String>,
    onAllow: (() -> Unit)? = null,
    onDeny: (() -> Unit)? = null,
): View {
    val p = theoPalette()
    val card = theoCard(radiusDp = TheoTokens.RADIUS_XL, paddingUnits = 5f)
    val head = theoRow(gap = 2f)
    head.addView(theoIcon("shield", p.warning, 18f))
    head.addView(theoText(title, TheoType.TITLE_SM, p.foreground))
    card.addView(head)
    card.addView(theoText(detail, TheoType.BODY_SM, p.mutedForeground))
    val chips = theoRow(gap = 1.5f)
    scopes.forEach { s -> chips.addView(theoBadge(s, TheoTone.WARNING)) }
    card.addView(chips)
    val btns = theoRow(gap = 2f, gravity = Gravity.END)
    btns.addView(theoButton("Deny", TheoButtonVariant.GHOST, small = true, onClick = onDeny))
    btns.addView(theoButton("Allow", TheoButtonVariant.PRIMARY, small = true, onClick = onAllow))
    card.addView(btns)
    TheoRowGap.apply(card, this)
    return card
}

fun Context.theoAutoCompactNotice(contextPercent: Int, threshold: Int = 80): View {
    val p = theoPalette()
    val card = theoCard(bg = TheoTokens.tint(p.warning, 0.08f), paddingUnits = 3f)
    val row = theoRow(gap = 2f)
    row.addView(theoIcon("layers", p.warning, 14f))
    val mid = theoColumn(gap = 1f)
    mid.addView(theoText("Auto-compact at $threshold%", TheoType.BODY_SM, p.foreground))
    mid.addView(theoProgressBar(contextPercent / 100f, TheoTone.WARNING))
    row.addView(mid)
    card.addView(row)
    return card
}

fun Context.theoExportChatDialog(formats: List<String>, selected: Int = 0, onExport: ((String) -> Unit)? = null): View {
    val p = theoPalette()
    val card = theoCard(radiusDp = TheoTokens.RADIUS_XL, paddingUnits = 5f)
    card.addView(theoText("Export chat", TheoType.TITLE_SM, p.foreground))
    val list = theoColumn(gap = 1.5f)
    formats.forEachIndexed { i, fmt ->
        val row = theoRow(gap = 2f)
        row.addView(theoIcon(if (i == selected) "check-circle" else "circle-dashed", if (i == selected) p.primary else p.mutedForeground, 14f))
        row.addView(theoText(fmt, TheoType.BODY, p.foreground))
        row.setOnClickListener { onExport?.invoke(fmt) }
        list.addView(row)
    }
    card.addView(list)
    TheoRowGap.apply(card, this)
    return card
}
