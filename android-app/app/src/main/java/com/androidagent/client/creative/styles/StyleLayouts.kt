package com.androidagent.client.creative.styles

import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.CutCornerShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// pattern:hero
@Composable
internal fun StyleHero(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var saved by rememberSaveable { mutableStateOf(false) }
    StyleStage(s) {
        Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween) {
            StyleBadge(s, p.value)
            StyleGlyph(s, "mountain", Modifier.size(90.dp))
        }
        StyleHeading(s, p)
        StyleRule(s)
        StyleAction(s, if (saved) "已加入计划 ✓" else p.action, interactive, { saved = !saved })
    }
}
// pattern:bento
@Composable
internal fun StyleBento(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    StyleStage(s) {
        StyleHeading(s, p)
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            StylePanel(s, Modifier.weight(1.3f), true) {
                StyleGlyph(s, "sun", Modifier.size(50.dp))
                StyleText(s, p.value, 28, true)
                StyleText(s, p.items[0], 12)
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                StylePanel(s) { StyleText(s, p.items[1], 12, true) }
                StylePanel(s) { StyleText(s, p.items[2], 12, true) }
            }
        }
    }
}
// pattern:profile
@Composable
internal fun StyleProfile(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var following by rememberSaveable { mutableStateOf(false) }
    StyleStage(s) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            StyleGlyph(s, "avatar")
            Column { StyleText(s, p.headline, 22, true); StyleText(s, p.caption, 11) }
        }
        StylePanel(s) {
            p.items.forEach { StyleText(s, it, 13) }
        }
        StyleAction(s, if (following) "已关注 ✓" else p.action, interactive, { following = !following })
    }
}
// pattern:pricing
@Composable
internal fun StylePricing(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var annual by rememberSaveable { mutableStateOf(false) }
    StyleStage(s) {
        StyleBadge(s, p.caption, true)
        StyleText(s, p.headline, 22, true)
        Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween, Alignment.CenterVertically) {
            StyleText(s, if (annual) "¥199 / 年" else p.value, 26, true)
            Switch(checked = annual, onCheckedChange = { if (interactive) annual = it })
        }
        p.items.forEach { StyleText(s, "✓  $it", 13) }
        StyleText(s, if (annual) "已选择年付方案 · 演示价格" else "切换年付方案 · 演示价格", 11)
    }
}
// pattern:ticket
@Composable
internal fun StyleTicket(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    StyleStage(s) {
        StylePanel(s) {
            StyleBadge(s, p.caption, true)
            StyleText(s, p.headline, 26, true)
            StyleText(s, p.value, 16)
            Canvas(Modifier.fillMaxWidth().height(12.dp)) {
                val count = (size.width / 10.dp.toPx()).toInt()
                repeat(count) { drawLine(s.text.copy(alpha = .4f), Offset(it * 10.dp.toPx(), center.y), Offset(it * 10.dp.toPx() + 5.dp.toPx(), center.y), 1.dp.toPx()) }
            }
            Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween) {
                p.items.take(2).forEach { StyleText(s, it, 12, true) }
            }
            Canvas(Modifier.fillMaxWidth().height(36.dp).semantics { contentDescription = "装饰条码，不用于实际核验" }) {
                repeat(42) { index ->
                    val width = if (index % 3 == 0) 3f else 1.3f
                    drawRect(s.text, Offset(index * size.width / 42, 0f), Size(width.dp.toPx(), size.height))
                }
            }
        }
    }
}
// pattern:boarding
@Composable
internal fun StyleBoarding(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    StyleStage(s) {
        StyleHeading(s, p)
        StylePanel(s) {
            Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween, Alignment.CenterVertically) {
                StyleText(s, "SHA", 30, true); StyleText(s, "── ✈ ──", 16); StyleText(s, "HND", 30, true)
            }
            StyleRule(s)
            p.items.forEachIndexed { index, item ->
                Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween) {
                    StyleText(s, listOf("出发", "登机口", "座位")[index % 3], 12)
                    StyleText(s, item, 12, true)
                }
            }
            StyleBadge(s, p.value, true)
        }
    }
}
// pattern:music
@Composable
internal fun StyleMusic(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var playing by rememberSaveable { mutableStateOf(false) }
    StyleStage(s) {
        Row(horizontalArrangement = Arrangement.spacedBy(18.dp), verticalAlignment = Alignment.CenterVertically) {
            StyleGlyph(s, "disc", Modifier.size(96.dp))
            Column { StyleText(s, p.headline, 20, true); StyleText(s, p.caption, 11) }
        }
        StyleProgress(s, .42f)
        Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween) { StyleText(s, "01:24", 11); StyleText(s, p.value, 11) }
        StyleAction(s, if (playing) "Ⅱ  暂停演示" else "▶  播放演示", interactive, { playing = !playing })
        StyleText(s, "本地交互预览 · 无音频", 10)
    }
}
// pattern:podcast
@Composable
internal fun StylePodcast(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var episode by rememberSaveable { mutableIntStateOf(0) }
    StyleStage(s) {
        StyleBadge(s, p.value)
        StyleHeading(s, p)
        p.items.forEachIndexed { index, item ->
            Row(Modifier.fillMaxWidth().clip(s.shape).background(if (episode == index) s.primary.copy(alpha = .14f) else s.panel)
                .clickable(enabled = interactive) { episode = index }.padding(12.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                StyleText(s, if (episode == index) "▶" else "○", 20)
                StyleText(s, item, 13, episode == index)
            }
        }
    }
}
// pattern:equalizer
@Composable
internal fun StyleEqualizer(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var preset by rememberSaveable { mutableIntStateOf(0) }
    StyleStage(s) {
        StyleHeading(s, p)
        Row(Modifier.fillMaxWidth().height(95.dp), Arrangement.spacedBy(9.dp), Alignment.Bottom) {
            repeat(12) { index ->
                val level = ((index * 7 + preset * 3) % 11 + 2) / 13f
                Box(Modifier.weight(1f).fillMaxHeight(level).background(if (index % 3 == 0) s.extra else s.primary, s.shape))
            }
        }
        StyleAction(s, "切换预设 · ${p.items[preset]}", interactive, { preset = (preset + 1) % p.items.size })
    }
}
// pattern:calendar
@Composable
internal fun StyleCalendar(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var selected by rememberSaveable { mutableIntStateOf(9) }
    StyleStage(s) {
        StyleHeading(s, p)
        Row(Modifier.fillMaxWidth(), Arrangement.SpaceAround) {
            listOf("一", "二", "三", "四", "五", "六", "日").forEach { StyleText(s, it, 11) }
        }
        // September 2026 starts on Tuesday and has 30 days.
        repeat(5) { week ->
            Row(Modifier.fillMaxWidth(), Arrangement.spacedBy(3.dp)) {
                repeat(7) { day ->
                    val date = week * 7 + day
                    val validDate = date in 1..30
                    Text(if (validDate) date.toString() else "", Modifier.weight(1f).heightIn(min = 36.dp)
                        .clip(s.shape).background(if (date == selected) s.primary else s.panel)
                        .clickable(enabled = interactive && validDate) { selected = date }.padding(vertical = 10.dp),
                        color = if (date == selected) styleOnAccent(s) else s.text, textAlign = TextAlign.Center, fontSize = 12.sp)
                }
            }
        }
        StyleText(s, "已选择 9 月 $selected 日", 12, true)
    }
}
// pattern:timeline
@Composable
internal fun StyleTimeline(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    StyleStage(s) {
        StyleHeading(s, p)
        p.items.forEachIndexed { index, item ->
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Box(Modifier.size(12.dp).background(if (index == 0) s.primary else s.extra, CircleShape))
                    if (index < p.items.lastIndex) Box(Modifier.width(2.dp).height(33.dp).background(s.primary.copy(alpha = .25f)))
                }
                Column { StyleText(s, item, 14, index == 0); StyleText(s, "${9 + index}:00 · ${p.value}", 11) }
            }
        }
    }
}
// pattern:checklist
@Composable
internal fun StyleChecklist(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var checked by rememberSaveable { mutableIntStateOf(1) }
    StyleStage(s) {
        StyleHeading(s, p)
        StyleProgress(s, Integer.bitCount(checked).toFloat() / p.items.size)
        p.items.forEachIndexed { index, item ->
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = checked and (1 shl index) != 0,
                    onCheckedChange = { if (interactive) checked = checked xor (1 shl index) },
                    colors = CheckboxDefaults.colors(checkedColor = s.primary))
                StyleText(s, item, 14)
            }
        }
    }
}
// pattern:kanban
@Composable
internal fun StyleKanban(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var done by rememberSaveable { mutableStateOf(false) }
    StyleStage(s) {
        StyleHeading(s, p)
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                StyleBadge(s, "待办")
                if (!done) StylePanel(s) { StyleText(s, p.items[0], 12, true) }
                StylePanel(s) { StyleText(s, p.items[1], 12) }
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                StyleBadge(s, "完成", true)
                if (done) StylePanel(s, emphasized = true) { StyleText(s, p.items[0], 12) }
                StylePanel(s, emphasized = true) { StyleText(s, p.items[2], 12) }
            }
        }
        StyleAction(s, if (done) "撤回任务" else p.action, interactive, { done = !done })
    }
}
// pattern:stats
@Composable
internal fun StyleStats(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    StyleStage(s) {
        StyleHeading(s, p)
        StylePanel(s, emphasized = true) {
            StyleText(s, p.value, 36, true)
            StyleBadge(s, "↑ 12.8% · 示例数据", true)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            p.items.take(2).forEach { item -> StylePanel(s, Modifier.weight(1f)) { StyleText(s, item, 12, true) } }
        }
    }
}
// pattern:bars
@Composable
internal fun StyleBars(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var week by rememberSaveable { mutableIntStateOf(0) }
    StyleStage(s) {
        StyleHeading(s, p)
        Row(Modifier.fillMaxWidth().height(115.dp), Arrangement.spacedBy(10.dp), Alignment.Bottom) {
            repeat(7) { index ->
                Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Bottom) {
                    val v = ((index * 3 + week * 2) % 8 + 2) / 10f
                    Box(Modifier.fillMaxWidth().height((v * 80).dp).background(if (index == 4) s.extra else s.primary, s.shape))
                    StyleText(s, "${index + 1}", 10)
                }
            }
        }
        StyleAction(s, if (week == 0) "查看上一周" else "返回本周", interactive, { week = 1 - week })
    }
}
// pattern:sparkline
@Composable
internal fun StyleSparkline(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    StyleStage(s) {
        StyleHeading(s, p)
        StyleText(s, p.value, 34, true)
        Canvas(Modifier.fillMaxWidth().height(90.dp).semantics { contentDescription = "示例折线：先回落，再逐渐增长" }) {
            val values = listOf(.68f, .44f, .53f, .7f, .48f, .4f, .51f, .26f, .33f, .15f)
            val line = Path()
            values.forEachIndexed { i, y ->
                val x = i * size.width / (values.size - 1)
                if (i == 0) line.moveTo(x, y * size.height) else line.lineTo(x, y * size.height)
            }
            drawPath(line, s.primary, style = Stroke(3.dp.toPx(), cap = StrokeCap.Round))
            line.lineTo(size.width, size.height); line.lineTo(0f, size.height); line.close()
            drawPath(line, Brush.verticalGradient(listOf(s.primary.copy(alpha = .25f), Color.Transparent)))
        }
        Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween) { p.items.take(2).forEach { StyleText(s, it, 11) } }
    }
}
// pattern:ring
@Composable
internal fun StyleRing(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var progress by rememberSaveable { mutableFloatStateOf(.72f) }
    val animated by animateFloatAsState(progress, label = "ring")
    StyleStage(s) {
        StyleHeading(s, p)
        Box(Modifier.align(Alignment.CenterHorizontally).size(132.dp), contentAlignment = Alignment.Center) {
            Canvas(Modifier.fillMaxSize().padding(8.dp)) {
                drawArc(s.primary.copy(alpha = .15f), -90f, 360f, false, style = Stroke(12.dp.toPx()))
                drawArc(s.primary, -90f, animated * 360f, false, style = Stroke(12.dp.toPx(), cap = StrokeCap.Round))
            }
            StyleText(s, "${(animated * 100).toInt()}%", 27, true)
        }
        StyleAction(s, p.action, interactive, { progress = if (progress >= .99f) .2f else (progress + .14f).coerceAtMost(1f) })
    }
}
// pattern:heatmap
@Composable
internal fun StyleHeatmap(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    StyleStage(s) {
        StyleHeading(s, p)
        StyleText(s, p.value, 30, true)
        Column(verticalArrangement = Arrangement.spacedBy(5.dp), modifier = Modifier.semantics { contentDescription = "四行十列活跃热力图，深色代表更活跃" }) {
            repeat(4) { row ->
                Row(horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    repeat(10) { col ->
                        Box(Modifier.weight(1f).aspectRatio(1f).background(s.primary.copy(alpha = ((row * 7 + col * 3) % 5 + 1) / 5f), RoundedCornerShape(3.dp)))
                    }
                }
            }
        }
        StyleText(s, "少  ░ ▒ ▓ █  多 · 示例数据", 11)
    }
}
// pattern:gauge
@Composable
internal fun StyleGauge(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var level by rememberSaveable { mutableFloatStateOf(.65f) }
    StyleStage(s) {
        StyleHeading(s, p)
        Canvas(Modifier.fillMaxWidth().height(100.dp).semantics { contentDescription = "仪表读数 ${(level * 100).toInt()}" }) {
            val diameter = size.height * 1.7f
            val origin = Offset((size.width - diameter) / 2, 10f)
            drawArc(s.primary.copy(alpha = .14f), 180f, 180f, false, origin, Size(diameter, diameter), style = Stroke(16.dp.toPx(), cap = StrokeCap.Round))
            drawArc(s.primary, 180f, 180f * level, false, origin, Size(diameter, diameter), style = Stroke(16.dp.toPx(), cap = StrokeCap.Round))
        }
        StyleText(s, "${(level * 100).toInt()} / 100", 24, true, Modifier.align(Alignment.CenterHorizontally))
        Slider(value = level, onValueChange = { if (interactive) level = it }, colors = SliderDefaults.colors(thumbColor = s.primary, activeTrackColor = s.primary))
    }
}
// pattern:funnel
@Composable
internal fun StyleFunnel(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    StyleStage(s) {
        StyleHeading(s, p)
        p.items.forEachIndexed { index, item ->
            Box(Modifier.align(Alignment.CenterHorizontally).fillMaxWidth(1f - index * .2f)
                .background(s.primary.copy(alpha = 1f - index * .2f), s.shape).padding(15.dp), contentAlignment = Alignment.Center) {
                Text(item, color = styleOnAccent(s), fontFamily = s.font, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            }
        }
        StyleText(s, p.value, 11)
    }
}
// pattern:leaderboard
@Composable
internal fun StyleLeaderboard(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    StyleStage(s) {
        StyleHeading(s, p)
        Row(Modifier.fillMaxWidth().height(135.dp), Arrangement.spacedBy(9.dp), Alignment.Bottom) {
            listOf(1, 0, 2).forEach { rank ->
                Column(Modifier.weight(1f).background(s.primary.copy(alpha = if (rank == 0) .25f else .1f), s.shape)
                    .height((130 - rank * 26).dp).padding(10.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.SpaceBetween) {
                    StyleText(s, "#${rank + 1}", 22, true)
                    StyleText(s, p.items[rank], 11, true)
                }
            }
        }
        StyleBadge(s, p.value)
    }
}
// pattern:comparison
@Composable
internal fun StyleComparison(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var choice by rememberSaveable { mutableIntStateOf(1) }
    StyleStage(s) {
        StyleHeading(s, p)
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            p.items.take(2).forEachIndexed { index, item ->
                StylePanel(s, Modifier.weight(1f).clickable(enabled = interactive) { choice = index }, choice == index) {
                    StyleBadge(s, if (choice == index) "✓ 已选" else "可选", choice == index)
                    StyleText(s, item, 17, true)
                    StyleText(s, if (index == 0) "轻量起步\n基础功能" else "完整体验\n进阶功能", 12)
                }
            }
        }
        StyleText(s, "当前：${p.items[choice]}", 12, true)
    }
}
// pattern:chat
@Composable
internal fun StyleChat(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var sent by rememberSaveable { mutableStateOf(false) }
    StyleStage(s) {
        StyleHeading(s, p)
        p.items.take(2).forEachIndexed { index, item ->
            StylePanel(s, Modifier.fillMaxWidth(.85f).align(if (index == 0) Alignment.Start else Alignment.End), index == 1) { StyleText(s, item, 13) }
        }
        if (sent) StyleBadge(s, "收到，我们开始吧 ✓", true, Modifier.align(Alignment.End))
        StyleAction(s, if (sent) "重置对话" else p.action, interactive, { sent = !sent })
    }
}
// pattern:notification
@Composable
internal fun StyleNotification(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var read by rememberSaveable { mutableStateOf(false) }
    StyleStage(s) {
        StyleHeading(s, p)
        p.items.forEachIndexed { index, item ->
            StylePanel(s, emphasized = !read && index == 0) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Box(Modifier.size(8.dp).background(if (read) s.text.copy(alpha = .2f) else s.primary, CircleShape))
                    StyleText(s, item, 13, !read)
                }
            }
        }
        StyleAction(s, if (read) "全部已读 ✓" else p.action, interactive, { read = !read })
    }
}
// pattern:empty
@Composable
internal fun StyleEmpty(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var created by rememberSaveable { mutableStateOf(false) }
    StyleStage(s) {
        StyleGlyph(s, "shapes", Modifier.align(Alignment.CenterHorizontally).size(94.dp))
        StyleText(s, if (created) "第一条记录已创建" else p.headline, 24, true)
        StyleText(s, if (created) p.items[0] else p.caption, 13)
        StyleAction(s, if (created) "重新体验" else p.action, interactive, { created = !created })
    }
}
// pattern:search
@Composable
internal fun StyleSearch(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var query by rememberSaveable { mutableStateOf("") }
    StyleStage(s) {
        StyleHeading(s, p)
        OutlinedTextField(value = query, onValueChange = { if (interactive) query = it },
            modifier = Modifier.fillMaxWidth(), readOnly = !interactive, singleLine = true,
            placeholder = { Text("搜索示例项目") }, shape = s.shape,
            colors = OutlinedTextFieldDefaults.colors(focusedTextColor = s.text, unfocusedTextColor = s.text,
                focusedBorderColor = s.primary, unfocusedBorderColor = s.text.copy(alpha = .3f)))
        val matches = p.items.filter { it.contains(query, ignoreCase = true) }
        matches.forEach { StylePanel(s) { StyleText(s, it, 13) } }
        if (matches.isEmpty()) StyleText(s, "没有匹配结果", 13)
    }
}
// pattern:command
@Composable
internal fun StyleCommand(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var selected by rememberSaveable { mutableStateOf("") }
    StyleStage(s) {
        StyleHeading(s, p)
        StylePanel(s) {
            StyleText(s, ">  选择一个快捷操作", 14, true)
            StyleRule(s)
            p.items.forEachIndexed { index, item ->
                Row(Modifier.fillMaxWidth().clickable(enabled = interactive) { selected = item }.padding(vertical = 10.dp), Arrangement.SpaceBetween) {
                    StyleText(s, item, 13); StyleBadge(s, "${index + 1}")
                }
            }
        }
        if (selected.isNotEmpty()) StyleText(s, "已选择：$selected", 12)
    }
}
// pattern:settings
@Composable
internal fun StyleSettings(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var flags by rememberSaveable { mutableIntStateOf(1) }
    StyleStage(s) {
        StyleHeading(s, p)
        p.items.forEachIndexed { index, item ->
            Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween, Alignment.CenterVertically) {
                StyleText(s, item, 14, modifier = Modifier.weight(1f))
                Switch(checked = flags and (1 shl index) != 0,
                    onCheckedChange = { if (interactive) flags = flags xor (1 shl index) },
                    colors = SwitchDefaults.colors(checkedTrackColor = s.primary, checkedThumbColor = styleOnAccent(s)))
            }
            StyleRule(s)
        }
    }
}
// pattern:slider
@Composable
internal fun StyleSlider(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var value by rememberSaveable { mutableFloatStateOf(.6f) }
    StyleStage(s) {
        StyleHeading(s, p)
        StylePanel(s, emphasized = true) {
            StyleGlyph(s, "sun", Modifier.align(Alignment.CenterHorizontally))
            StyleText(s, "${(value * 100).toInt()}%", 34, true, Modifier.align(Alignment.CenterHorizontally))
            Slider(value = value, onValueChange = { if (interactive) value = it },
                colors = SliderDefaults.colors(thumbColor = s.primary, activeTrackColor = s.primary))
            Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween) { p.items.take(2).forEach { StyleText(s, it, 11) } }
        }
    }
}
// pattern:stepper
@Composable
internal fun StyleStepper(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var step by rememberSaveable { mutableIntStateOf(0) }
    StyleStage(s) {
        StyleHeading(s, p)
        Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween) {
            p.items.forEachIndexed { index, _ ->
                StyleBadge(s, if (index < step) "✓" else "${index + 1}", index <= step)
            }
        }
        StyleProgress(s, (step + 1f) / p.items.size)
        StylePanel(s) {
            StyleText(s, p.items[step], 20, true)
            StyleText(s, "第 ${step + 1} 步，共 ${p.items.size} 步", 12)
        }
        StyleAction(s, if (step == p.items.lastIndex) "重新开始" else p.action, interactive, { step = (step + 1) % p.items.size })
    }
}
// pattern:tabs
@Composable
internal fun StyleTabs(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    StyleStage(s) {
        StyleHeading(s, p)
        Row(Modifier.fillMaxWidth().background(s.panel, s.shape).padding(4.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            p.items.forEachIndexed { index, item ->
                Box(Modifier.weight(1f).heightIn(min = 48.dp).clip(s.shape)
                    .background(if (tab == index) s.primary else Color.Transparent)
                    .clickable(enabled = interactive) { tab = index }.padding(8.dp), contentAlignment = Alignment.Center) {
                    Text(item, color = if (tab == index) styleOnAccent(s) else s.text, fontFamily = s.font, fontSize = 12.sp)
                }
            }
        }
        StylePanel(s, emphasized = true) {
            StyleText(s, p.items[tab], 26, true)
            StyleText(s, "${tab + 1} / ${p.items.size} · 分段内容预览", 12)
            StyleProgress(s, (tab + 1f) / p.items.size)
        }
    }
}
// pattern:dock
@Composable
internal fun StyleDock(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var selected by rememberSaveable { mutableIntStateOf(0) }
    StyleStage(s) {
        StyleHeading(s, p)
        StylePanel(s, emphasized = true) {
            StyleGlyph(s, "shapes", Modifier.align(Alignment.CenterHorizontally))
            StyleText(s, p.items[selected], 24, true, Modifier.align(Alignment.CenterHorizontally))
        }
        StylePanel(s) {
            Row(Modifier.fillMaxWidth(), Arrangement.SpaceAround) {
                p.items.forEachIndexed { index, item ->
                    Column(Modifier.clip(s.shape).clickable(enabled = interactive) { selected = index }.padding(10.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                        StyleText(s, listOf("⌂", "◇", "○")[index % 3], 24, selected == index)
                        StyleText(s, item, 11, selected == index)
                        if (selected == index) Box(Modifier.padding(top = 3.dp).size(5.dp).background(s.primary, CircleShape))
                    }
                }
            }
        }
    }
}
// pattern:login
@Composable
internal fun StyleLogin(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var email by rememberSaveable { mutableStateOf("") }
    var submitted by rememberSaveable { mutableStateOf(false) }
    StyleStage(s) {
        StyleHeading(s, p)
        OutlinedTextField(value = email, onValueChange = { if (interactive) { email = it; submitted = false } },
            modifier = Modifier.fillMaxWidth(), readOnly = !interactive, singleLine = true,
            label = { Text("邮箱地址") }, shape = s.shape,
            colors = OutlinedTextFieldDefaults.colors(focusedTextColor = s.text, unfocusedTextColor = s.text,
                focusedBorderColor = s.primary, unfocusedBorderColor = s.text.copy(alpha = .3f)))
        StyleAction(s, p.action, interactive, { submitted = true })
        StyleText(s, if (submitted) {
            if (email.matches(Regex("""[^\s@]+@[^\s@]+\.[^\s@]+"""))) "验证界面演示 · 未发送邮件" else "请填写有效邮箱"
        } else "输入示例邮箱，体验表单反馈", 12)
        StyleRule(s)
        StyleText(s, p.value, 11)
    }
}
// pattern:otp
@Composable
internal fun StyleOtp(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var code by rememberSaveable { mutableStateOf("") }
    StyleStage(s) {
        StyleHeading(s, p)
        Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
            repeat(4) { index ->
                Box(Modifier.weight(1f).height(58.dp).background(s.panel, s.shape).border(1.dp, s.primary, s.shape), contentAlignment = Alignment.Center) {
                    StyleText(s, code.getOrNull(index)?.toString() ?: "—", 24, true)
                }
            }
        }
        OutlinedTextField(value = code, onValueChange = { if (interactive) code = it.filter(Char::isDigit).take(4) },
            modifier = Modifier.fillMaxWidth(), readOnly = !interactive, singleLine = true,
            label = { Text("输入四位演示验证码") }, shape = s.shape,
            colors = OutlinedTextFieldDefaults.colors(focusedTextColor = s.text, unfocusedTextColor = s.text))
        StyleText(s, if (code.length == 4) "输入完成 ✓ · 仅演示" else "已输入 ${code.length} / 4 位", 12)
    }
}
// pattern:form
@Composable
internal fun StyleForm(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var selected by rememberSaveable { mutableIntStateOf(0) }
    var confirmed by rememberSaveable { mutableStateOf(false) }
    StyleStage(s) {
        StyleHeading(s, p)
        p.items.forEachIndexed { index, item ->
            Row(Modifier.fillMaxWidth().clickable(enabled = interactive) { selected = index; confirmed = false }, verticalAlignment = Alignment.CenterVertically) {
                RadioButton(selected = selected == index, onClick = { if (interactive) { selected = index; confirmed = false } },
                    colors = RadioButtonDefaults.colors(selectedColor = s.primary))
                StyleText(s, item, 13)
            }
        }
        StyleAction(s, if (confirmed) "已保存：${p.items[selected]}" else p.action, interactive, { confirmed = true })
    }
}
// pattern:product
@Composable
internal fun StyleProduct(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var added by rememberSaveable { mutableStateOf(false) }
    StyleStage(s) {
        StylePanel(s, emphasized = true) {
            StyleGlyph(s, "shapes", Modifier.align(Alignment.CenterHorizontally).size(94.dp))
        }
        Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween, Alignment.CenterVertically) {
            StyleText(s, p.headline, 19, true, Modifier.weight(1f)); StyleBadge(s, p.value, true)
        }
        StyleText(s, p.caption, 12)
        StyleAction(s, if (added) "已加入购物袋 ✓" else p.action, interactive, { added = !added })
    }
}
// pattern:cart
@Composable
internal fun StyleCart(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var count by rememberSaveable { mutableIntStateOf(1) }
    StyleStage(s) {
        StyleHeading(s, p)
        StylePanel(s) {
            Row(horizontalArrangement = Arrangement.spacedBy(15.dp), verticalAlignment = Alignment.CenterVertically) {
                StyleGlyph(s, "shapes", Modifier.size(60.dp))
                Column { StyleText(s, p.items[0], 15, true); StyleText(s, p.value, 12) }
            }
            Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween, Alignment.CenterVertically) {
                OutlinedButton(onClick = { if (interactive) count = (count - 1).coerceAtLeast(1) }, enabled = count > 1) { Text("−") }
                StyleText(s, count.toString(), 22, true)
                OutlinedButton(onClick = { if (interactive) count = (count + 1).coerceAtMost(9) }, enabled = count < 9) { Text("+") }
            }
        }
        StyleText(s, "合计 ¥${count * 128} · 演示价格", 20, true)
    }
}
// pattern:receipt
@Composable
internal fun StyleReceipt(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    StyleStage(s) {
        StylePanel(s) {
            StyleText(s, p.headline, 22, true, Modifier.align(Alignment.CenterHorizontally))
            StyleText(s, p.caption, 11, modifier = Modifier.align(Alignment.CenterHorizontally))
            StyleRule(s)
            p.items.forEachIndexed { index, item ->
                Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween) { StyleText(s, item, 13); StyleText(s, "¥${listOf(68, 32, 28)[index % 3]}", 13) }
            }
            StyleRule(s)
            Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween) { StyleText(s, "合计", 15, true); StyleText(s, p.value, 22, true) }
            StyleBadge(s, "支付成功 · 示例", true, Modifier.align(Alignment.CenterHorizontally))
        }
    }
}
// pattern:weather
@Composable
internal fun StyleWeather(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    StyleStage(s) {
        StyleHeading(s, p)
        Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween, Alignment.CenterVertically) {
            StyleText(s, p.value, 56, true); StyleGlyph(s, "sun", Modifier.size(96.dp))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            p.items.forEach { item -> StylePanel(s, Modifier.weight(1f)) { StyleText(s, item, 11, true) } }
        }
        StyleText(s, "天气数据为静态示例", 10)
    }
}
// pattern:map
@Composable
internal fun StyleMap(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var route by rememberSaveable { mutableStateOf(false) }
    StyleStage(s) {
        StyleHeading(s, p)
        Canvas(Modifier.fillMaxWidth().height(130.dp).clip(s.shape).background(s.panel).semantics { contentDescription = "示意地图：起点到公园，不是真实导航" }) {
            repeat(6) { index ->
                drawLine(s.text.copy(alpha = .1f), Offset(index * size.width / 5, 0f), Offset(index * size.width / 5 + 30.dp.toPx(), size.height), 9.dp.toPx())
                drawLine(s.text.copy(alpha = .1f), Offset(0f, index * size.height / 4), Offset(size.width, index * size.height / 4), 7.dp.toPx())
            }
            val path = Path().apply { moveTo(size.width * .15f, size.height * .8f); lineTo(size.width * .42f, size.height * .65f); lineTo(size.width * .55f, size.height * .3f); lineTo(size.width * .85f, size.height * .2f) }
            if (route) drawPath(path, s.primary, style = Stroke(4.dp.toPx(), cap = StrokeCap.Round))
            drawCircle(s.primary, 7.dp.toPx(), Offset(size.width * .15f, size.height * .8f))
            drawCircle(s.extra, 9.dp.toPx(), Offset(size.width * .85f, size.height * .2f))
        }
        StyleAction(s, if (route) "已显示示例路线" else p.action, interactive, { route = !route })
    }
}
// pattern:timer
@Composable
internal fun StyleTimer(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var running by rememberSaveable { mutableStateOf(false) }
    var remaining by rememberSaveable { mutableIntStateOf(25 * 60) }
    LaunchedEffect(running) {
        while (running && remaining > 0) { kotlinx.coroutines.delay(1000); remaining-- }
        if (remaining == 0) running = false
    }
    StyleStage(s) {
        StyleHeading(s, p)
        StylePanel(s, emphasized = true) {
            StyleText(s, "%02d:%02d".format(remaining / 60, remaining % 60), 48, true, Modifier.align(Alignment.CenterHorizontally))
            StyleProgress(s, remaining / 1500f)
            StyleText(s, if (running) "专注进行中" else "准备好，慢慢来", 12, modifier = Modifier.align(Alignment.CenterHorizontally))
        }
        StyleAction(s, if (running) "暂停计时" else p.action, interactive, { if (remaining == 0) remaining = 1500; running = !running })
    }
}
// pattern:swatches
@Composable
internal fun StyleSwatches(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var selected by rememberSaveable { mutableIntStateOf(0) }
    val colors = listOf(s.primary, s.extra, s.text, s.panel)
    StyleStage(s) {
        StyleHeading(s, p)
        Box(Modifier.fillMaxWidth().height(95.dp).background(colors[selected], s.shape))
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            colors.forEachIndexed { index, color ->
                Box(Modifier.weight(1f).height(50.dp).clip(s.shape).background(color)
                    .border(if (selected == index) 3.dp else 1.dp, s.text.copy(alpha = .5f), s.shape)
                    .clickable(enabled = interactive) { selected = index }.semantics { contentDescription = "颜色 ${index + 1}" }, contentAlignment = Alignment.Center) {
                    if (selected == index) Text("✓", color = if (index == 2) s.bg else s.text, fontSize = 20.sp)
                }
            }
        }
        StyleText(s, "当前色板 · ${selected + 1} / ${colors.size}", 13, true)
    }
}
// pattern:rating
@Composable
internal fun StyleRating(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var stars by rememberSaveable { mutableIntStateOf(4) }
    StyleStage(s) {
        StyleHeading(s, p)
        StyleText(s, "$stars.0", 50, true, Modifier.align(Alignment.CenterHorizontally))
        Row(Modifier.fillMaxWidth(), Arrangement.SpaceEvenly) {
            repeat(5) { index ->
                Text(if (index < stars) "★" else "☆", Modifier.clickable(enabled = interactive) { stars = index + 1 }.padding(5.dp)
                    .semantics { contentDescription = "评分 ${index + 1} 星" }, color = s.primary, fontSize = 31.sp)
            }
        }
        StyleText(s, "${p.items[(stars - 1) % p.items.size]} · 点击星星评分", 12, modifier = Modifier.align(Alignment.CenterHorizontally))
    }
}
// pattern:poll
@Composable
internal fun StylePoll(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var voted by rememberSaveable { mutableIntStateOf(-1) }
    StyleStage(s) {
        StyleHeading(s, p)
        p.items.forEachIndexed { index, item ->
            StylePanel(s, Modifier.fillMaxWidth().clickable(enabled = interactive) { voted = index }, voted == index) {
                Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween) {
                    StyleText(s, item, 13, true)
                    if (voted >= 0) StyleText(s, "${listOf(48, 32, 20)[index % 3]}%", 12)
                }
                if (voted >= 0) StyleProgress(s, listOf(.48f, .32f, .2f)[index % 3])
            }
        }
        StyleText(s, if (voted < 0) "点击选项查看示例结果" else "已选：${p.items[voted]} · 本地演示", 11)
    }
}
// pattern:accordion
@Composable
internal fun StyleAccordion(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var expanded by rememberSaveable { mutableIntStateOf(0) }
    StyleStage(s) {
        StyleHeading(s, p)
        p.items.forEachIndexed { index, item ->
            StylePanel(s, Modifier.fillMaxWidth().animateContentSize().clickable(enabled = interactive) { expanded = if (expanded == index) -1 else index }) {
                Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween) {
                    StyleText(s, item, 13, true, Modifier.weight(1f)); StyleText(s, if (expanded == index) "−" else "+", 18)
                }
                if (expanded == index) StyleText(s, "轻触标题可收起或展开。这里展示第 ${index + 1} 项的详细说明。", 12)
            }
        }
    }
}
// pattern:onboarding
@Composable
internal fun StyleOnboarding(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var page by rememberSaveable { mutableIntStateOf(0) }
    StyleStage(s) {
        StyleGlyph(s, listOf("mountain", "sun", "shapes")[page % 3], Modifier.align(Alignment.CenterHorizontally).size(90.dp))
        StyleText(s, p.items[page], 25, true)
        StyleText(s, p.caption, 13)
        Row(Modifier.align(Alignment.CenterHorizontally), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            p.items.indices.forEach { i -> Box(Modifier.width(if (i == page) 23.dp else 7.dp).height(7.dp).background(s.primary.copy(alpha = if (i == page) 1f else .2f), CircleShape)) }
        }
        StyleAction(s, if (page == p.items.lastIndex) "重新体验" else p.action, interactive, { page = (page + 1) % p.items.size })
    }
}
// pattern:story
@Composable
internal fun StyleStory(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var page by rememberSaveable { mutableIntStateOf(0) }
    StyleStage(s) {
        Row(horizontalArrangement = Arrangement.spacedBy(5.dp)) {
            p.items.indices.forEach { i -> Box(Modifier.weight(1f).height(4.dp).background(s.primary.copy(alpha = if (i <= page) 1f else .2f), CircleShape)) }
        }
        StylePanel(s, emphasized = true) {
            StyleGlyph(s, "mountain", Modifier.align(Alignment.CenterHorizontally).size(100.dp))
            StyleText(s, p.items[page], 24, true)
            StyleText(s, p.caption, 11)
        }
        StyleAction(s, "${p.action} · ${page + 1}/${p.items.size}", interactive, { page = (page + 1) % p.items.size })
    }
}
// pattern:grid
@Composable
internal fun StyleGrid(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var selected by rememberSaveable { mutableIntStateOf(-1) }
    StyleStage(s) {
        StyleHeading(s, p)
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            repeat(2) { column ->
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    repeat(2) { row ->
                        val index = column * 2 + row
                        StylePanel(s, Modifier.fillMaxWidth().clickable(enabled = interactive) { selected = index }, selected == index) {
                            StyleGlyph(s, if (index % 2 == 0) "mountain" else "shapes", Modifier.size(if (column == row) 60.dp else 35.dp))
                            StyleText(s, if (selected == index) "已收藏 ✓" else p.items[index % p.items.size], 12, true)
                        }
                    }
                }
            }
        }
    }
}
