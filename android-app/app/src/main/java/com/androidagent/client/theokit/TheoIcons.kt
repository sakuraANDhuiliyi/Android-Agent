package com.androidagent.client.theokit

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PathMeasure
import android.view.View

/**
 * TheoKit icon set — stroke icons drawn on a 24x24 grid, mirroring the inline
 * SVG icon registry of the desktop theokit library. Unknown names fall back
 * to `circle-dashed` (same policy as the desktop build).
 */
class TheoIconView(context: Context, var iconName: String, var color: Int, sizeDp: Float) : View(context) {

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    private val path = Path()
    private val pathMeasure = PathMeasure()
    private var sizePx = TheoUi.dp(context, sizeDp)

    init {
        layoutParams = android.view.ViewGroup.LayoutParams(sizePx, sizePx)
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        setMeasuredDimension(sizePx, sizePx)
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val scale = sizePx / 24f
        paint.color = color
        paint.strokeWidth = 2f * scale
        path.reset()
        TheoIcons.draw(path, iconName)
        if (path.isEmpty) TheoIcons.draw(path, "circle-dashed")
        canvas.save()
        canvas.scale(scale, scale)
        canvas.drawPath(path, paint)
        canvas.restore()
    }

    fun dashed(): Boolean = TheoIcons.DASHED.contains(iconName)
}

object TheoIcons {

    val DASHED = setOf("circle-dashed", "loader")

    fun draw(p: Path, name: String) {
        when (name) {
            "check" -> check(p)
            "x" -> x(p)
            "plus" -> plus(p)
            "minus" -> minus(p)
            "chevron-down" -> chevron(p, 0f)
            "chevron-up" -> chevron(p, 180f)
            "chevron-left" -> chevron(p, 90f)
            "chevron-right" -> chevron(p, -90f)
            "arrow-right" -> arrowRight(p)
            "arrow-up-right" -> arrowUpRight(p)
            "circle-dot" -> circleDot(p)
            "circle-dashed" -> circleDashed(p)
            "loader" -> loader(p)
            "check-circle" -> checkCircle(p)
            "x-circle" -> xCircle(p)
            "alert-triangle" -> alertTriangle(p)
            "info-circle" -> infoCircle(p)
            "help-circle" -> helpCircle(p)
            "terminal" -> terminal(p)
            "search" -> search(p)
            "file-text" -> fileText(p)
            "file-plus" -> filePlus(p)
            "file-search" -> fileSearch(p)
            "file-edit" -> fileEdit(p)
            "folder" -> folder(p)
            "folder-open" -> folderOpen(p)
            "edit-3" -> edit3(p)
            "hammer" -> hammer(p)
            "wrench" -> wrench(p)
            "shield-check" -> shieldCheck(p)
            "zap" -> zap(p)
            "globe" -> globe(p)
            "server" -> server(p)
            "cpu" -> cpu(p)
            "database" -> database(p)
            "clock" -> clock(p)
            "calendar" -> calendar(p)
            "hash" -> hash(p)
            "coins" -> coins(p)
            "star" -> star(p)
            "flag" -> flag(p)
            "target" -> target(p)
            "sparkles" -> sparkles(p)
            "brain" -> brain(p)
            "bot" -> bot(p)
            "user" -> user(p)
            "message-square" -> messageSquare(p)
            "send" -> send(p)
            "paperclip" -> paperclip(p)
            "stop" -> stop(p)
            "copy" -> copy(p)
            "refresh" -> refresh(p)
            "trash" -> trash(p)
            "download" -> download(p)
            "upload" -> upload(p)
            "play" -> play(p)
            "pause" -> pause(p)
            "skip-forward" -> skipForward(p)
            "external-link" -> externalLink(p)
            "more-horizontal" -> moreHorizontal(p)
            "git-branch" -> gitBranch(p)
            "lock" -> lock(p)
            "key" -> key(p)
            "settings" -> settings(p)
            "book-open" -> bookOpen(p)
            "list-checks" -> listChecks(p)
            "scroll-text" -> scrollText(p)
            "layers" -> layers(p)
            "activity" -> activity(p)
            "eye" -> eye(p)
            "code" -> code(p)
            "box" -> box(p)
            "sliders" -> sliders(p)
            "link" -> link(p)
            "bell" -> bell(p)
            "trending-up" -> trendingUp(p)
            "bar-chart" -> barChart(p)
            "image" -> image(p)
            "film" -> film(p)
            "corner-down-right" -> cornerDownRight(p)
            "rotate-cw" -> rotateCw(p)
            "shield" -> shield(p)
            "wifi" -> wifi(p)
            else -> circleDashed(p)
        }
    }

