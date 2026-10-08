package com.androidagent.client.core.database

import com.androidagent.client.AgentPrefs
import okhttp3.HttpUrl.Companion.toHttpUrl
import java.security.MessageDigest

/** Capture once so the API, database and late-response checks always use the same identity. */
class CacheSession(val serverUrl: String, val userId: String, val apiToken: String) {
    private val server = serverUrl.trim().toHttpUrl().newBuilder()
        .query(null).fragment(null).build().toString().trimEnd('/')
    // Older token-only accounts may not yet have a user ID. Never share their cache.
    private val account = if (userId.isBlank()) "token:${digest(apiToken)}" else "account:$userId"
    val databaseName: String = "agent_cache_${digest("$server\n$account")}.db"
    val sessionKey: String = digest("$server\n$userId\n$apiToken")

    fun matches(other: CacheSession): Boolean = sessionKey == other.sessionKey
    fun isCurrent(prefs: AgentPrefs): Boolean = try {
        matches(capture(prefs))
    } catch (_: IllegalArgumentException) {
        false // A cleared/invalid service address also invalidates in-flight work.
    }

    companion object {
        fun capture(prefs: AgentPrefs): CacheSession = CacheSession(prefs.serverUrl, prefs.userId, prefs.apiToken)
        private fun digest(value: String): String = MessageDigest.getInstance("SHA-256")
            .digest(value.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
    }
}
