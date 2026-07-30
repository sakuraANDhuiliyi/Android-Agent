package com.androidagent.client

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

/**
 * WebSocket-first job event sync with cursor-based HTTP polling fallback.
 */
class JobWatcher(
    private val api: AgentApi,
    private val scope: CoroutineScope,
    private val onEvent: (JSONObject) -> Unit,
    private val onJob: (JobInfo) -> Unit,
    private val onDone: (JobInfo) -> Unit,
    private val onError: (Throwable) -> Unit,
) {
    private var watcherJob: Job? = null
    private var wsHandle: AgentApi.CloseableWatcher? = null
    private val closed = AtomicBoolean(false)
    private val lastEventId = AtomicLong(0)
    private val seenKeys = LinkedHashSet<String>()

    fun start(jobId: String, afterEventId: Long = 0L) {
        stop()
        closed.set(false)
        lastEventId.set(afterEventId)
        watcherJob = scope.launch {
            var useWs = true
            while (isActive && !closed.get()) {
                if (useWs) {
                    val connected = AtomicBoolean(false)
                    val failed = AtomicBoolean(false)
                    val done = AtomicBoolean(false)
                    try {
                        wsHandle = withContext(Dispatchers.IO) {
                            api.watchJob(
                                jobId = jobId,
                                afterEventId = lastEventId.get(),
                                onEvent = { raw ->
                                    connected.set(true)
                                    val event = if (raw.has("event")) raw.optJSONObject("event") ?: raw else raw
                                    if (accept(event)) onEvent(event)
                                },
                                onDone = {
                                    done.set(true)
                                },
                                onFailure = {
                                    failed.set(true)
                                },
                            )
                        }
                        // Wait briefly to see if WS works; otherwise fall back.
                        delay(800)
                        if (failed.get() || !connected.get()) {
                            useWs = false
                            wsHandle?.close()
                            wsHandle = null
                        } else {
                            while (isActive && !closed.get() && !failed.get() && !done.get()) {
                                // Keep WS alive; also poll lightly for status.
                                pollOnce(jobId)?.let { job ->
                                    onJob(job)
                                    if (isTerminal(job.status)) {
                                        onDone(job)
                                        stop()
                                        return@launch
                                    }
                                }
                                delay(1500)
                            }
                            if (done.get()) {
                                pollOnce(jobId)?.let { onDone(it) }
                                stop()
                                return@launch
                            }
                            useWs = false
                            wsHandle?.close()
                            wsHandle = null
                        }
                    } catch (e: Exception) {
                        useWs = false
                        onError(e)
                    }
                }

                // Polling fallback with cursor.
                try {
                    val job = pollOnce(jobId) ?: break
                    onJob(job)
                    if (isTerminal(job.status)) {
                        onDone(job)
                        stop()
                        return@launch
                    }
                } catch (e: Exception) {
                    onError(e)
                }
                delay(1000)
            }
        }
    }

    fun currentCursor(): Long = lastEventId.get()

    fun stop() {
        closed.set(true)
        wsHandle?.close()
        wsHandle = null
        watcherJob?.cancel()
        watcherJob = null
    }

    private suspend fun pollOnce(jobId: String): JobInfo? {
        val job = withContext(Dispatchers.IO) { api.getJob(jobId) }
        for (event in job.events) {
            if (accept(event)) onEvent(event)
        }
        return job
    }

    private fun accept(event: JSONObject): Boolean {
        val id = event.optLong("id", 0L)
        val key = if (id > 0) {
            lastEventId.updateAndGet { maxOf(it, id) }
            "id:$id"
        } else {
            val type = event.optString("type")
            val seq = event.optInt("seq", -1)
            val created = event.optDouble("created_at", 0.0)
            "$type:$seq:$created:${event.optString("message").take(40)}"
        }
        synchronized(seenKeys) {
            if (!seenKeys.add(key)) return false
            if (seenKeys.size > 2000) {
                val iterator = seenKeys.iterator()
                repeat(500) {
                    if (iterator.hasNext()) {
                        iterator.next()
                        iterator.remove()
                    }
                }
            }
        }
        return true
    }

    companion object {
        fun isTerminal(status: String): Boolean =
            status in setOf("succeeded", "failed", "canceled", "interrupted")
    }
}
