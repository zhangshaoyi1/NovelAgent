"""Web 弹窗「一闪而过」防护红线（2026-09-14）

背景：写作间底部按钮触发的弹窗被全屏遮罩盖住触发点，用户双击/连点/没点准时
第二次点击落在遮罩上，刚打开的弹窗被立刻关闭 → 模型下拉"一闪而过"没机会选。
该缺陷当日复发过一次（首版静默期 350ms 不足以覆盖 550ms 慢双击），故把不变量
固化为可检查约束——前端无单测覆盖，靠文本红线防回退。

约束：
- W1  遮罩关闭只能由 `click` 驱动（`mousedown` 会提前隐藏遮罩，导致 click 穿透到
      下层元素，可能误触发「写下一章」等按钮）
- W2  存在「时间静默期」且不低于系统双击间隔默认值 500ms
- W3  存在「位置守卫」：打开时记录触发点矩形，短时间内点回该矩形不算点背景
- W4  页内弹窗开关必须复用 app.js 统一实现（不得各写一套绕过防护）
- W5  模型下拉只在内容真变化且用户未操作时重建（重建 <option> 会强制收起下拉）
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "src" / "agent" / "web"
APP_JS = WEB / "static" / "app.js"
WRITER_HTML = WEB / "templates" / "writer.html"


def _read(p: Path) -> str:
    assert p.exists(), f"缺少前端资源：{p}"
    return p.read_text(encoding="utf-8")


def test_w1_overlay_dismiss_driven_by_click_not_mousedown() -> None:
    """W1：遮罩关闭判定必须是 click，且不得有 mousedown 驱动的遮罩关闭。"""
    src = _read(APP_JS)
    assert re.search(r"addEventListener\(\s*'click'", src), "app.js 缺少 click 遮罩关闭监听"
    # 禁止把遮罩关闭挂在 mousedown 上（会提前隐藏遮罩 → click 穿透下层元素）
    mousedown_blocks = re.findall(
        r"addEventListener\(\s*'mousedown'\s*,\s*\(e\)\s*=>\s*\{(.*?)\}\)", src, re.S
    )
    for block in mousedown_blocks:
        assert "closeAnyModal" not in block, (
            "遮罩关闭不得由 mousedown 驱动：会在 mouseup 前隐藏遮罩，"
            "使 click 穿透到下层按钮/链接"
        )


def test_w2_grace_window_above_os_double_click_interval() -> None:
    """W2：时间静默期必须高于系统双击间隔默认值（Windows 500ms），否则慢双击仍被误关。"""
    src = _read(APP_JS)
    m = re.search(r"MODAL_GRACE_MS\s*=\s*(\d+)", src)
    assert m, "app.js 缺少 MODAL_GRACE_MS 静默期常量"
    assert int(m.group(1)) >= 500, (
        f"MODAL_GRACE_MS={m.group(1)} 低于系统双击间隔默认 500ms，"
        "慢双击会再次把弹窗误关（实测 350ms 时 550ms 慢双击仍失败）"
    )


def test_w3_origin_rect_guard_present() -> None:
    """W3：位置守卫——打开时记录触发点矩形，短时间内点回原位置不算点背景。"""
    src = _read(APP_JS)
    assert "_modalOriginRect" in src, "app.js 缺少触发点矩形记录（位置守卫）"
    assert "ORIGIN_GUARD_MS" in src, "app.js 缺少位置守卫时间窗常量"
    # showAnyModal 内必须落记录，否则守卫拿不到锚点
    show = re.search(r"function showAnyModal\(el\)\s*\{(.*?)\n\}", src, re.S)
    assert show, "未找到 showAnyModal 实现"
    assert "_modalOriginRect" in show.group(1), "showAnyModal 未记录触发点矩形，位置守卫失效"
    assert "_modalOpenedAt" in show.group(1), "showAnyModal 未记录打开时刻，时间静默期失效"
    # click 处理器里必须真的消费这两道守卫
    dismiss = re.search(
        r"addEventListener\(\s*'click'\s*,\s*\(e\)\s*=>\s*\{(.*?)\n\}\)", src, re.S
    )
    assert dismiss, "未找到遮罩 click 关闭处理器"
    body = dismiss.group(1)
    assert "MODAL_GRACE_MS" in body, "遮罩关闭未消费时间静默期"
    assert "_hitOriginRect" in body, "遮罩关闭未消费位置守卫"


def test_w4_page_modal_helpers_reuse_shared_impl() -> None:
    """W4：页内弹窗开关必须复用 app.js 统一实现，不得各写一套绕过防护。"""
    src = _read(WRITER_HTML)
    m = re.search(r"function openWriterModal\(id\)\s*\{([^}]*)\}", src)
    assert m, "writer.html 缺少 openWriterModal"
    assert "showAnyModal" in m.group(1), (
        "openWriterModal 未复用 showAnyModal：会绕过静默期/位置守卫（历史事故成因）"
    )
    m2 = re.search(r"function closeWriterModal\(id\)\s*\{([^}]*)\}", src)
    assert m2, "writer.html 缺少 closeWriterModal"
    assert "closeAnyModal" in m2.group(1), "closeWriterModal 未复用 closeAnyModal"


def test_w5_model_options_not_rebuilt_unconditionally() -> None:
    """W5：模型下拉只在内容真变化且用户未操作时重建，否则会强制收起已展开的下拉。"""
    src = _read(WRITER_HTML)
    m = re.search(r"async function loadModelOptions\(\)\s*\{(.*?)\n  \}", src, re.S)
    assert m, "writer.html 缺少 loadModelOptions"
    body = m.group(1)
    assert "el.innerHTML === html" in body, (
        "loadModelOptions 缺少「内容无变化则跳过」短路：每次刷新都会重建 <option>，"
        "用户展开的下拉会被强制收起"
    )
    assert "activeElement" in body, (
        "loadModelOptions 缺少「用户正在操作该下拉则不重建」守卫"
    )