    private fun move(p: Path, x: Float, y: Float) = p.moveTo(x, y)
    private fun line(p: Path, x: Float, y: Float) = p.lineTo(x, y)

    private fun check(p: Path) { move(p, 5f, 13f); line(p, 10f, 18f); line(p, 19f, 7f) }
    private fun x(p: Path) { move(p, 6f, 6f); line(p, 18f, 18f); move(p, 18f, 6f); line(p, 6f, 18f) }
    private fun plus(p: Path) { move(p, 12f, 5f); line(p, 12f, 19f); move(p, 5f, 12f); line(p, 19f, 12f) }
    private fun minus(p: Path) { move(p, 5f, 12f); line(p, 19f, 12f) }

    private fun chevron(p: Path, rotateDeg: Float) {
        val pts = floatArrayOf(7f, 10f, 12f, 15f, 17f, 10f)
        val rad = Math.toRadians(rotateDeg.toDouble())
        val cos = Math.cos(rad).toFloat()
        val sin = Math.sin(rad).toFloat()
        for (i in 0 until 3) {
            val px = pts[i * 2] - 12f
            val py = pts[i * 2 + 1] - 12f
            val tx = px * cos - py * sin + 12f
            val ty = px * sin + py * cos + 12f
            if (i == 0) move(p, tx, ty) else line(p, tx, ty)
        }
    }

    private fun arrowRight(p: Path) { move(p, 4f, 12f); line(p, 20f, 12f); move(p, 14f, 6f); line(p, 20f, 12f); line(p, 14f, 18f) }
    private fun arrowUpRight(p: Path) { move(p, 7f, 17f); line(p, 17f, 7f); move(p, 9f, 7f); line(p, 17f, 7f); line(p, 17f, 15f) }

    private fun circleDot(p: Path) { p.addCircle(12f, 12f, 9f, Path.Direction.CW); p.addCircle(12f, 12f, 2.5f, Path.Direction.CW) }
    private fun circleDashed(p: Path) { p.addCircle(12f, 12f, 9f, Path.Direction.CW) }
    private fun loader(p: Path) { p.addArc(android.graphics.RectF(4f, 4f, 20f, 20f), 0f, 270f) }
    private fun checkCircle(p: Path) { p.addCircle(12f, 12f, 9f, Path.Direction.CW); move(p, 8f, 12.5f); line(p, 11f, 15.5f); line(p, 16f, 9f) }
    private fun xCircle(p: Path) { p.addCircle(12f, 12f, 9f, Path.Direction.CW); move(p, 9f, 9f); line(p, 15f, 15f); move(p, 15f, 9f); line(p, 9f, 15f) }
    private fun alertTriangle(p: Path) { move(p, 12f, 3f); line(p, 21f, 19f); line(p, 3f, 19f); p.close(); move(p, 12f, 9f); line(p, 12f, 13.5f); move(p, 12f, 16.5f); line(p, 12f, 16.6f) }
    private fun infoCircle(p: Path) { p.addCircle(12f, 12f, 9f, Path.Direction.CW); move(p, 12f, 11f); line(p, 12f, 16f); move(p, 12f, 8f); line(p, 12f, 8.1f) }
    private fun helpCircle(p: Path) { p.addCircle(12f, 12f, 9f, Path.Direction.CW); move(p, 9.5f, 9.5f); p.cubicTo(9.5f, 7.5f, 14.5f, 7.5f, 14.5f, 9.5f); p.cubicTo(14.5f, 11.5f, 12f, 11.5f, 12f, 13.5f); move(p, 12f, 16.5f); line(p, 12f, 16.6f) }

