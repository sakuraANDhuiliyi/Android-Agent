package com.androidagent.client.creative

object CreativeCatalog {
    const val MAX_RECIPES = 500

    // Each entry has its own composition and interaction; no style × layout permutations.
    val recipes: List<CreativeRecipe> = com.androidagent.client.creative.styles.CreativeStyleCatalog.recipes.also { catalog ->
        require(catalog.size <= MAX_RECIPES) { "创意广场最多收录 $MAX_RECIPES 个作品" }
        require(catalog.map { it.id }.distinct().size == catalog.size) { "创意 ID 不可重复" }
    }

    fun filter(
        recipes: List<CreativeRecipe> = this.recipes,
        query: String = "",
        category: CreativeCategory = CreativeCategory.ALL,
        styleId: String? = null,
    ): List<CreativeRecipe> {
        val terms = query.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
        return recipes.filter { recipe ->
            (category == CreativeCategory.ALL || recipe.category == category) &&
                (styleId == null || recipe.style?.id == styleId) &&
                terms.all { term ->
                    recipe.title.contains(term, ignoreCase = true) ||
                        recipe.summary.contains(term, ignoreCase = true) ||
                        recipe.tags.any { it.contains(term, ignoreCase = true) }
                }
        }
    }

    fun find(id: String): CreativeRecipe? = recipes.firstOrNull { it.id == id }
}
