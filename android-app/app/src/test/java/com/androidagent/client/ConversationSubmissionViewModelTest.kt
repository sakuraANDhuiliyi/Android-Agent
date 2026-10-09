package com.androidagent.client

import androidx.lifecycle.viewModelScope
import com.androidagent.client.core.agent.ConversationSessionPrefs
import com.androidagent.client.core.agent.JobEventWatcher
import com.androidagent.client.core.database.JobEntity
import com.androidagent.client.feature.conversation.ConversationRepository
import com.androidagent.client.feature.conversation.ConversationSignal
import com.androidagent.client.feature.conversation.ConversationViewModel
import kotlinx.coroutines.*
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.*
import org.junit.Assert.*
import java.io.IOException
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

@OptIn(ExperimentalCoroutinesApi::class)
class ConversationSubmissionViewModelTest {
    private class Session : ConversationSessionPrefs {
        override var selectedJobId: String? = null
        override var selectedProviderId = "provider-a"
        override var guestMode = false
        override var guestRemaining = 3
        override fun eventCursor(jobId: String) = 0L
        override fun setEventCursor(jobId: String, cursor: Long) = Unit
    }
    private val models = CopyOnWriteArrayList<ConversationViewModel>()
    private val collectors = CopyOnWriteArrayList<CoroutineScope>()
    private val gates = CopyOnWriteArrayList<CountDownLatch>()
    @Before fun setup() { Dispatchers.setMain(Dispatchers.Unconfined) }
    @After fun cleanup() {
        gates.forEach { it.countDown() }
        runBlocking { models.forEach { it.viewModelScope.coroutineContext[Job]?.cancelAndJoin() } }
        collectors.forEach { it.cancel() }; Dispatchers.resetMain()
    }
    private fun gate() = CountDownLatch(1).also { gates += it }
    private fun await(condition: () -> Boolean) {
        val until = System.nanoTime() + TimeUnit.SECONDS.toNanos(6)
        while (!condition()) { if (System.nanoTime() > until) fail("Condition not reached"); Thread.sleep(10) }
    }
    private fun job(id: String = "old", status: String = "succeeded") = JSONObject().put("id", id).put("project_id", "p")
        .put("conversation_id", "c").put("turn_id", "$id-turn").put("status", status).put("prompt", "$id prompt")
    private fun pending() = PendingConversationSubmission.freeze("p", "c", "original", "provider-a", listOf(ContextAttachment("file", "a", text = "full context")))
    private fun ack(p: PendingConversationSubmission, status: String = "paused") = JSONObject().put("schema_version", 1).put("conversation_id", "c")
        .put("submission", JSONObject().put("schema_version", 1).put("request_key", p.key).put("project_id", "p")
            .put("conversation_id", "c").put("job_id", "accepted").put("turn_id", "accepted-turn").put("created_at", 1700000000))
        .put("job", job("accepted", status))
    private fun body(req: Request) = JSONObject(Buffer().also { req.body!!.writeTo(it) }.readUtf8())
    private class Disk { @Volatile var raw: String? = null; @Volatile var failed = false }
    private inner class Harness(val disk: Disk = Disk(), val respond: (Request) -> JSONObject? = { null }) {
        val scope = submissionScope("https://submission.test", "u", "p", "c")
        val storage = ConversationSubmissionStore({ disk.raw }, { if (disk.failed) false else { disk.raw = it; true } })
        var afterSave: () -> Unit = {}
        val session = Session(); val account = AtomicBoolean(true); val selected = AtomicBoolean(true)
        val requests = CopyOnWriteArrayList<String>(); val bodies = CopyOnWriteArrayList<JSONObject>()
        val watches = CopyOnWriteArrayList<String>(); val signals = CopyOnWriteArrayList<ConversationSignal>()
        var jobs = listOf(job()); val jobDao = FakeJobDao()
        val api = AgentApi("https://submission.test", "synthetic", OkHttpClient.Builder().addInterceptor { chain ->
            val req = chain.request(); requests += "${req.method} ${req.url.encodedPath}"
            if (req.method == "POST" && req.url.encodedPath.endsWith("/ask")) bodies += body(req)
            val response = respond(req) ?: when {
                req.url.encodedPath == "/api/conversations/c/events" -> JSONObject().put("conversation_id", "c").put("events", JSONArray()).put("has_more", false)
                req.url.encodedPath == "/api/jobs" -> JSONObject().put("jobs", JSONArray(jobs))
                req.url.encodedPath.endsWith("/approvals") -> JSONObject().put("approvals", JSONArray())
                req.url.encodedPath.endsWith("/messages") -> JSONObject().put("schema_version", 1).put("job_id", req.url.pathSegments[2]).put("messages", JSONArray())
                req.url.encodedPath.endsWith("/cancel") -> JSONObject().put("job", job(req.url.pathSegments[2], "canceled"))
                req.url.encodedPath.contains("/submissions/") -> ack(storage.load(scope).pending!!)
                req.url.encodedPath.endsWith("/ask") -> ack(storage.load(scope).pending!!)
                req.url.encodedPath.startsWith("/api/jobs/") -> JSONObject().put("job", jobs.firstOrNull { it.getString("id") == req.url.pathSegments.last() } ?: job("accepted", "paused"))
                else -> error("Unexpected ${req.method} ${req.url}")
            }
            Response.Builder().request(req).protocol(Protocol.HTTP_1_1).code(response.optInt("__status", 200)).message("Synthetic")
                .body(response.toString().toResponseBody("application/json".toMediaType())).build()
        }.build())
        val repo = ConversationRepository(api, FakeConversationEventDao(), FakeConversationDao(), jobDao, FakeApprovalDao(), { account.get() })
        val vm = ConversationViewModel(api, repo, session, ApprovalAllowlist(mutableSetOf()), {}, {}, {},
            { _, _, _, _, _, _ -> object : JobEventWatcher {
                override fun start(jobId: String, afterEventId: Long) { watches += jobId }
                override fun currentCursor() = 0L
                override fun stop() = Unit
            } }, isSelectedConversation = { _, _ -> selected.get() },
            readSubmission = { _, _ -> storage.load(scope) }, saveSubmission = { storage.save(scope, it); afterSave() },
            removeSubmission = { p, confirmed -> storage.remove(scope, p.key, confirmed) }, receiptPollIntervalMs = 100000)
            .also { models += it }
        init { CoroutineScope(Dispatchers.Unconfined).also { collectors += it }.launch { vm.signals.collect { signals += it } } }
        fun start(blocked: Boolean = false) {
            vm.start("p", "c"); vm.setForeground(true)
            if (!blocked) await { vm.state.value.job?.id == "old" }
            else await { requests.contains("GET /api/jobs") && !vm.state.value.checkingSubmission }
        }
    }

