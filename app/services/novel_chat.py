NOVEL_CHAT_SKILL_RECOMMENDATION_RULES: list[tuple[str, tuple[str, ...], str]] = [
    (
        "chapter_rewrite",
        (
            "改写",
            "润色",
            "重写",
            "优化文案",
            "替换正文",
            "修改",
            "更改",
            "帮我改",
            "帮我修改",
            "帮我更改",
            "按建议改",
            "按照建议改",
            "按你的建议改",
            "按照你的建议改",
            "直接改",
            "直接修改",
        ),
        "检测到你关注具体文本修改，已匹配到「章节改写」。",
    ),
    (
        "chapter_eval",
        ("评估", "打分", "诊断", "问题", "哪里不好", "不合理"),
        "检测到你关注质量诊断，已匹配到「章节评估」。",
    ),
    (
        "character_insight",
        ("人物", "角色", "动机", "关系", "成长线"),
        "检测到你关注人物塑造，已匹配到「人物分析」。",
    ),
    (
        "platform_advice",
        ("平台", "发布", "受众", "商业化", "投放", "标题包装"),
        "检测到你关注发布策略，已匹配到「平台建议」。",
    ),
    (
        "story_overview",
        ("全书", "整体", "结构", "节奏", "主线", "梳理", "分集"),
        "检测到你关注全局结构，已匹配到「全书梳理」。",
    ),
]


def recommend_chat_skill(message: str) -> tuple[str | None, str | None]:
    text = (message or "").strip().lower()
    if not text:
        return None, None

    for skill, keywords, reason in NOVEL_CHAT_SKILL_RECOMMENDATION_RULES:
        if any(keyword in text for keyword in keywords):
            return skill, reason
    return None, None