    private fun terminal(p: Path) { p.addRoundRect(android.graphics.RectF(3f, 4f, 21f, 20f), 2f, 2f, Path.Direction.CW); move(p, 7f, 9f); line(p, 10f, 12f); line(p, 7f, 15f); move(p, 12.5f, 15f); line(p, 16.5f, 15f) }
    private fun search(p: Path) { p.addCircle(11f, 11f, 7f, Path.Direction.CW); move(p, 16.5f, 16.5f); line(p, 21f, 21f) }

    private fun fileText(p: Path) {
        move(p, 6f, 3f); line(p, 14f, 3f); line(p, 18f, 7f); line(p, 18f, 21f); line(p, 6f, 21f); p.close()
        move(p, 14f, 3f); line(p, 14f, 7f); line(p, 18f, 7f)
        move(p, 9f, 12f); line(p, 15f, 12f); move(p, 9f, 16f); line(p, 15f, 16f)
    }

    private fun filePlus(p: Path) {
        move(p, 6f, 3f); line(p, 14f, 3f); line(p, 18f, 7f); line(p, 18f, 21f); line(p, 6f, 21f); p.close()
        move(p, 14f, 3f); line(p, 14f, 7f); line(p, 18f, 7f)
        move(p, 12f, 11f); line(p, 12f, 17f); move(p, 9f, 14f); line(p, 15f, 14f)
    }

    private fun fileSearch(p: Path) {
        move(p, 6f, 3f); line(p, 14f, 3f); line(p, 18f, 7f); line(p, 18f, 21f); line(p, 6f, 21f); p.close()
        move(p, 14f, 3f); line(p, 14f, 7f); line(p, 18f, 7f)
        p.addCircle(11f, 14f, 3f, Path.Direction.CW); move(p, 13.2f, 16.2f); line(p, 15.5f, 18.5f)
    }

    private fun fileEdit(p: Path) {
        move(p, 6f, 3f); line(p, 14f, 3f); line(p, 18f, 7f); line(p, 18f, 21f); line(p, 6f, 21f); p.close()
        move(p, 14f, 3f); line(p, 14f, 7f); line(p, 18f, 7f)
        move(p, 12.5f, 14.5f); line(p, 16.5f, 10.5f); line(p, 18f, 12f); line(p, 14f, 16f); line(p, 12.3f, 16.7f); p.close()
    }

    private fun folder(p: Path) { move(p, 3f, 6f); line(p, 9f, 6f); line(p, 11f, 8.5f); line(p, 21f, 8.5f); line(p, 21f, 19f); line(p, 3f, 19f); p.close() }
    private fun folderOpen(p: Path) { move(p, 3f, 6f); line(p, 9f, 6f); line(p, 11f, 8.5f); line(p, 20f, 8.5f); line(p, 20f, 11f); move(p, 3f, 19f); line(p, 5.5f, 11f); line(p, 22f, 11f); line(p, 19.5f, 19f); p.close() }

    private fun edit3(p: Path) { move(p, 13f, 5f); line(p, 19f, 11f); line(p, 9f, 21f); line(p, 3.5f, 20.5f); line(p, 3f, 15f); p.close(); move(p, 15f, 7f); line(p, 17f, 9f) }
    private fun hammer(p: Path) { move(p, 14f, 4f); line(p, 20f, 10f); line(p, 16f, 14f); line(p, 10f, 8f); p.close(); move(p, 11f, 11f); line(p, 4f, 18f); line(p, 6f, 20f); line(p, 13f, 13f) }
    private fun wrench(p: Path) { move(p, 15f, 4f); line(p, 11f, 8f); line(p, 16f, 13f); line(p, 20f, 9f); p.cubicTo(21f, 13f, 17f, 17f, 13f, 16f); line(p, 6f, 21f); line(p, 3f, 18f); line(p, 8f, 11f); p.cubicTo(7f, 7f, 11f, 3f, 15f, 4f); p.close() }

