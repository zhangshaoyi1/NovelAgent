"""文体卫生门禁测试（2026-09-12 书级质检）

用例直接取自两本成书被读者点名的生成残留实锤，回归锁死：
- 五灵破 ch004 残缺比喻「跟……似的」
- 五灵破 ch050 填充词「就这么着」
- 无灵 ch140 成语误用「名不传虚」+ 破折号劈词「他手——里」
- 无灵 ch240 短语复读「骨骼碎裂的声音」
- 无灵 ch350 对话轮次断裂（提问 → 零回应 → 「你说得对」）
"""

from agent.core.quality.text_hygiene import hygiene_issues, split_issues


def _ids(issues):
    return [i["rule_id"] for i in issues]


def test_residual_simile_blocking():
    text = "那钟声浑厚苍凉，跟……似的从地底深处传来，震得每个人胸腔发麻。" * 20
    issues = hygiene_issues(text)
    assert "residual_simile" in _ids(issues)
    blocking, _ = split_issues(issues)
    assert any(i["rule_id"] == "residual_simile" for i in blocking)


def test_clean_simile_not_flagged():
    text = "那钟声浑厚苍凉，跟闷雷似的从地底深处传来，震得每个人胸腔发麻。" * 20
    assert "residual_simile" not in _ids(hygiene_issues(text))


def test_idiom_misuse_blocking():
    text = "万魔殿的『蚀魂印』，果然名不传虚。" + "正文填充。" * 60
    issues = hygiene_issues(text)
    blocking, _ = split_issues(issues)
    assert any(
        i["rule_id"] == "idiom_misuse" and "名不虚传" in i["description"]
        for i in blocking
    )


def test_correct_idiom_not_flagged():
    text = "万魔殿的『蚀魂印』，果然名不虚传。" + "正文填充。" * 60
    assert "idiom_misuse" not in _ids(hygiene_issues(text))


def test_dash_split_word_warning_not_blocking():
    text = "他手——里挥舞着一柄铁剑，剑尖指着林凡。" + "正文填充。" * 60
    blocking, warning = split_issues(hygiene_issues(text))
    assert "dash_split_word" in _ids(warning)
    assert "dash_split_word" not in _ids(blocking)


def test_filler_phrase_warning():
    text = "就这么着，山壁被凿出一个巨大的拱形洞口。" + "正文填充。" * 60
    _, warning = split_issues(hygiene_issues(text))
    assert "filler_phrase" in _ids(warning)


def test_exclamation_density_blocking():
    text = "痛！无法形容！万蚁噬骨！他嘶吼！血在烧！骨在响！魂在颤！" * 30
    issues = hygiene_issues(text)
    blocking, _ = split_issues(issues)
    assert any(i["rule_id"] == "exclamation_density" for i in blocking)


def test_simile_density_blocking():
    text = "力量像是潮水，如同海啸，仿佛雷鸣，宛如烈火，好似狂风，犹如巨浪。" * 20
    issues = hygiene_issues(text)
    blocking, _ = split_issues(issues)
    assert any(i["rule_id"] == "simile_density" for i in blocking)


def test_phrase_echo_warning():
    text = (
        "赵凌风猛地喷出一口鲜血，骨骼碎裂的声音再次响起，他摔在烂泥里。"
        "陆渊一拳轰出，骨骼碎裂的声音再次响起，监工倒飞出去。"
        "最后一拳落下，骨骼碎裂的声音再次响起，矿渊陷入死寂。"
        + "正文填充。" * 40
    )
    issues = hygiene_issues(text)
    _, warning = split_issues(issues)
    echo = [i for i in warning if i["rule_id"] == "phrase_echo"]
    assert echo and all("（≥3 次）" in i["description"] for i in echo)


def test_dialogue_turn_gap_warning():
    text = (
        "铁山走到他面前，问道：「我们该当如何？」\n\n"
        "铁山拍了拍陆渊的肩膀：「你说得对，就按你说的办。」\n\n"
    ) + "正文填充。" * 60
    _, warning = split_issues(hygiene_issues(text))
    assert "dialogue_turn_gap" in _ids(warning)


def test_clean_text_passes():
    paragraphs = [
        "林凡蹲下身，指尖拂过青苔，晨光越过山脊，把影子拉得很长。",
        "他把石头按进土里，五道细若游丝的光线渗出来，交织成一幅图谱。",
        "他咬紧牙关，神识沉入纹路，额角渗出细汗，太阳穴突突直跳。",
        "半柱香之后，那些阵法的走向终于清晰起来，像有人刻进了记忆深处。",
        "他站起身，拍掉膝盖上的泥，往灵植园深处的矮墙走去。",
        "园子里静悄悄，只有风掠过篱笆的轻响和几声断续的虫鸣。",
        "远处传来挑水人的吆喝，檐下的麻雀被惊得扑棱棱飞起来。",
        "他把昨晚想好的引灵路线默走了一遍，确认每一个节点都没有遗漏。",
        "日头爬到头顶时，四号畦地的虫害总算清理完毕。",
        "王胖子在园门探了下头，见他在忙活，撇撇嘴又缩了回去。",
        "午饭是两个杂面馍，他就着凉白开慢慢咽了下去，胃里有了点暖意。",
        "歇了半刻钟，他重新蹲回畦地边上，检查每一株灵谷的叶脉。",
        "叶背藏着几粒虫卵，他用指甲一一掐破，指腹染上一层青色的浆。",
        "傍晚的雾气从山坳里漫上来，把整片园子裹进灰白的纱帐。",
        "收工前，他把工具一件件归位，锄头挂在老地方，木柄磨得发亮。",
        "回茅屋的路上，他拐去后厨领了明天份的粗盐，顺手帮老刘扛了柴。",
        "夜里他盘膝坐在草席上，试着让那丝微薄灵气沿任脉走了一个周天。",
        "经脉里的滞涩感比昨夜轻了些，他睁开眼，眼里映着豆大的灯火。",
        "灯火忽然晃了一下，窗外没有风，他盯着窗纸看了半晌，没有动。",
        "三更梆子响过，山里彻底静了，他吹熄油灯，和衣躺下。",
    ]
    text = "".join(paragraphs)
    blocking, _ = split_issues(hygiene_issues(text))
    assert blocking == []


def test_empty_and_short_safe():
    assert hygiene_issues("") == []
    assert split_issues(hygiene_issues("短文本")) == ([], [])
