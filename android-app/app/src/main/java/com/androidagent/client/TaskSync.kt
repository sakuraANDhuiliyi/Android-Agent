package com.androidagent.client

import android.app.Application
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import androidx.work.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/** Server owns execution; this repository only persists observations and notification dedupe. */
class TaskRepository(private val context: Context) {
    private val prefs = AgentPrefs(context)
    val scope: String get() = scopeFor(prefs)
    private val disk get() = context.getSharedPreferences("tasks_$scope", Context.MODE_PRIVATE)

    fun track(id: String) {
        if (!disk.contains("job:$id")) disk.edit().putString("job:$id", "queued").commit()
        TaskSync.schedule(context)
    }

    fun cachedTasks(): List<JobInfo> = AgentApi(prefs.serverUrl, prefs.apiToken)
        .decodeTasks(org.json.JSONObject(disk.getString("snapshot", "{}").orEmpty()))

    fun register() {
        if (!disk.contains("registered")) disk.edit().putLong("registered", System.currentTimeMillis()).commit()
    }

    fun sync(): Boolean {
        if (prefs.apiToken.isBlank()) return false
        val identity = scope
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        val snapshot = api.taskSnapshot()
        val jobs = api.decodeTasks(snapshot)
        if (identity != scopeFor(AgentPrefs(context))) return false
        synchronized(notificationLock) {
            val state = context.getSharedPreferences("tasks_$identity", Context.MODE_PRIVATE)
            state.edit().putString("snapshot", snapshot.toString()).commit()
            for (job in jobs) {
                if (identity != scopeFor(AgentPrefs(context))) return false
                val old = state.getString("job:${job.id}", null)
                // A slower HTTP response must not regress a terminal observation.
                if (old in UiFormat.TERMINAL_STATUSES && job.status !in UiFormat.TERMINAL_STATUSES) continue
                val createdAfterRegistration = (job.createdAt ?: 0.0) * 1000 > state.getLong("registered", Long.MAX_VALUE)
                if (job.status in UiFormat.TERMINAL_STATUSES && old !in UiFormat.TERMINAL_STATUSES && (old != null || createdAfterRegistration)) {
                    JobNotifier.notifyJobFinished(context, job.id, job.status,
                        "${job.prompt.take(100)}\n${job.changedFiles.size} files changed" +
                            (job.buildStatus?.let { "\nBuild · $it" } ?: "") +
                            "\n${job.error ?: job.result.orEmpty().take(300)}",
                        job.projectId, job.conversationId.orEmpty(), job.prompt.take(80), job.turnId)
                }
                val approvals = if (job.status == "awaiting_approval") api.listApprovals(job.id).filter { it.status == "pending" } else emptyList()
                val pendingIds = approvals.map { it.id }.toSet()
                for (previous in state.getStringSet("pending:${job.id}", emptySet()).orEmpty() - pendingIds) {
                    cancelApprovalNotification(context, job.id, previous)
                }
                state.edit().putStringSet("pending:${job.id}", pendingIds).commit()
                if (job.status == "awaiting_approval") {
                    for (approval in approvals) {
                        val key = "approval:${approval.id}"
                        if (!state.getBoolean(key, false) && identity == scopeFor(AgentPrefs(context))) {
                            JobNotifier.notifyApproval(context, job.id, job.projectId, job.conversationId.orEmpty(),
                                job.prompt.take(80), UiFormat.approvalIntent(approval.payload, approval.kind), approval.id, identity)
                            state.edit().putBoolean(key, true).commit()
                        }
                    }
                }
                state.edit().putString("job:${job.id}", job.status).commit()
            }
        }
        return jobs.any { UiFormat.isActive(it.status) }
    }

    companion object {
        private val notificationLock = Any()
        fun cancelApprovalNotification(context: Context, job: String, approval: String) {
            androidx.core.app.NotificationManagerCompat.from(context).cancel("$job-approval:$approval", (job + ":approval:" + approval).hashCode())
        }
        fun scopeFor(prefs: AgentPrefs): String = MessageDigest.getInstance("SHA-256")
            .digest("${prefs.serverUrl}|${prefs.userId}|${prefs.apiToken}".toByteArray())
            .joinToString("") { "%02x".format(it) }
    }
}

