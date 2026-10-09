package com.androidagent.client

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AgentEmotionPlaybackTest {
    @Test fun `page finishing after backgrounding must not restart the avatar`() {
        val state = AgentEmotionPlaybackState()
        state.attach(isVisible = true)
        state.setForeground(true)
        assertTrue(state.shouldResume)
        assertFalse(state.shouldAnimate)

        state.setForeground(false)
        state.pageFinished()
        assertFalse(state.shouldResume)
        assertFalse(state.shouldAnimate)

        state.setForeground(true)
        assertTrue(state.shouldAnimate)
    }

    @Test fun `ancestor or window hiding stops playback until visible again`() {
        val state = runningState()
        state.setVisible(false)
        assertFalse(state.shouldResume)
        assertFalse(state.shouldAnimate)

        // A duplicate resume callback cannot override a hidden parent or window.
        state.setForeground(true)
        assertFalse(state.shouldAnimate)
        state.setVisible(true)
        assertTrue(state.shouldResume)
        assertTrue(state.shouldAnimate)
    }

    @Test fun `detaching and attaching under a hidden parent preserves the pause`() {
        val state = runningState()
        state.detach()
        state.setVisible(true)
        assertFalse(state.shouldResume)
        assertFalse(state.shouldAnimate)

        state.attach(isVisible = false)
        assertFalse(state.shouldAnimate)
        state.setVisible(true)
        assertTrue(state.shouldAnimate)
    }

    @Test fun `disabling system animations keeps a visible renderer ready for static updates`() {
        val state = runningState()
        state.setAnimationsEnabled(false)
        assertTrue(state.ready)
        assertTrue(state.shouldResume)
        assertFalse(state.shouldAnimate)

        state.setAnimationsEnabled(true)
        assertTrue(state.shouldAnimate)
        state.setForeground(false)
        state.setAnimationsEnabled(false)
        state.setAnimationsEnabled(true)
        assertFalse(state.shouldAnimate)
    }

    @Test fun `animation preference set before page load remains effective after loading`() {
        val state = AgentEmotionPlaybackState()
        state.setAnimationsEnabled(false)
        state.setForeground(true)
        state.attach(isVisible = true)
        state.pageFinished()
        assertTrue(state.shouldResume)
        assertFalse(state.shouldAnimate)
    }

    @Test fun `release is terminal even when queued callbacks arrive afterwards`() {
        val state = runningState()
        state.release()
        state.release()
        state.pageFinished()
        state.setForeground(true)
        state.attach(isVisible = true)
        state.setVisible(true)
        state.setAnimationsEnabled(true)
        assertTrue(state.released)
        assertFalse(state.ready)
        assertFalse(state.shouldResume)
        assertFalse(state.shouldAnimate)
    }

    private fun runningState() = AgentEmotionPlaybackState().apply {
        attach(isVisible = true)
        setForeground(true)
        pageFinished()
        assertTrue(shouldAnimate)
    }
}
