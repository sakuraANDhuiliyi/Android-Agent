package com.androidagent.client

import androidx.lifecycle.viewModelScope
import com.androidagent.client.core.agent.ConversationSessionPrefs
import com.androidagent.client.core.agent.JobEventWatcher
import com.androidagent.client.feature.conversation.ConversationRepository
import com.androidagent.client.feature.conversation.ConversationSignal
import com.androidagent.client.feature.conversation.ConversationViewModel
import kotlinx.coroutines.*
import kotlinx.coroutines.test.UnconfinedTestDispatcher
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
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/** Real API, repository and ViewModel with controlled IO and separately retained watcher callbacks. */
@OptIn(ExperimentalCoroutinesApi::class)
class ConversationJobRecoveryTest {
    private class Session : ConversationSessionPrefs {
        override var selectedJobId: String? = null
        override var selectedProviderId = "auto"
        override val guestMode = false
        override var guestRemaining = 3
        val cursors = mutableMapOf<String, Long>()
        override fun eventCursor(jobId: String) = cursors[jobId] ?: 0L
        override fun setEventCursor(jobId: String, cursor: Long) { cursors[jobId] = cursor }
    }
    private class Watcher(
        val event: (JSONObject) -> Unit,
        val job: (JobInfo) -> Unit,
        val done: (JobInfo) -> Unit,
        val error: (Throwable) -> Unit,
    ) : JobEventWatcher {
        var jobId = ""
        var initialCursor = -1L
        var stops = 0
        override fun start(jobId: String, afterEventId: Long) { this.jobId = jobId; initialCursor = afterEventId }
        override fun currentCursor() = 42L
        override fun stop() { stops++ }
    }
    private data class Reply(val body: JSONObject, val code: Int = 200)
    private val gates = CopyOnWriteArrayList<CountDownLatch>()
    private val models = CopyOnWriteArrayList<ConversationViewModel>()
    private val collectors = CopyOnWriteArrayList<CoroutineScope>()