object TaskSync {
    fun schedule(context: Context) {
        val prefs = AgentPrefs(context)
        if (prefs.apiToken.isBlank()) return
        TaskRepository(context).register()
        val manager = WorkManager.getInstance(context)
        val data = workDataOf("scope" to TaskRepository.scopeFor(prefs))
        val constraints = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
        manager.enqueueUniquePeriodicWork("task-sync-periodic", ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<TaskSyncWorker>(15, TimeUnit.MINUTES)
                .addTag("periodic").setConstraints(constraints).setInputData(data).build())
        manager.enqueueUniqueWork("task-sync-${TaskRepository.scopeFor(prefs)}", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<TaskSyncWorker>().setConstraints(constraints)
                .setInputData(data).setBackoffCriteria(BackoffPolicy.LINEAR, 30, TimeUnit.SECONDS).build())
    }
}

class TaskSyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val repo = TaskRepository(applicationContext)
        if (inputData.getString("scope") != repo.scope) return@withContext Result.success()
        try {
            var active = repo.sync()
            // Short bounded burst; WorkManager owns retries after the Activity/process exits.
            if (tags.none { it == "periodic" }) repeat(5) {
                if (!active || isStopped || inputData.getString("scope") != repo.scope) return@withContext Result.success()
                delay(8000)
                active = repo.sync()
            }
            if (active) Result.retry() else Result.success()
        } catch (e: kotlinx.coroutines.CancellationException) { throw e }
        catch (e: ApiException) { if (e.code in listOf(401, 403)) Result.failure() else Result.retry() }
        catch (_: Exception) { Result.retry() }
    }
}

class ApprovalActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val job = intent.getStringExtra("job") ?: return
        val approval = intent.getStringExtra("approval") ?: return
        val scope = intent.getStringExtra("scope") ?: return
        if (scope != TaskRepository.scopeFor(AgentPrefs(context))) return
        WorkManager.getInstance(context).enqueueUniqueWork("approval-$approval", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<ApprovalActionWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setInputData(workDataOf("job" to job, "approval" to approval, "scope" to scope,
                    "allow" to intent.getBooleanExtra("allow", false))).build())
    }
}

class ApprovalActionWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val prefs = AgentPrefs(applicationContext)
        if (inputData.getString("scope") != TaskRepository.scopeFor(prefs)) return@withContext Result.success()
        val job = inputData.getString("job") ?: return@withContext Result.failure()
        val approval = inputData.getString("approval") ?: return@withContext Result.failure()
        try {
            val api = AgentApi(prefs.serverUrl, prefs.apiToken)
            if (api.listApprovals(job).any { it.id == approval && it.status == "pending" }) {
                api.resolveApproval(job, approval, inputData.getBoolean("allow", false))
            }
            TaskRepository.cancelApprovalNotification(applicationContext, job, approval)
            TaskSync.schedule(applicationContext)
            Result.success()
        } catch (e: kotlinx.coroutines.CancellationException) { throw e }
        catch (e: ApiException) { if (e.code in listOf(400, 401, 403, 404, 409)) Result.failure() else Result.retry() }
        catch (_: Exception) { Result.retry() }
    }
}

class AgentApplication : Application() {
    private var identity: String? = null
    private val listener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
        if (key in setOf("api_token", "api_token_cipher", "user_id", "server_url", "selected_job_id")) {
            val next = TaskRepository.scopeFor(AgentPrefs(this))
            if (identity != null && next != identity) androidx.core.app.NotificationManagerCompat.from(this).cancelAll()
            identity = next
            TaskSync.schedule(this)
        }
    }
    override fun onCreate() {
        super.onCreate()
        identity = TaskRepository.scopeFor(AgentPrefs(this))
        getSharedPreferences("agent_prefs", MODE_PRIVATE).registerOnSharedPreferenceChangeListener(listener)
        TaskSync.schedule(this)
    }
}