    @Test fun `POST has durable full snapshot and one click gate then adopts valid paused ACK without resume`() {
        val entered = gate(); val release = gate()
        lateinit var h: Harness
        h = Harness { r -> if (r.url.encodedPath.endsWith("/ask")) {
            assertNotNull(h.storage.load(h.scope).pending); entered.countDown(); release.await(5, TimeUnit.SECONDS)
        }; null }
        h.start(); h.vm.send("original", false, listOf(ContextAttachment("file", "a", text = "full context")), "composer:1")
        assertTrue(entered.await(5, TimeUnit.SECONDS)); h.vm.send("second", false, emptyList())
        h.session.selectedProviderId = "provider-b"; release.countDown(); await { h.vm.state.value.jobId == "accepted" }
        assertEquals(1, h.bodies.size); assertEquals("provider-a", h.bodies.single().getString("provider"))
        assertEquals("full context", h.bodies.single().getJSONArray("contexts").getJSONObject(0).getString("text"))
        assertNull(h.storage.load(h.scope).pending); assertFalse(h.requests.any { it.endsWith("/resume") })
        assertEquals("composer:1", h.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().single().submitted.generation)
    }

    @Test fun `response loss reopening only locates and exact original retry ignores changed controls`() {
        val disk = Disk(); val h = Harness(disk) { r -> if (r.url.encodedPath.endsWith("/ask")) throw IOException("response lost"); null }
        h.start(); h.vm.send("original", false, listOf(ContextAttachment("file", "a", text = "full context")), "old-instance")
        await { h.bodies.size == 1 && !h.vm.state.value.submittingTask }; val original = h.storage.load(h.scope).pending!!
        h.vm.setForeground(false)
        val reopened = Harness(disk); reopened.jobs = listOf(job("accepted", "running")); reopened.session.selectedJobId = "accepted"
        reopened.start(blocked = true); await { reopened.vm.state.value.submissionCandidate != null }
        assertEquals(original, reopened.vm.state.value.pendingSubmission); assertNull(reopened.vm.state.value.jobId)
        assertTrue(reopened.watches.isEmpty()); assertTrue(reopened.requests.all { it.startsWith("GET") })
        assertFalse(reopened.requests.any { it.endsWith("/approvals") }); assertTrue(reopened.signals.isEmpty())
        reopened.session.selectedProviderId = "different"; reopened.vm.retrySubmission()
        await { reopened.vm.state.value.pendingSubmission == null }
        assertEquals(original.requestBody().toString(), reopened.bodies.single().toString())
        assertTrue(reopened.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isEmpty())
    }

