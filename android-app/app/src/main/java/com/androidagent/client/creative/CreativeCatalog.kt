package com.androidagent.client.creative

object CreativeCatalog {
    val recipes: List<CreativeRecipe> = listOf(
        CreativeRecipe(
            id = "pulse-cta",
            title = "呼吸主按钮",
            summary = "用轻微缩放吸引注意，适合关键行动入口",
            category = CreativeCategory.MOTION,
            preview = CreativePreview.PULSE_BUTTON,
            tags = setOf("按钮", "呼吸", "转化"),
            source = """
                @Composable
                fun PulseCta(text: String, onClick: () -> Unit) {
                    val transition = rememberInfiniteTransition(label = "pulse")
                    val scale by transition.animateFloat(
                        initialValue = 0.97f,
                        targetValue = 1.03f,
                        animationSpec = infiniteRepeatable(
                            animation = tween(900, easing = EaseInOut),
                            repeatMode = RepeatMode.Reverse,
                        ),
                        label = "scale",
                    )
                    Button(
                        onClick = onClick,
                        modifier = Modifier.graphicsLayer {
                            scaleX = scale
                            scaleY = scale
                        },
                    ) { Text(text) }
                }
            """.trimIndent(),
        ),
        CreativeRecipe(
            id = "expandable-insight",
            title = "展开数据卡",
            summary = "点击后平滑展开详情，适合指标和摘要信息",
            category = CreativeCategory.DATA,
            preview = CreativePreview.EXPANDABLE_CARD,
            tags = setOf("卡片", "展开", "数据"),
            source = """
                @Composable
                fun ExpandableInsight(title: String, value: String, detail: String) {
                    var expanded by rememberSaveable { mutableStateOf(false) }
                    ElevatedCard(
                        onClick = { expanded = !expanded },
                        modifier = Modifier.fillMaxWidth().animateContentSize(),
                    ) {
                        Column(Modifier.padding(16.dp)) {
                            Text(title, style = MaterialTheme.typography.labelLarge)
                            Text(value, style = MaterialTheme.typography.headlineMedium)
                            AnimatedVisibility(expanded) {
                                Text(detail, Modifier.padding(top = 8.dp))
                            }
                        }
                    }
                }
            """.trimIndent(),
        ),
        CreativeRecipe(
            id = "favorite-pop",
            title = "收藏弹跳",
            summary = "收藏状态切换时加入弹性反馈",
            category = CreativeCategory.FEEDBACK,
            preview = CreativePreview.FAVORITE_TOGGLE,
            tags = setOf("收藏", "弹性", "反馈"),
            source = """
                @Composable
                fun FavoriteToggle(checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
                    val scale by animateFloatAsState(
                        targetValue = if (checked) 1.22f else 1f,
                        animationSpec = spring(dampingRatio = Spring.DampingRatioMediumBouncy),
                        label = "favorite-scale",
                    )
                    IconButton(onClick = { onCheckedChange(!checked) }) {
                        Text(
                            text = if (checked) "♥" else "♡",
                            color = if (checked) MaterialTheme.colorScheme.error else LocalContentColor.current,
                            fontSize = 34.sp,
                            modifier = Modifier.graphicsLayer { scaleX = scale; scaleY = scale },
                        )
                    }
                }
            """.trimIndent(),
        ),
        CreativeRecipe(
            id = "animated-counter",
            title = "滚动数字",
            summary = "数字变化时上下滚动，适合计数器和统计值",
            category = CreativeCategory.DATA,
            preview = CreativePreview.ANIMATED_COUNTER,
            tags = setOf("数字", "统计", "转场"),
            source = """
                @Composable
                fun AnimatedCounter(count: Int) {
                    AnimatedContent(
                        targetState = count,
                        transitionSpec = {
                            if (targetState > initialState) {
                                slideInVertically { it } + fadeIn() togetherWith
                                    slideOutVertically { -it } + fadeOut()
                            } else {
                                slideInVertically { -it } + fadeIn() togetherWith
                                    slideOutVertically { it } + fadeOut()
                            }
                        },
                        label = "counter",
                    ) { value ->
                        Text(value.toString(), style = MaterialTheme.typography.displaySmall)
                    }
                }
            """.trimIndent(),
        ),
        CreativeRecipe(
            id = "loading-dots",
            title = "波浪加载点",
            summary = "三个点依次起伏，适合短时等待状态",
            category = CreativeCategory.FEEDBACK,
            preview = CreativePreview.LOADING_DOTS,
            tags = setOf("加载", "循环", "等待"),
            source = """
                @Composable
                fun LoadingDots() {
                    val transition = rememberInfiniteTransition(label = "loading")
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        repeat(3) { index ->
                            val offset by transition.animateFloat(
                                initialValue = 0f,
                                targetValue = -10f,
                                animationSpec = infiniteRepeatable(
                                    tween(450, delayMillis = index * 120),
                                    RepeatMode.Reverse,
                                ),
                                label = "dot-${'$'}index",
                            )
                            Box(
                                Modifier.size(9.dp)
                                    .offset(y = offset.dp)
                                    .background(MaterialTheme.colorScheme.primary, CircleShape),
                            )
                        }
                    }
                }
            """.trimIndent(),
        ),
        CreativeRecipe(
            id = "progress-reveal",
            title = "进度揭示卡",
            summary = "进入页面后从零增长，突出目标完成度",
            category = CreativeCategory.COMPONENT,
            preview = CreativePreview.PROGRESS_REVEAL,
            tags = setOf("进度", "卡片", "进入动画"),
            source = """
                @Composable
                fun ProgressReveal(progress: Float, label: String) {
                    var started by remember { mutableStateOf(false) }
                    LaunchedEffect(Unit) { started = true }
                    val animated by animateFloatAsState(
                        if (started) progress else 0f,
                        animationSpec = tween(900, easing = FastOutSlowInEasing),
                        label = "progress",
                    )
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween) {
                            Text(label)
                            Text("${'$'}{(animated * 100).roundToInt()}%")
                        }
                        LinearProgressIndicator(
                            progress = { animated },
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
            """.trimIndent(),
        ),
    )

    fun find(id: String): CreativeRecipe? = recipes.firstOrNull { it.id == id }
}
