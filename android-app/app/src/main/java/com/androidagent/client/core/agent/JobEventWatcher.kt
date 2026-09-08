package com.androidagent.client.core.agent

import com.androidagent.client.AgentApi
import com.androidagent.client.JobInfo
import com.androidagent.client.JobWatcher
import kotlinx.coroutines.CoroutineScope
import org.json.JSONObject

/** ViewModel 持有的任务事件流抽象；生产实现为 WebSocket 优先的 [JobWatcher]。 */
interface JobEventWatcher {
    fun start(jobId: String, afterEventId: Long)
    fun currentCursor(): Long
    fun stop()
}

fun interface JobWatcherFactory {
    fun create(
        api: AgentApi,
        scope: CoroutineScope,
        onEvent: (JSONObject) -> Unit,
        onJob: (JobInfo) -> Unit,
        onDone: (JobInfo) -> Unit,
        onError: (Throwable) -> Unit,
    ): JobEventWatcher
}

val DefaultJobWatcherFactory = JobWatcherFactory { api, scope, onEvent, onJob, onDone, onError ->
    JobWatcher(api, scope, onEvent, onJob, onDone, onError)
}