    @Test fun `pending or corrupt storage blocks cached active and newest attach with zero ask or approval`() {
        for (corrupt in listOf(false, true)) {
            val h = Harness(); if (corrupt) h.disk.raw = "broken" else h.storage.save(h.scope, pending())
            h.jobs = listOf(job("accepted", "running")); h.session.selectedJobId = "accepted"
            runBlocking { h.jobDao.upsertAll(listOf(JobEntity.from(h.api.getJob("accepted"), 1L))) }
            h.start(blocked = true); h.vm.send("new", false, emptyList()); h.vm.refresh(); Thread.sleep(80)
            assertNull(h.vm.state.value.jobId); assertTrue(h.watches.isEmpty()); assertTrue(h.bodies.isEmpty())
            assertFalse(h.requests.any { it.endsWith("/approvals") }); assertEquals(corrupt, h.vm.state.value.submissionStorageError)
        }
    }

    @Test fun `persistence failure prevents network send and preserves initial binding`() {
        val h = Harness(); h.start(); h.disk.failed = true; h.vm.send("original", false, emptyList())
        assertTrue(h.bodies.isEmpty()); assertEquals("old", h.vm.state.value.jobId); assertFalse(h.vm.state.value.sending)
    }

    @Test fun `selection account foreground and job changes during persistence dispatch zero POST`() {
        for (change in listOf("selection", "account", "foreground", "job")) {
            val h = Harness(); h.start()
            h.afterSave = { when (change) {
                "selection" -> h.selected.set(false)
                "account" -> h.account.set(false)
                "foreground" -> h.vm.setForeground(false)
                "job" -> h.session.selectedJobId = "newer"
            } }
            h.vm.send("original", false, emptyList()); Thread.sleep(80)
            assertNotNull(h.storage.load(h.scope).pending); assertTrue(h.bodies.isEmpty())
            assertFalse(h.watches.contains("accepted")); assertTrue(h.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isEmpty())
        }
    }

    @Test fun `malformed ACK identities keep original record draft and old binding`() {
        for (field in listOf("request_key", "project_id", "conversation_id", "job_id", "turn_id", "created_at", "schema_version")) {
            lateinit var h: Harness
            h = Harness { r -> if (r.url.encodedPath.endsWith("/ask")) ack(h.storage.load(h.scope).pending!!).also {
                it.getJSONObject("submission").put(field, if (field == "created_at") 0 else "wrong")
            } else null }
            h.start(); h.vm.send("original", false, emptyList(), "draft")
            await { h.bodies.size == 1 && !h.vm.state.value.submittingTask }
            assertNotNull(h.storage.load(h.scope).pending); assertEquals("old", h.vm.state.value.jobId)
            assertTrue(h.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isEmpty())
            h.vm.setForeground(false)
        }
    }

