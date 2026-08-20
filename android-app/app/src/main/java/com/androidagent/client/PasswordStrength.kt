package com.androidagent.client

import android.content.Context

object PasswordStrength {
    fun score(password: String): Int {
        if (password.length < 8) return 1
        var kinds = 0
        if (password.any { it.isLetter() }) kinds++
        if (password.any { it.isDigit() }) kinds++
        if (password.any { !it.isLetterOrDigit() }) kinds++
        return when {
            kinds >= 3 && password.length >= 12 -> 3
            kinds >= 2 -> 2
            else -> 1
        }
    }

    fun label(context: Context, score: Int): String = context.getString(
        when (score) {
            3 -> R.string.password_strength_strong
            2 -> R.string.password_strength_medium
            else -> R.string.password_strength_weak
        },
    )
}
