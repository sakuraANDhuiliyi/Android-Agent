package com.androidagent.client

import com.androidagent.client.feature.conversation.EventSyncPage
import com.androidagent.client.feature.conversation.HistoryPageRequests
import com.androidagent.client.feature.conversation.drainEventPages
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import org.junit.Assert.*
import org.junit.Test

class EventPaginationTest {
    @Test fun `refresh releases paging but stale completion cannot clear replacement`() {
        val requests = HistoryPageRequests()
        val old = requests.begin()!!
        assertNull(requests.begin())
        requests.invalidate()
        val replacement = requests.begin()!!
        assertFalse(requests.finish(old))
        assertTrue(requests.owns(replacement))
        assertTrue(requests.finish(replacement))
        assertNotNull(requests.begin())
    }

    @Test fun `terminal synchronization consumes more than two pages`() = runBlocking {
        val cursors = mutableListOf<Int>()
        val received = mutableListOf<Int>()
        drainEventPages(0, { true }, { cursor ->
            cursors += cursor
            val end = minOf(245, cursor + 120)
            EventSyncPage((cursor + 1..end).toList(), end, end < 245)
        }, { received.addAll(it) })
        assertEquals(listOf(0, 120, 240), cursors)
        assertEquals((1..245).toList(), received)
    }

    @Test fun `identity change drops in flight page and stops further requests`() = runBlocking {
        var current = true
        var calls = 0
        val page = CompletableDeferred<EventSyncPage<Int>>()
        val received = mutableListOf<Int>()
        val job = launch {
            drainEventPages(0, { current }, { calls++; page.await() }, { received.addAll(it) })
        }
        yield()
        current = false
        page.complete(EventSyncPage(listOf(1), 1, true))
        job.join()
        assertEquals(1, calls)
        assertTrue(received.isEmpty())
    }

    @Test fun `cancellation prevents consuming the pending page`() = runBlocking {
        val page = CompletableDeferred<EventSyncPage<Int>>()
        val received = mutableListOf<Int>()
        val job = launch { drainEventPages(0, { true }, { page.await() }, { received.addAll(it) }) }
        yield()
        job.cancel()
        page.complete(EventSyncPage(listOf(1), 1, false))
        job.join()
        assertTrue(received.isEmpty())
    }

    @Test(expected = IllegalStateException::class)
    fun `non advancing server cursor fails instead of looping forever`() = runBlocking {
        drainEventPages(10, { true }, { EventSyncPage(listOf(10), 10, true) }, {})
    }
}