    @Test fun `POST without top level conversation id retains original but GET may omit it`() {
        lateinit var h: Harness
        h = Harness { r -> if (r.url.encodedPath.endsWith("/ask") || r.url.encodedPath.contains("/submissions/"))
            ack(h.storage.load(h.scope).pending!!).also { it.remove("conversation_id") } else null }
        h.start(); h.vm.send("original", false, emptyList()); await { h.bodies.size == 1 && !h.vm.state.value.submittingTask }
        assertNotNull(h.vm.state.value.pendingSubmission); assertEquals("old", h.vm.state.value.jobId)
        h.vm.refreshSubmission(); await { h.vm.state.value.submissionCandidate != null }
        assertNotNull(h.vm.state.value.pendingSubmission); assertEquals("old", h.vm.state.value.jobId)
    }

    @Test fun `job mismatch even with valid immutable receipt cannot confirm`() {
        for (field in listOf("id", "project_id", "conversation_id", "turn_id")) {
            lateinit var h: Harness
            h = Harness { r -> if (r.url.encodedPath.endsWith("/ask")) ack(h.storage.load(h.scope).pending!!).also { it.getJSONObject("job").put(field, "wrong") } else null }
            h.start(); h.vm.send("original", false, emptyList()); await { h.bodies.size == 1 && !h.vm.state.value.submittingTask }
            assertNotNull(h.vm.state.value.pendingSubmission); assertEquals("old", h.vm.state.value.jobId); h.vm.setForeground(false)
        }
    }

    @Test fun `late POST cannot confirm after background selection ABA account or same conversation job change`() {
        for (change in listOf("background", "aba", "account", "job")) {
            val entered = gate(); val release = gate(); val finished = gate()
            lateinit var h: Harness
            h = Harness { r -> if (r.url.encodedPath.endsWith("/ask")) {
                val result = ack(h.storage.load(h.scope).pending!!); entered.countDown(); release.await(5, TimeUnit.SECONDS); finished.countDown(); result
            } else null }
            h.start(); h.vm.send("original", false, emptyList(), "old"); assertTrue(entered.await(5, TimeUnit.SECONDS))
            when (change) {
                "background" -> h.vm.setForeground(false)
                "aba" -> { h.vm.setForeground(false); h.selected.set(false); h.selected.set(true); h.vm.setForeground(true) }
                "account" -> h.account.set(false)
                "job" -> h.session.selectedJobId = "newer"
            }
            release.countDown(); assertTrue(finished.await(5, TimeUnit.SECONDS)); Thread.sleep(80)
            assertNotNull(h.storage.load(h.scope).pending); assertFalse(h.watches.contains("accepted"))
            assertTrue(h.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isEmpty()); h.vm.setForeground(false)
        }
    }

    @Test fun `permanent errors preserve rejected original while 401 and transient errors remain uncertain without unkeyed fallback`() {
        for (code in listOf(400, 401, 403, 404, 409, 413, 422, 429, 500)) {
            val h = Harness { r -> if (r.url.encodedPath.endsWith("/ask")) JSONObject().put("__status", code).put("detail", "Synthetic") else null }
            h.start(); h.vm.send("original", false, emptyList()); await { h.bodies.size == 1 && !h.vm.state.value.submittingTask }
            val saved = h.storage.load(h.scope).pending!!
            assertEquals(if (code in setOf(400, 403, 404, 409, 413, 422)) code else null, saved.rejectedCode)
            assertTrue(h.bodies.single().has("request_key")); assertEquals(1, h.bodies.size); h.vm.setForeground(false)
        }
    }

    @Test fun `local removal has no server calls holds across reopening and retains already bound Stop`() {
        val h = Harness(); h.start(); h.storage.save(h.scope, pending()); h.jobs = listOf(job("accepted", "running"), job("old", "running"))
        h.vm.setForeground(false); h.vm.setForeground(true); await { h.vm.state.value.submissionCandidate != null }
        h.vm.refresh(); await { h.vm.state.value.job?.status == "running" }; val requests = h.requests.size
        h.vm.removeLocalSubmission(); assertEquals(requests, h.requests.size)
        assertTrue(h.storage.load(h.scope).manualHold); assertNull(h.storage.load(h.scope).pending)
        h.vm.controlJob("cancel"); await { h.requests.contains("POST /api/jobs/old/cancel") }
        h.vm.setForeground(false)
        val reopened = Harness(h.disk); reopened.jobs = listOf(job("accepted", "running")); reopened.session.selectedJobId = "accepted"
        reopened.start(blocked = true); assertTrue(reopened.vm.state.value.manualAttachmentHold); assertNull(reopened.vm.state.value.jobId)
        assertTrue(reopened.watches.isEmpty()); assertFalse(reopened.requests.any { it.startsWith("POST") })
        reopened.vm.listTasksForManualSelection(); await { reopened.vm.state.value.taskSelection.isNotEmpty() }
        reopened.vm.selectTaskManually("accepted"); await { reopened.vm.state.value.jobId == "accepted" }
        assertTrue(reopened.storage.load(reopened.scope).manualHold)
    }

