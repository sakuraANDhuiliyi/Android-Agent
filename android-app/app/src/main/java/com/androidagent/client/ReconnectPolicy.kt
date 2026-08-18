package com.androidagent.client

/** Exponential backoff for WebSocket/polling reconnect. */
object ReconnectPolicy {
    const val MAX_DELAY_MS = 30_000L
    const val BASE_DELAY_MS = 1_000L

    fun delayMs(attempt: Int): Long {
        val bounded = attempt.coerceIn(0, 8)
        val delay = BASE_DELAY_MS * (1L shl bounded)
        return delay.coerceAtMost(MAX_DELAY_MS)
    }
}
