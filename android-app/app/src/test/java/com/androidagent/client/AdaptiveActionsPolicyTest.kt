package com.androidagent.client

import org.junit.Assert.*
import org.junit.Test

class AdaptiveActionsPolicyTest {
    @Test fun roomyActionsStayHorizontal() {
        assertFalse(AdaptiveActionsPolicy.shouldStack(320, listOf(110, 120)))
    }
    @Test fun longestLabelFitsItsWeightedSlot() {
        assertTrue(AdaptiveActionsPolicy.shouldStack(300, listOf(70, 190)))
    }
    @Test fun largeFontOrNarrowScreenStacksWithoutDroppingActions() {
        assertTrue(AdaptiveActionsPolicy.shouldStack(280, listOf(160, 160)))
        assertFalse(AdaptiveActionsPolicy.shouldStack(0, emptyList()))
    }
}