    @Test fun `remove disabled during POST and failed removal preserves original without hold`() {
        val entered = gate(); val release = gate()
        val h = Harness { r -> if (r.url.encodedPath.endsWith("/ask")) { entered.countDown(); release.await(5, TimeUnit.SECONDS); throw IOException("lost") }; null }
        h.start(); h.vm.send("original", false, emptyList()); assertTrue(entered.await(5, TimeUnit.SECONDS))
        h.vm.removeLocalSubmission(); assertNotNull(h.storage.load(h.scope).pending)
        release.countDown(); await { !h.vm.state.value.submittingTask }; h.disk.failed = true; h.vm.removeLocalSubmission()
        assertNotNull(h.vm.state.value.pendingSubmission); assertFalse(h.storage.load(h.scope).manualHold)
    }

    @Test fun `late candidate GET after local removal cannot reopen or connect its task`() {
        val entered = gate(); val release = gate(); val finished = gate()
        val original = pending()
        val h = Harness { r -> if (r.url.encodedPath.contains("/submissions/")) {
            entered.countDown(); release.await(5, TimeUnit.SECONDS); finished.countDown(); ack(original)
        } else null }
        h.storage.save(h.scope, original); h.vm.start("p", "c"); h.vm.setForeground(true)
        assertTrue(entered.await(5, TimeUnit.SECONDS)); h.vm.removeLocalSubmission(); release.countDown()
        assertTrue(finished.await(5, TimeUnit.SECONDS)); Thread.sleep(80)
        h.vm.refresh(); Thread.sleep(80)
        assertNull(h.vm.state.value.pendingSubmission); assertNull(h.vm.state.value.submissionCandidate)
        assertTrue(h.vm.state.value.manualAttachmentHold); assertNull(h.vm.state.value.jobId); assertTrue(h.watches.isEmpty())
        assertFalse(h.requests.any { it.startsWith("POST") || it.endsWith("/approvals") })
    }

    @Test fun `already bound Stop remains available during submission confirmation`() {
        val entered = gate(); val release = gate()
        lateinit var h: Harness
        h = Harness { r -> if (r.url.encodedPath.endsWith("/ask")) {
            val result = ack(h.storage.load(h.scope).pending!!); entered.countDown(); release.await(5, TimeUnit.SECONDS); result
        } else null }
        h.start(); h.vm.send("original", false, emptyList()); assertTrue(entered.await(5, TimeUnit.SECONDS))
        h.vm.controlJob("cancel"); await { h.requests.contains("POST /api/jobs/old/cancel") }
        assertTrue(h.vm.state.value.submittingTask); release.countDown(); await { !h.vm.state.value.submittingTask }
    }

    @Test fun `lookup404 does not permit a new send or alternate key`() {
        val h = Harness { r -> if (r.url.encodedPath.contains("/submissions/")) JSONObject().put("__status", 404) else null }
        h.storage.save(h.scope, pending()); h.start(blocked = true); h.vm.send("other", false, emptyList())
        assertNotNull(h.vm.state.value.pendingSubmission); assertTrue(h.bodies.isEmpty())
    }

    @Test fun `known guest keeps legacy request without key`() {
        val h = Harness { r -> if (r.url.encodedPath.endsWith("/ask")) JSONObject().put("job", job("accepted", "paused")) else null }
        h.session.guestMode = true; h.start(); h.vm.send("guest", false, emptyList())
        await { h.vm.state.value.jobId == "accepted" }; assertFalse(h.bodies.single().has("request_key")); assertNull(h.disk.raw)
    }
}