    private fun shieldCheck(p: Path) { move(p, 12f, 3f); line(p, 20f, 6f); line(p, 20f, 12f); p.cubicTo(20f, 17f, 16.5f, 20f, 12f, 21f); p.cubicTo(7.5f, 20f, 4f, 17f, 4f, 12f); line(p, 4f, 6f); p.close(); move(p, 8.5f, 12f); line(p, 11f, 14.5f); line(p, 15.5f, 9.5f) }
    private fun zap(p: Path) { move(p, 13f, 3f); line(p, 5f, 13f); line(p, 11f, 13f); line(p, 10f, 21f); line(p, 19f, 10f); line(p, 13f, 10f); p.close() }
    private fun globe(p: Path) { p.addCircle(12f, 12f, 9f, Path.Direction.CW); move(p, 3f, 12f); line(p, 21f, 12f); p.addOval(android.graphics.RectF(7.5f, 3f, 16.5f, 21f), Path.Direction.CW) }
    private fun server(p: Path) { p.addRoundRect(android.graphics.RectF(3f, 4f, 21f, 10f), 2f, 2f, Path.Direction.CW); p.addRoundRect(android.graphics.RectF(3f, 14f, 21f, 20f), 2f, 2f, Path.Direction.CW); move(p, 7f, 7f); line(p, 7f, 7.1f); move(p, 7f, 17f); line(p, 7f, 17.1f) }
    private fun cpu(p: Path) { p.addRect(android.graphics.RectF(6f, 6f, 18f, 18f), Path.Direction.CW); p.addRect(android.graphics.RectF(10f, 10f, 14f, 14f), Path.Direction.CW); move(p, 9f, 3f); line(p, 9f, 6f); move(p, 15f, 3f); line(p, 15f, 6f); move(p, 9f, 18f); line(p, 9f, 21f); move(p, 15f, 18f); line(p, 15f, 21f); move(p, 3f, 9f); line(p, 6f, 9f); move(p, 3f, 15f); line(p, 6f, 15f); move(p, 18f, 9f); line(p, 21f, 9f); move(p, 18f, 15f); line(p, 21f, 15f) }
    private fun database(p: Path) { p.addOval(android.graphics.RectF(4f, 3f, 20f, 8f), Path.Direction.CW); move(p, 4f, 5.5f); line(p, 4f, 18.5f); p.cubicTo(4f, 21f, 20f, 21f, 20f, 18.5f); line(p, 20f, 5.5f); move(p, 4f, 12f); p.cubicTo(4f, 14.5f, 20f, 14.5f, 20f, 12f) }
    private fun clock(p: Path) { p.addCircle(12f, 12f, 9f, Path.Direction.CW); move(p, 12f, 7f); line(p, 12f, 12f); line(p, 16f, 14f) }
    private fun calendar(p: Path) { p.addRoundRect(android.graphics.RectF(3f, 5f, 21f, 21f), 2f, 2f, Path.Direction.CW); move(p, 3f, 10f); line(p, 21f, 10f); move(p, 8f, 3f); line(p, 8f, 7f); move(p, 16f, 3f); line(p, 16f, 7f) }
    private fun hash(p: Path) { move(p, 5f, 9f); line(p, 19f, 9f); move(p, 5f, 15f); line(p, 19f, 15f); move(p, 10f, 4f); line(p, 8f, 20f); move(p, 16f, 4f); line(p, 14f, 20f) }
    private fun coins(p: Path) { p.addOval(android.graphics.RectF(8f, 8f, 20f, 20f), Path.Direction.CW); move(p, 8f, 8.5f); p.cubicTo(8f, 5f, 3f, 5f, 3f, 8.5f); p.cubicTo(3f, 12f, 8f, 12f, 8f, 15.5f); line(p, 8f, 16f) }
    private fun star(p: Path) { move(p, 12f, 3f); line(p, 14.7f, 8.6f); line(p, 20.8f, 9.4f); line(p, 16.4f, 13.7f); line(p, 17.4f, 19.8f); line(p, 12f, 17f); line(p, 6.6f, 19.8f); line(p, 7.6f, 13.7f); line(p, 3.2f, 9.4f); line(p, 9.3f, 8.6f); p.close() }
    private fun flag(p: Path) { move(p, 5f, 21f); line(p, 5f, 4f); line(p, 18f, 4f); line(p, 15f, 8.5f); line(p, 18f, 13f); line(p, 5f, 13f) }
    private fun target(p: Path) { p.addCircle(12f, 12f, 9f, Path.Direction.CW); p.addCircle(12f, 12f, 5f, Path.Direction.CW); p.addCircle(12f, 12f, 1.5f, Path.Direction.CW) }
    private fun sparkles(p: Path) { move(p, 12f, 3f); line(p, 13.6f, 8.4f); line(p, 19f, 10f); line(p, 13.6f, 11.6f); line(p, 12f, 17f); line(p, 10.4f, 11.6f); line(p, 5f, 10f); line(p, 10.4f, 8.4f); p.close(); move(p, 19f, 15f); line(p, 19.8f, 17.2f); line(p, 22f, 18f); line(p, 19.8f, 18.8f); line(p, 19f, 21f); line(p, 18.2f, 18.8f); line(p, 16f, 18f); line(p, 18.2f, 17.2f); p.close() }
    private fun brain(p: Path) { move(p, 12f, 5f); p.cubicTo(9f, 2f, 4f, 4f, 5f, 8f); p.cubicTo(2f, 9f, 2.5f, 14f, 6f, 15f); p.cubicTo(5f, 19f, 9f, 21f, 12f, 19f); move(p, 12f, 5f); p.cubicTo(15f, 2f, 20f, 4f, 19f, 8f); p.cubicTo(22f, 9f, 21.5f, 14f, 18f, 15f); p.cubicTo(19f, 19f, 15f, 21f, 12f, 19f); line(p, 12f, 5f) }
    private fun bot(p: Path) { p.addRoundRect(android.graphics.RectF(4f, 8f, 20f, 19f), 3f, 3f, Path.Direction.CW); move(p, 12f, 8f); line(p, 12f, 4f); p.addCircle(12f, 3f, 1f, Path.Direction.CW); move(p, 9f, 13f); line(p, 9f, 13.1f); move(p, 15f, 13f); line(p, 15f, 13.1f); move(p, 9.5f, 16.5f); line(p, 14.5f, 16.5f) }
    private fun user(p: Path) { p.addCircle(12f, 8f, 4f, Path.Direction.CW); move(p, 4.5f, 21f); p.cubicTo(5.5f, 16.5f, 8.5f, 15f, 12f, 15f); p.cubicTo(15.5f, 15f, 18.5f, 16.5f, 19.5f, 21f) }
    private fun messageSquare(p: Path) { move(p, 4f, 5f); line(p, 20f, 5f); line(p, 20f, 16f); line(p, 9f, 16f); line(p, 4f, 20f); p.close() }
    private fun send(p: Path) { move(p, 21f, 3f); line(p, 3f, 10.5f); line(p, 10.5f, 13.5f); line(p, 21f, 3f); line(p, 13.5f, 21f); line(p, 10.5f, 13.5f); p.close() }
    private fun paperclip(p: Path) { move(p, 20f, 11.5f); line(p, 12f, 19.5f); p.cubicTo(8f, 23.5f, 2.5f, 18f, 6.5f, 14f); line(p, 15f, 5.5f); p.cubicTo(17.5f, 3f, 21f, 6.5f, 18.5f, 9f); line(p, 10f, 17.5f); p.cubicTo(9f, 18.5f, 7.5f, 17f, 8.5f, 16f); line(p, 16f, 8.5f) }
    private fun stop(p: Path) { p.addRoundRect(android.graphics.RectF(6f, 6f, 18f, 18f), 2f, 2f, Path.Direction.CW) }
    private fun copy(p: Path) { p.addRoundRect(android.graphics.RectF(9f, 9f, 21f, 21f), 2f, 2f, Path.Direction.CW); move(p, 5f, 15f); line(p, 4f, 15f); p.cubicTo(3f, 15f, 3f, 14f, 3f, 13f); line(p, 3f, 4f); p.cubicTo(3f, 3f, 3f, 3f, 4f, 3f); line(p, 13f, 3f); p.cubicTo(14f, 3f, 15f, 3f, 15f, 4f); line(p, 15f, 5f) }
    private fun refresh(p: Path) { move(p, 21f, 4f); line(p, 21f, 10f); line(p, 15f, 10f); move(p, 3f, 20f); line(p, 3f, 14f); line(p, 9f, 14f); p.addArc(android.graphics.RectF(3f, 3f, 21f, 21f), 200f, 130f); p.addArc(android.graphics.RectF(3f, 3f, 21f, 21f), 20f, 130f) }
    private fun trash(p: Path) { move(p, 4f, 7f); line(p, 20f, 7f); move(p, 9f, 7f); line(p, 9f, 4f); line(p, 15f, 4f); line(p, 15f, 7f); move(p, 6f, 7f); line(p, 7f, 20f); p.cubicTo(7f, 21f, 8f, 21f, 8.5f, 21f); line(p, 15.5f, 21f); p.cubicTo(16f, 21f, 17f, 21f, 17f, 20f); line(p, 18f, 7f); move(p, 10f, 11f); line(p, 10f, 17f); move(p, 14f, 11f); line(p, 14f, 17f) }
    private fun download(p: Path) { move(p, 12f, 3f); line(p, 12f, 15f); move(p, 7f, 11f); line(p, 12f, 16f); line(p, 17f, 11f); move(p, 4f, 20f); line(p, 20f, 20f) }
    private fun upload(p: Path) { move(p, 12f, 16f); line(p, 12f, 4f); move(p, 7f, 8f); line(p, 12f, 3f); line(p, 17f, 8f); move(p, 4f, 20f); line(p, 20f, 20f) }
    private fun play(p: Path) { move(p, 7f, 4f); line(p, 19f, 12f); line(p, 7f, 20f); p.close() }
    private fun pause(p: Path) { move(p, 8f, 5f); line(p, 8f, 19f); move(p, 16f, 5f); line(p, 16f, 19f) }
    private fun skipForward(p: Path) { move(p, 5f, 5f); line(p, 13f, 12f); line(p, 5f, 19f); p.close(); move(p, 18f, 5f); line(p, 18f, 19f) }
    private fun externalLink(p: Path) { move(p, 14f, 4f); line(p, 20f, 4f); line(p, 20f, 10f); move(p, 20f, 4f); line(p, 11f, 13f); move(p, 18f, 14f); line(p, 18f, 19f); line(p, 5f, 19f); line(p, 5f, 6f); line(p, 10f, 6f) }
    private fun moreHorizontal(p: Path) { p.addCircle(5f, 12f, 1.2f, Path.Direction.CW); p.addCircle(12f, 12f, 1.2f, Path.Direction.CW); p.addCircle(19f, 12f, 1.2f, Path.Direction.CW) }
    private fun gitBranch(p: Path) { p.addCircle(6f, 5f, 2f, Path.Direction.CW); p.addCircle(6f, 19f, 2f, Path.Direction.CW); p.addCircle(18f, 8f, 2f, Path.Direction.CW); move(p, 6f, 7f); line(p, 6f, 17f); move(p, 6f, 13f); p.cubicTo(6f, 13f, 18f, 14f, 18f, 10f) }
    private fun lock(p: Path) { p.addRoundRect(android.graphics.RectF(4f, 10f, 20f, 21f), 2f, 2f, Path.Direction.CW); move(p, 8f, 10f); line(p, 8f, 7f); p.cubicTo(8f, 4.5f, 16f, 4.5f, 16f, 7f); line(p, 16f, 10f) }
    private fun key(p: Path) { p.addCircle(7.5f, 15.5f, 4f, Path.Direction.CW); move(p, 10.3f, 12.7f); line(p, 20f, 3f); move(p, 16f, 7f); line(p, 19f, 10f); move(p, 13.5f, 9.5f); line(p, 15.5f, 11.5f) }
    private fun settings(p: Path) { p.addCircle(12f, 12f, 3f, Path.Direction.CW); var i = 0; while (i < 8) { val a = Math.toRadians((i * 45).toDouble()); val cx = 12 + Math.cos(a) * 7.5; val cy = 12 + Math.sin(a) * 7.5; p.addCircle(cx.toFloat(), cy.toFloat(), 1.6f, Path.Direction.CW); i++ } }
    private fun bookOpen(p: Path) { move(p, 12f, 6f); p.cubicTo(10f, 4.5f, 6.5f, 4.5f, 3f, 5f); line(p, 3f, 18f); p.cubicTo(6.5f, 17.5f, 10f, 17.5f, 12f, 19f); p.cubicTo(14f, 17.5f, 17.5f, 17.5f, 21f, 18f); line(p, 21f, 5f); p.cubicTo(17.5f, 4.5f, 14f, 4.5f, 12f, 6f); line(p, 12f, 19f) }
    private fun listChecks(p: Path) { move(p, 3f, 6f); line(p, 5f, 8f); line(p, 8f, 4f); move(p, 3f, 13f); line(p, 5f, 15f); line(p, 8f, 11f); move(p, 3f, 20f); line(p, 5f, 22f); line(p, 8f, 18f); move(p, 12f, 6f); line(p, 21f, 6f); move(p, 12f, 13f); line(p, 21f, 13f); move(p, 12f, 20f); line(p, 21f, 20f) }
    private fun scrollText(p: Path) { move(p, 6f, 3f); line(p, 18f, 3f); line(p, 18f, 18f); line(p, 7f, 18f); p.cubicTo(6f, 18f, 6f, 21f, 7.5f, 21f); line(p, 18f, 21f); move(p, 9f, 7f); line(p, 15f, 7f); move(p, 9f, 11f); line(p, 15f, 11f); move(p, 9f, 15f); line(p, 12f, 15f) }
    private fun layers(p: Path) { move(p, 12f, 3f); line(p, 21f, 8f); line(p, 12f, 13f); line(p, 3f, 8f); p.close(); move(p, 3f, 12.5f); line(p, 12f, 17.5f); line(p, 21f, 12.5f); move(p, 3f, 17f); line(p, 12f, 22f); line(p, 21f, 17f) }
    private fun activity(p: Path) { move(p, 3f, 12f); line(p, 7f, 12f); line(p, 10f, 4f); line(p, 14f, 20f); line(p, 17f, 12f); line(p, 21f, 12f) }
    private fun eye(p: Path) { move(p, 2f, 12f); p.cubicTo(5f, 5f, 19f, 5f, 22f, 12f); p.cubicTo(19f, 19f, 5f, 19f, 2f, 12f); p.close(); p.addCircle(12f, 12f, 3f, Path.Direction.CW) }
    private fun code(p: Path) { move(p, 8f, 6f); line(p, 3f, 12f); line(p, 8f, 18f); move(p, 16f, 6f); line(p, 21f, 12f); line(p, 16f, 18f) }
    private fun box(p: Path) { move(p, 12f, 3f); line(p, 21f, 7.5f); line(p, 21f, 16.5f); line(p, 12f, 21f); line(p, 3f, 16.5f); line(p, 3f, 7.5f); p.close(); move(p, 3f, 7.5f); line(p, 12f, 12f); line(p, 21f, 7.5f); move(p, 12f, 12f); line(p, 12f, 21f) }
    private fun sliders(p: Path) { move(p, 4f, 7f); line(p, 20f, 7f); move(p, 4f, 17f); line(p, 20f, 17f); p.addCircle(9f, 7f, 2f, Path.Direction.CW); p.addCircle(15f, 17f, 2f, Path.Direction.CW) }
    private fun link(p: Path) { move(p, 10f, 14f); p.cubicTo(8f, 16f, 4.5f, 12.5f, 8f, 9f); line(p, 11f, 6f); p.cubicTo(12.5f, 4.5f, 15f, 4.5f, 16f, 6f); move(p, 14f, 10f); p.cubicTo(16f, 8f, 19.5f, 11.5f, 16f, 15f); line(p, 13f, 18f); p.cubicTo(11.5f, 19.5f, 9f, 19.5f, 8f, 18f) }
    private fun bell(p: Path) { move(p, 6f, 9f); p.cubicTo(6f, 3f, 18f, 3f, 18f, 9f); line(p, 18f, 14f); line(p, 20.5f, 18f); line(p, 3.5f, 18f); line(p, 6f, 14f); p.close(); move(p, 10f, 20.5f); p.cubicTo(10.5f, 21.8f, 13.5f, 21.8f, 14f, 20.5f) }
    private fun trendingUp(p: Path) { move(p, 3f, 17f); line(p, 9f, 11f); line(p, 13f, 15f); line(p, 21f, 7f); move(p, 15f, 7f); line(p, 21f, 7f); line(p, 21f, 13f) }
    private fun barChart(p: Path) { move(p, 5f, 20f); line(p, 5f, 12f); move(p, 12f, 20f); line(p, 12f, 4f); move(p, 19f, 20f); line(p, 19f, 9f); move(p, 3f, 21f); line(p, 21f, 21f) }
    private fun image(p: Path) { p.addRoundRect(android.graphics.RectF(3f, 4f, 21f, 20f), 2f, 2f, Path.Direction.CW); p.addCircle(8.5f, 9.5f, 1.5f, Path.Direction.CW); move(p, 4f, 17f); line(p, 9f, 12f); line(p, 13f, 16f); line(p, 16f, 13f); line(p, 20f, 17f) }
    private fun film(p: Path) { p.addRect(android.graphics.RectF(3f, 4f, 21f, 20f), Path.Direction.CW); move(p, 7f, 4f); line(p, 7f, 20f); move(p, 17f, 4f); line(p, 17f, 20f); move(p, 3f, 12f); line(p, 21f, 12f); move(p, 3f, 8f); line(p, 7f, 8f); move(p, 3f, 16f); line(p, 7f, 16f); move(p, 17f, 8f); line(p, 21f, 8f); move(p, 17f, 16f); line(p, 21f, 16f) }
    private fun cornerDownRight(p: Path) { move(p, 4f, 4f); line(p, 4f, 12f); p.cubicTo(4f, 15f, 7f, 15f, 10f, 15f); line(p, 15f, 15f); move(p, 10f, 10f); line(p, 16f, 15f); line(p, 10f, 20f) }
    private fun rotateCw(p: Path) { move(p, 21f, 5f); line(p, 21f, 11f); line(p, 15f, 11f); p.addArc(android.graphics.RectF(4f, 4f, 20f, 20f), -20f, 230f) }
    private fun shield(p: Path) { move(p, 12f, 3f); line(p, 20f, 6f); line(p, 20f, 12f); p.cubicTo(20f, 17f, 16.5f, 20f, 12f, 21f); p.cubicTo(7.5f, 20f, 4f, 17f, 4f, 12f); line(p, 4f, 6f); p.close() }
    private fun wifi(p: Path) { move(p, 2.5f, 9f); p.cubicTo(8f, 4f, 16f, 4f, 21.5f, 9f); move(p, 6f, 12.5f); p.cubicTo(9.5f, 9.5f, 14.5f, 9.5f, 18f, 12.5f); move(p, 9.5f, 16f); p.cubicTo(11f, 14.7f, 13f, 14.7f, 14.5f, 16f); p.addCircle(12f, 19f, 1f, Path.Direction.CW) }
}
