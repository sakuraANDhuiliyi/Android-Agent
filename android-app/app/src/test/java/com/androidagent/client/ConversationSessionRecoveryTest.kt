package com.androidagent.client

import androidx.lifecycle.viewModelScope
import com.androidagent.client.core.agent.ConversationSessionPrefs
import com.androidagent.client.core.agent.JobEventWatcher
import com.androidagent.client.core.database.CachedConversationEventEntity
import com.androidagent.client.feature.conversation.ConversationRepository
import com.androidagent.client.feature.conversation.ConversationSignal
import com.androidagent.client.feature.conversation.ConversationUiState
import com.androidagent.client.feature.conversation.ConversationViewModel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.withContext
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/** Exercise the real ViewModel and repository without sockets, services, or Android UI. */
@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class ConversationSessionRecoveryTest {
    private class Session : ConversationSessionPrefs {
        override var selectedJobId: String? = null
        override var selectedProviderId: String = "auto"
        override var guestMode: Boolean = false
        override var guestRemaining: Int = 3
        private val cursors = HashMap<String, Long>()
        override fun eventCursor(jobId: String): Long = cursors[jobId] ?: 0L
        override fun setEventCursor(jobId: String, cursor: Long) {
            cursors[jobId] = cursor
        }
    }

    private class Watcher : JobEventWatcher {
        val starts = ConcurrentLinkedQueue<String>()
        val onDone = AtomicReference<((JobInfo) -> Unit)?>(null)
        override fun start(jobId: String, afterEventId: Long) { starts += jobId }
        override fun currentCursor(): Long = 42L
        override fun stop() = Unit
    }

    private data class Reply(val body: JSONObject, val status: Int = 200)

    private data class Harness(
        val vm: ConversationViewModel,
        val events: FakeConversationEventDao,
        val watcher: Watcher,
        val signals: ConcurrentLinkedQueue<ConversationSignal>,
    )

    private val gates = ArrayList<CountDownLatch>()
    private val viewModels = ArrayList<ConversationViewModel>()
    private val collectors = ArrayList<CoroutineScope>()
    private val mainDispatcher = Executors.newSingleThreadExecutor().asCoroutineDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(mainDispatcher)
    }

    @After
    fun tearDown() {
        // An assertion failure must also release deliberately blocked IO work.
        gates.forEach { it.countDown() }
        runBlocking {
            viewModels.forEach { it.viewModelScope.coroutineContext[Job]?.cancelAndJoin() }
        }
        collectors.forEach { it.cancel() }
        Dispatchers.resetMain()
        mainDispatcher.close()
    }

    private fun gate(): CountDownLatch = CountDownLatch(1).also { gates += it }

    private fun harness(respond: (Request) -> Reply): Harness {
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            // Application interceptor returns a complete response before DNS or sockets.
            val reply = respond(chain.request())
            Response.Builder()
                .request(chain.request())
                .protocol(Protocol.HTTP_1_1)
                .code(reply.status)
                .message("Synthetic test response")
                .body(reply.body.toString().toResponseBody("application/json".toMediaType()))
                .build()
        }.build()
        val api = AgentApi("https://session.test", "synthetic-token", client)
        val events = FakeConversationEventDao()
        val repository = ConversationRepository(
            api, events, FakeConversationDao(), FakeJobDao(), FakeApprovalDao(),
        )
        val watcher = Watcher()
        val vm = ConversationViewModel(
            api = api,
            repository = repository,
            session = Session(),
            allowlist = ApprovalAllowlist(mutableSetOf()),
            persistAllowlist = {},
            trackNewJob = {},
            scheduleTaskSync = {},
            watcherFactory = { _, _, _, _, onDone, _ ->
                watcher.onDone.set(onDone)
                watcher
            },
        )
        viewModels += vm
        val signals = ConcurrentLinkedQueue<ConversationSignal>()
        val collector = CoroutineScope(Dispatchers.Unconfined)
        collector.launch { vm.signals.collect { signals += it } }
        collectors += collector
        return Harness(vm, events, watcher, signals)
    }

    private fun awaitTrue(message: String, condition: () -> Boolean) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
        // TimelineStore is main-thread confined, just as it is in production. An unconfined
        // dispatcher can resume on an IO thread and race a test-thread HashMap traversal.
        while (!runBlocking { withContext(Dispatchers.Main) { condition() } }) {
            if (System.nanoTime() >= deadline) throw AssertionError(message)
            Thread.sleep(10)
        }
    }

    private fun <T> onMain(action: () -> T): T = runBlocking { withContext(Dispatchers.Main) { action() } }

    private fun event(seq: Int, text: String = "message-$seq"): JSONObject = JSONObject()
        .put("id", "event-$seq")
        .put("conversation_id", "conv-1")
        .put("event_type", "assistant_message")
        .put("seq", seq)
        .put("turn_id", "turn-1")
        .put("task_id", "job-1")
        .put("created_at", 1700000000.0 + seq)
        .put("payload", JSONObject().put("message_id", "message-$seq").put("content", text))

    private fun page(
        events: List<JSONObject>,
        hasMore: Boolean = false,
        nextAfter: Int? = null,
        nextBefore: Int? = null,
    ): Reply = Reply(JSONObject()
        .put("conversation_id", "conv-1")
        .put("events", JSONArray(events))
        .put("has_more", hasMore)
        .put("next_after_seq", nextAfter ?: JSONObject.NULL)
        .put("next_before_seq", nextBefore ?: JSONObject.NULL))

    private fun emptyJobs(): Reply = Reply(JSONObject().put("jobs", JSONArray()))

    private fun job(status: String): JSONObject = JSONObject()
        .put("id", "job-1").put("project_id", "p-1").put("conversation_id", "conv-1")
        .put("prompt", "synthetic task").put("status", status).put("created_at", 1700000000.0)

    private fun assertRevokedCacheClearsTimeline(status: Int) {
        val entered = CountDownLatch(1)
        val release = gate()
        val harness = harness { request ->
            assertEquals("/api/conversations/conv-1/events", request.url.encodedPath)
            entered.countDown()
            check(release.await(10, TimeUnit.SECONDS)) { "test did not release authorization response" }
            Reply(JSONObject().put("detail", "Access revoked"), status)
        }
        runBlocking {
            harness.events.upsertAll(listOf(CachedConversationEventEntity.fromEvent(
                event(1, "cached-private-content"), "conv-1", 1L,
            )!!))
        }
        harness.vm.start("p-1", "conv-1")
        assertTrue("remote request was not reached", entered.await(5, TimeUnit.SECONDS))
        assertEquals(ConversationUiState.Source.CACHE, harness.vm.state.value.source)
        assertTrue(harness.vm.store.sortedItems().any { it.content.optString("text") == "cached-private-content" })

        release.countDown()

        awaitTrue("HTTP $status left cached private content visible") {
            harness.signals.isNotEmpty() && harness.vm.state.value.source == ConversationUiState.Source.NONE
        }
        assertTrue(harness.vm.store.sortedItems().isEmpty())
        assertFalse(harness.vm.state.value.offline)
        assertEquals(null, harness.vm.state.value.jobId)
    }

    @Test
    fun `cached timeline is cleared after unauthorized response`() = assertRevokedCacheClearsTimeline(401)

    @Test
    fun `cached timeline is cleared after forbidden response`() = assertRevokedCacheClearsTimeline(403)

    @Test
    fun `cached timeline is cleared after not found response`() = assertRevokedCacheClearsTimeline(404)

    @Test
    fun `refresh during an old history request permits replacement pagination`() {
        val firstPageEntered = CountDownLatch(1)
        val secondPageEntered = CountDownLatch(1)
        val releaseFirstPage = gate()
        val releaseSecondPage = gate()
        val latestRequests = AtomicInteger()
        val historyRequests = AtomicInteger()
        val harness = harness { request ->
            when (request.url.encodedPath) {
                "/api/jobs" -> emptyJobs()
                "/api/conversations/conv-1/events" -> {
                    val before = request.url.queryParameter("before_seq")?.toInt()
                    if (before == Int.MAX_VALUE) {
                        val seq = if (latestRequests.incrementAndGet() == 1) 200 else 300
                        page(listOf(event(seq)), hasMore = true, nextBefore = seq)
                    } else {
                        when (historyRequests.incrementAndGet()) {
                            1 -> {
                                assertEquals(200, before)
                                firstPageEntered.countDown()
                                check(releaseFirstPage.await(10, TimeUnit.SECONDS))
                                page(listOf(event(199, "stale-page")), hasMore = true, nextBefore = 199)
                            }
                            2 -> {
                                assertEquals(300, before)
                                secondPageEntered.countDown()
                                check(releaseSecondPage.await(10, TimeUnit.SECONDS))
                                page(listOf(event(299, "replacement-page")))
                            }
                            else -> throw AssertionError("Unexpected duplicate history request")
                        }
                    }
                }
                else -> throw AssertionError("Unexpected request: ${request.url}")
            }
        }
        onMain { harness.vm.start("p-1", "conv-1") }
        awaitTrue("initial history did not load") { harness.vm.state.value.historyHasMore }
        onMain { harness.vm.loadEarlier() }
        assertTrue(firstPageEntered.await(5, TimeUnit.SECONDS))
        assertTrue(harness.vm.state.value.loadingEarlier)

        onMain { harness.vm.refresh() }
        awaitTrue("refresh did not replace the history cursor") {
            harness.vm.store.conversationSeqMax == 300L && !harness.vm.state.value.loadingEarlier
        }
        onMain { harness.vm.loadEarlier() }
        assertTrue("pagination remained permanently blocked after refresh", secondPageEntered.await(5, TimeUnit.SECONDS))
        assertTrue(harness.vm.state.value.loadingEarlier)
        releaseFirstPage.countDown()
        releaseSecondPage.countDown()

        awaitTrue("replacement page did not complete") {
            !harness.vm.state.value.loadingEarlier && !harness.vm.state.value.historyHasMore
        }
        val text = onMain { harness.vm.store.sortedItems().map { it.content.optString("text") } }
        assertTrue(text.contains("replacement-page"))
        assertFalse(text.contains("stale-page"))
        assertEquals(2, historyRequests.get())
    }

    @Test
    fun `watcher completion drains more than one canonical event page`() {
        val cursors = ConcurrentLinkedQueue<Int>()
        val harness = harness { request ->
            when (request.url.encodedPath) {
                "/api/jobs" -> Reply(JSONObject().put("jobs", JSONArray().put(job("running"))))
                "/api/jobs/job-1" -> Reply(JSONObject().put("job", job("running")))
                "/api/jobs/job-1/approvals" -> Reply(JSONObject().put("approvals", JSONArray()))
                "/api/conversations/conv-1/events" -> {
                    val after = request.url.queryParameter("after_seq")?.toInt()
                    if (after == null) {
                        page(listOf(event(1)))
                    } else {
                        assertEquals("120", request.url.queryParameter("limit"))
                        cursors += after
                        when (after) {
                            1 -> page((2..121).map { event(it) }, hasMore = true, nextAfter = 121)
                            121 -> page((122..151).map { event(it) }, nextAfter = 151)
                            else -> throw AssertionError("Unexpected canonical cursor: $after")
                        }
                    }
                }
                else -> throw AssertionError("Unexpected request: ${request.url}")
            }
        }
        harness.vm.start("p-1", "conv-1")
        awaitTrue("running job was not attached") {
            harness.vm.state.value.job?.status == "running" && harness.watcher.starts.contains("job-1")
        }
        val onDone = harness.watcher.onDone.get()
        assertNotNull(onDone)
        onDone!!.invoke(harness.vm.state.value.job!!.copy(status = "succeeded"))

        awaitTrue("completion sync stopped after its first 120 events") {
            harness.vm.store.conversationSeqMax == 151L
        }
        assertEquals(listOf(1, 121), cursors.toList())
        val cached = runBlocking { harness.events.listByConversation("conv-1") }
        assertEquals(151, cached.size)
        assertEquals(151L, cached.last().seq)
        assertTrue(harness.vm.store.sortedItems().any { it.content.optString("text") == "message-151" })
    }
}
