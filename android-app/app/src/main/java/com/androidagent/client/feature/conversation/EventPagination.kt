package com.androidagent.client.feature.conversation

import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive

/** A stale request may finish after refresh starts a replacement request. */
internal class HistoryPageRequests {
    private var sequence = 0L
    private var active: Long? = null

    fun begin(): Long? {
        if (active != null) return null
        return (++sequence).also { active = it }
    }

    fun invalidate() { sequence++; active = null }
    fun owns(request: Long): Boolean = active == request
    fun finish(request: Long): Boolean {
        if (!owns(request)) return false
        active = null
        return true
    }
}

internal data class EventSyncPage<T>(val events: List<T>, val nextCursor: Int?, val hasMore: Boolean)

/** Consume every forward page, with cancellation and identity checks between pages. */
internal suspend fun <T> drainEventPages(
    after: Int,
    isCurrent: () -> Boolean,
    fetch: suspend (Int) -> EventSyncPage<T>,
    consume: (List<T>) -> Unit,
) {
    var cursor = after
    while (isCurrent()) {
        currentCoroutineContext().ensureActive()
        val page = fetch(cursor)
        currentCoroutineContext().ensureActive()
        if (!isCurrent()) return
        consume(page.events)
        if (!page.hasMore) return
        val next = page.nextCursor
        check(next != null && next > cursor) { "事件分页游标没有前进" }
        cursor = next
    }
}