    @Before fun setup() { Dispatchers.setMain(UnconfinedTestDispatcher()) }
    @After fun cleanup() {
        gates.forEach { it.countDown() }
        runBlocking { models.forEach { it.viewModelScope.coroutineContext[Job]?.cancelAndJoin() } }
        collectors.forEach { it.cancel() }
        Dispatchers.resetMain()
    }
    private fun gate() = CountDownLatch(1).also { gates += it }
    private fun await(message: String, predicate: () -> Boolean) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
        while (!predicate()) {
            if (System.nanoTime() > deadline) throw AssertionError(message)
            Thread.sleep(10)
        }
    }
    private fun oldJob(canRecover: Boolean = true, recoveryId: String? = null) = JSONObject()
        .put("id", "old").put("project_id", "p").put("conversation_id", "c")
        .put("status", "interrupted").put("can_recover", canRecover)
        .put("recovery_job_id", recoveryId ?: JSONObject.NULL)
    private fun newJob(status: String = "queued", conversation: String = "c") = JSONObject()
        .put("id", "new").put("project_id", "p").put("conversation_id", conversation)
        .put("status", status).put("can_recover", false)
    private fun envelope(job: JSONObject, code: Int = 200) = Reply(JSONObject().put("job", job), code)
    private fun events() = Reply(JSONObject().put("conversation_id", "c").put("events", JSONArray().put(
        JSONObject().put("id", "saved-event").put("conversation_id", "c").put("seq", 1)
            .put("event_type", "assistant_message").put("turn_id", "old-turn").put("task_id", "old")
            .put("payload", JSONObject().put("message_id", "saved-message").put("content", "saved history")),
    )).put("has_more", false))

    private inner class Harness(
        val original: JSONObject = oldJob(),
        val selected: AtomicBoolean = AtomicBoolean(true),
        val currentAccount: AtomicBoolean = AtomicBoolean(true),
        val respond: (Request) -> Reply? = { null },
    ) {
        val session = Session()
        val watchers = CopyOnWriteArrayList<Watcher>()
        val tracked = CopyOnWriteArrayList<String>()
        val requests = CopyOnWriteArrayList<String>()
        val signals = CopyOnWriteArrayList<ConversationSignal>()
        val jobs = FakeJobDao()
        val api = AgentApi("https://recovery.test", "synthetic-token", OkHttpClient.Builder().addInterceptor { chain ->
            val req = chain.request()
            requests += "${req.method} ${req.url.encodedPath}"
            val reply = respond(req) ?: when (req.url.encodedPath) {
                "/api/conversations/c/events" -> events()
                "/api/jobs" -> Reply(JSONObject().put("jobs", JSONArray().put(original)))
                "/api/jobs/old" -> envelope(original)
                "/api/jobs/new" -> envelope(newJob())
                "/api/jobs/old/approvals", "/api/jobs/new/approvals" -> Reply(JSONObject().put("approvals", JSONArray()))
                else -> throw AssertionError("Unexpected request ${req.method} ${req.url}")
            }
            Response.Builder().request(req).protocol(Protocol.HTTP_1_1).code(reply.code)
                .message("Synthetic response").body(reply.body.toString().toResponseBody("application/json".toMediaType())).build()
        }.build())
        val repository = ConversationRepository(api, FakeConversationEventDao(), FakeConversationDao(), jobs,
            FakeApprovalDao(), isCurrentSession = { currentAccount.get() })
        val vm = ConversationViewModel(api, repository, session, ApprovalAllowlist(mutableSetOf()), {},
            { tracked += it }, {}, { _, _, event, job, done, error ->
                Watcher(event, job, done, error).also { watchers += it }
            }, isSelectedConversation = { project, conversation -> project == "p" && conversation == "c" && selected.get() },
        ).also { models += it }
        init {
            CoroutineScope(Dispatchers.Unconfined).also { scope ->
                collectors += scope
                scope.launch { vm.signals.collect { signals += it } }
            }
        }
        fun start() {
            vm.start("p", "c")
            await("initial task did not load") { vm.state.value.job?.id == "old" && watchers.size == 1 }
        }
    }

    @Test fun `double recovery and send while pending create one job and preserve the conversation`() {
        val entered = gate(); val release = gate()
        val h = Harness { request -> if (request.url.encodedPath == "/api/jobs/old/recover") {
            entered.countDown(); assertTrue(release.await(5, TimeUnit.SECONDS)); envelope(newJob(), 201)
        } else null }
        h.start()
        h.session.cursors["new"] = 999L
        h.vm.recoverJob()
        assertTrue(entered.await(5, TimeUnit.SECONDS))
        assertTrue(h.vm.state.value.recovering)
        h.vm.recoverJob()
        h.vm.send("must not send", false, emptyList())
        release.countDown()
        await("new job not attached") { h.vm.state.value.job?.id == "new" && !h.vm.state.value.recovering }
        assertEquals(1, h.requests.count { it == "POST /api/jobs/old/recover" })
        assertFalse(h.requests.any { it.contains("/ask") })
        assertEquals(listOf("new"), h.tracked)
        assertEquals("new", h.session.selectedJobId)
        assertEquals(1, h.watchers.first().stops)
        assertEquals(0L, h.watchers.last().initialCursor)
        assertEquals("c", h.vm.state.value.job!!.conversationId)
        assertTrue(h.vm.store.sortedItems().any { it.content.optString("text") == "saved history" })
        assertFalse(h.signals.any { it is ConversationSignal.ComposerAcknowledged })
    }

    @Test fun `old subscription callbacks cannot overwrite recovered job or inject stale events`() {
        val h = Harness { if (it.url.encodedPath.endsWith("/recover")) envelope(newJob(), 201) else null }
        h.start()
        val original = h.vm.state.value.job!!
        val oldWatcher = h.watchers.single()
        h.vm.recoverJob()
        await("recovery did not finish") { h.vm.state.value.job?.id == "new" && !h.vm.state.value.recovering }
        oldWatcher.job(original)
        oldWatcher.done(original.copy(status = "succeeded"))
        oldWatcher.error(IllegalStateException("stale error"))
        oldWatcher.event(JSONObject().put("type", "assistant_message").put("id", 55)
            .put("task_id", "old").put("text", "stale injected text"))
        assertEquals("new", h.vm.state.value.jobId)
        assertEquals("new", h.session.selectedJobId)
        assertFalse(h.session.cursors.containsKey("old"))
        assertTrue(h.signals.isEmpty())
        assertFalse(h.vm.store.sortedItems().any { it.content.toString().contains("stale injected text") })
    }

    @Test fun `late verification from the previous job cannot mark a recovered job verified`() {
        val original = oldJob().put("verification", verificationFixture("old"))
        val child = newJob().put("verification", verificationFixture("new").also {
            it.getJSONObject("build").put("state", "failed")
            it.getJSONObject("unit_tests").put("state", "interrupted")
        })
        val h = Harness(original = original) {
            when (it.url.encodedPath) {
                "/api/jobs/old/recover" -> envelope(child, 201)
                "/api/jobs/new" -> envelope(child)
                else -> null
            }
        }
        h.start()
        val previous = h.vm.state.value.job!!
        assertTrue(previous.verification.build.verified)
        val oldWatcher = h.watchers.single()
        h.vm.recoverJob()
        await("child verification not loaded") {
            h.vm.state.value.job?.id == "new" && h.vm.state.value.job?.verification?.build?.state == VerificationState.FAILED
        }
        oldWatcher.job(previous)
        oldWatcher.done(previous.copy(status = "succeeded"))
        assertEquals("new", h.vm.state.value.job!!.id)
        assertFalse(h.vm.state.value.job!!.verification.build.verified)
        assertEquals(VerificationState.INTERRUPTED, h.vm.state.value.job!!.verification.tests.state)
    }

    @Test fun `existing paused recovery opens without POST or implicit resume`() {
        val h = Harness(original = oldJob(false, "new")) { request ->
            when (request.url.encodedPath) {
                "/api/jobs" -> Reply(JSONObject().put("jobs", JSONArray().put(newJob("paused")).put(oldJob(false, "new"))))
                "/api/jobs/new" -> envelope(newJob("paused"))
                else -> null
            }
        }
        h.session.selectedJobId = "old"
        val cachedChild = h.api.decodeTasks(JSONObject().put("jobs", JSONArray().put(newJob("paused")))).single()
        runBlocking { h.jobs.upsertAll(listOf(com.androidagent.client.core.database.JobEntity.from(cachedChild, 1L))) }
        h.start(); h.session.cursors["new"] = 12L; h.vm.recoverJob()
        await("paused recovery not opened") { h.vm.state.value.job?.status == "paused" && !h.vm.state.value.recovering }
        assertFalse(h.requests.any { it.startsWith("POST") })
        assertTrue(h.tracked.isEmpty())
        assertEquals(12L, h.watchers.last().initialCursor)
    }

    @Test fun `missing recoverability does not infer permission from interrupted status`() {
        val h = Harness(original = oldJob(false))
        h.start(); h.vm.recoverJob()
        assertFalse(h.vm.state.value.recovering)
        assertFalse(h.requests.any { it.endsWith("/recover") })
    }

    @Test fun `recovery response after account switch cannot change selection cache or watchers`() {
        val entered = gate(); val release = gate()
        val h = Harness { request -> if (request.url.encodedPath.endsWith("/recover")) {
            entered.countDown(); assertTrue(release.await(5, TimeUnit.SECONDS)); envelope(newJob(), 201)
        } else null }
        h.start(); h.vm.recoverJob(); assertTrue(entered.await(5, TimeUnit.SECONDS))
        h.currentAccount.set(false); h.session.selectedJobId = "other-account-job"; release.countDown()
        await("stale account result not discarded") { !h.vm.state.value.recovering }
        assertNull(h.vm.state.value.job)
        assertEquals("other-account-job", h.session.selectedJobId)
        assertFalse(h.jobs.rows.containsKey("new"))
        assertEquals(1, h.watchers.size)
        assertTrue(h.tracked.isEmpty())
    }

    @Test fun `recovery response and background watcher cannot select a different conversation`() {
        val entered = gate(); val release = gate()
        val h = Harness { request -> if (request.url.encodedPath.endsWith("/recover")) {
            entered.countDown(); assertTrue(release.await(5, TimeUnit.SECONDS)); envelope(newJob(), 201)
        } else null }
        h.start(); val original = h.vm.state.value.job!!
        h.vm.recoverJob(); assertTrue(entered.await(5, TimeUnit.SECONDS))
        h.selected.set(false); h.session.selectedJobId = "other-conversation-job"
        h.watchers.single().job(original)
        release.countDown()
        await("stale conversation result not discarded") { !h.vm.state.value.recovering }
        assertEquals("old", h.vm.state.value.jobId)
        assertEquals("other-conversation-job", h.session.selectedJobId)
        assertFalse(h.jobs.rows.containsKey("new"))
        assertEquals(1, h.watchers.size)
        assertTrue(h.signals.isEmpty())
    }

    @Test fun `mismatched recovery response is rejected and busy state is released`() {
        val h = Harness { if (it.url.encodedPath.endsWith("/recover")) envelope(newJob(conversation = "foreign"), 201) else null }
        h.start(); h.vm.recoverJob()
        await("invalid response was not rejected") { h.signals.isNotEmpty() && !h.vm.state.value.recovering }
        assertEquals("old", h.vm.state.value.jobId)
        assertFalse(h.jobs.rows.containsKey("new"))
        assertEquals(1, h.watchers.size)
    }

    @Test fun `network failure permits retry and a successful retry binds once`() {
        val attempts = AtomicInteger()
        val h = Harness { if (it.url.encodedPath.endsWith("/recover")) {
            if (attempts.incrementAndGet() == 1) Reply(JSONObject().put("detail", "temporary failure"), 503)
            else envelope(newJob(), 201)
        } else null }
        h.start(); h.vm.recoverJob()
        await("failed recovery remained busy") { h.signals.isNotEmpty() && !h.vm.state.value.recovering }
        assertEquals("old", h.vm.state.value.jobId)
        h.vm.recoverJob()
        await("retry failed") { h.vm.state.value.jobId == "new" && !h.vm.state.value.recovering }
        assertEquals(2, attempts.get())
        assertEquals(listOf("new"), h.tracked)
    }

    @Test fun `late original detail response cannot replace the recovery subscription`() {
        val entered = gate(); val release = gate(); val returned = gate()
        val h = Harness { request -> when (request.url.encodedPath) {
            "/api/jobs/old" -> {
                entered.countDown(); assertTrue(release.await(5, TimeUnit.SECONDS))
                returned.countDown(); envelope(oldJob())
            }
            "/api/jobs/old/recover" -> envelope(newJob(), 201)
            else -> null
        } }
        h.vm.start("p", "c")
        assertTrue(entered.await(5, TimeUnit.SECONDS))
        // A websocket snapshot arrives while attachJob's initial HTTP request is still running.
        val original = h.api.decodeTasks(JSONObject().put("jobs", JSONArray().put(h.original))).single()
        h.watchers.single().job(original)
        assertEquals("old", h.vm.state.value.jobId)
        h.vm.recoverJob()
        await("recovery not attached") { h.vm.state.value.jobId == "new" && !h.vm.state.value.recovering }
        release.countDown(); assertTrue(returned.await(5, TimeUnit.SECONDS))
        // Waiting for the old coroutine to process its response also makes the assertion catch
        // the separate attachJob HTTP race, rather than just the watcher callback race.
        runBlocking { h.vm.viewModelScope.coroutineContext[Job]!!.children.toList().joinAll() }
        assertEquals("new", h.vm.state.value.job!!.id)
        assertEquals("new", h.session.selectedJobId)
        assertFalse(h.requests.contains("GET /api/jobs/old/approvals"))
    }

    @Test fun `stale refresh list cannot reattach original after recovery`() {
        val listReads = AtomicInteger(); val entered = gate(); val release = gate(); val returned = gate()
        val h = Harness { request -> when {
            request.url.encodedPath == "/api/jobs" && listReads.incrementAndGet() == 2 -> {
                entered.countDown(); assertTrue(release.await(5, TimeUnit.SECONDS)); returned.countDown()
                Reply(JSONObject().put("jobs", JSONArray().put(oldJob())))
            }
            request.url.encodedPath.endsWith("/recover") -> envelope(newJob(), 201)
            else -> null
        } }
        h.start(); h.vm.refresh(); assertTrue(entered.await(5, TimeUnit.SECONDS))
        h.vm.recoverJob()
        await("recovery not attached") { h.vm.state.value.jobId == "new" && !h.vm.state.value.recovering }
        release.countDown(); assertTrue(returned.await(5, TimeUnit.SECONDS))
        runBlocking { h.vm.viewModelScope.coroutineContext[Job]!!.children.toList().joinAll() }
        assertEquals("new", h.vm.state.value.jobId)
        assertEquals(listOf("old", "new"), h.watchers.map { it.jobId })
    }

    @Test fun `sending a new task blocks recovery until its response is attached`() {
        val entered = gate(); val release = gate()
        val h = Harness { request -> if (request.url.encodedPath.endsWith("/ask")) {
            entered.countDown(); assertTrue(release.await(5, TimeUnit.SECONDS)); envelope(newJob(), 201)
        } else null }
        h.start(); h.vm.send("new request", false, emptyList())
        assertTrue(entered.await(5, TimeUnit.SECONDS))
        h.vm.recoverJob()
        assertFalse(h.vm.state.value.recovering)
        assertFalse(h.requests.any { it.endsWith("/recover") })
        release.countDown()
        await("sent job not attached") { h.vm.state.value.jobId == "new" && !h.vm.state.value.sending }
    }
}
