package com.androidagent.client

import com.androidagent.client.creative.CreativeCatalog
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CreativeCatalogTest {

    @Test
    fun recipeIdsAreUniqueAndSourcesAreCopyable() {
        val recipes = CreativeCatalog.recipes
        assertTrue(recipes.size >= 6)
        assertEquals(recipes.size, recipes.map { it.id }.toSet().size)
        recipes.forEach { recipe ->
            assertTrue(recipe.id.isNotBlank())
            assertTrue(recipe.title.isNotBlank())
            assertTrue(recipe.source.contains("@Composable"))
            assertTrue(recipe.minSdk >= 24)
        }
    }

    @Test
    fun agentPromptCarriesIntegrationAndBuildRequirements() {
        val recipe = CreativeCatalog.recipes.first()
        val prompt = recipe.buildAgentPrompt()
        assertTrue(prompt.contains(recipe.id))
        assertTrue(prompt.contains(recipe.title))
        assertTrue(prompt.contains(recipe.source))
        assertTrue(prompt.contains("assembleDebug"))
        assertTrue(prompt.contains("Compose 还是 XML/View"))
    }
}

