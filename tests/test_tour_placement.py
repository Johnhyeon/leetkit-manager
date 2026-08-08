"""가이드 설명 상자가 "가리키는 대상"을 덮지 않는지.

실사용에서 지적된 문제다 — 기본 창(1180x820)에서 "동작 버튼" 단계의 상자가 설명하려던
버튼 줄을 69% 덮었다. 가리키면서 가리는 셈이라 제일 나쁜 모양인데, 그때까지의 검증은
"화면 밖으로 나갔는지"만 봤지 "대상을 덮는지"는 안 봐서 못 잡았다.

placeTooltip은 JS라 여기서는 같은 규칙을 파이썬으로 옮겨 검사한다. 화면 크기·상자
높이 조합을 훑어 "화면 안 + 대상 안 덮음"을 만족하는 자리가 항상 나오는지 본다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

GAP = 12
EDGE = 10


def place_tooltip(rect, tw, th, pad, win_w, win_h):
    """leetkit_manager/ui/app.js의 placeTooltip과 같은 규칙."""
    left_r, top_r, right_r, bottom_r = rect
    max_left = win_w - tw - EDGE
    max_top = win_h - th - EDGE
    clamp = lambda v, lo, hi: max(lo, min(v, hi))  # noqa: E731

    candidates = [
        {"top": bottom_r + pad + GAP, "left": clamp(left_r, EDGE, max_left)},
        {"top": top_r - pad - GAP - th, "left": clamp(left_r, EDGE, max_left)},
        {"left": right_r + pad + GAP, "top": clamp(top_r, EDGE, max_top)},
        {"left": left_r - pad - GAP - tw, "top": clamp(top_r, EDGE, max_top)},
    ]

    def on_screen(c):
        return (c["top"] >= EDGE and c["left"] >= EDGE
                and c["top"] + th <= win_h - EDGE and c["left"] + tw <= win_w - EDGE)

    def covered(c):
        ox = max(0, min(c["left"] + tw, right_r + pad) - max(c["left"], left_r - pad))
        oy = max(0, min(c["top"] + th, bottom_r + pad) - max(c["top"], top_r - pad))
        return ox * oy

    for c in candidates:
        if on_screen(c) and covered(c) == 0:
            return c, covered(c)
    fallback = sorted(
        ({"top": clamp(c["top"], EDGE, max(EDGE, max_top)),
          "left": clamp(c["left"], EDGE, max(EDGE, max_left))} for c in candidates),
        key=covered,
    )
    return fallback[0], covered(fallback[0])


def _clear_spot_exists(rect, tw, th, pad, win_w, win_h):
    """화면 안에 들어가면서 대상을 안 덮는 자리가 하나라도 있는지."""
    left_r, top_r, right_r, bottom_r = rect
    max_left = win_w - tw - EDGE
    max_top = win_h - th - EDGE
    clamp = lambda v, lo, hi: max(lo, min(v, hi))  # noqa: E731
    for c in (
        {"top": bottom_r + pad + GAP, "left": clamp(left_r, EDGE, max_left)},
        {"top": top_r - pad - GAP - th, "left": clamp(left_r, EDGE, max_left)},
        {"left": right_r + pad + GAP, "top": clamp(top_r, EDGE, max_top)},
        {"left": left_r - pad - GAP - tw, "top": clamp(top_r, EDGE, max_top)},
    ):
        if not (c["top"] >= EDGE and c["left"] >= EDGE
                and c["top"] + th <= win_h - EDGE and c["left"] + tw <= win_w - EDGE):
            continue
        ox = max(0, min(c["left"] + tw, right_r + pad) - max(c["left"], left_r - pad))
        oy = max(0, min(c["top"] + th, bottom_r + pad) - max(c["top"], top_r - pad))
        if ox * oy == 0:
            return True
    return False


class TestPlacement:
    def test_the_reported_case_no_longer_covers_the_target(self):
        """실제로 신고된 상황 — 기본 창, 카드 아래쪽 버튼 줄, 494px짜리 설명."""
        rect = (41, 312, 564, 351)  # 동작 버튼 줄
        spot, covered = place_tooltip(rect, tw=360, th=494, pad=6, win_w=1180, win_h=820)
        assert covered == 0, f"대상을 {covered}px² 덮는다 — {spot}"

    @pytest.mark.parametrize("win", [(1180, 820), (1040, 700), (1280, 1000), (1400, 760), (1920, 1080)])
    @pytest.mark.parametrize("th", [140, 265, 392, 494, 600])
    def test_takes_a_clear_spot_whenever_one_exists(self, win, th):
        """덮지 않는 자리가 하나라도 있으면 반드시 그걸 고른다.

        "언제나 안 덮는다"로는 못 쓴다 — 대상이 화면을 가로지르고 설명도 화면만큼
        길면 그런 자리가 아예 없다(600px 상자를 700px 창에 넣는 경우). 그건 아래
        test_impossible_case_picks_the_least_bad_spot이 따로 본다.
        """
        win_w, win_h = win
        rects = [
            (22, 62, win_w - 22, 98),                      # 판독줄(가로로 넓다)
            (41, 312, min(564, win_w // 2), 351),          # 왼쪽 카드 버튼 줄
            (41, 150, 75, 184),                            # 상태 표시등(작다)
            (win_w - 120, 20, win_w - 20, 56),             # 오른쪽 상단 버튼
        ]
        for rect in rects:
            spot, covered = place_tooltip(rect, tw=360, th=th, pad=6, win_w=win_w, win_h=win_h)
            if _clear_spot_exists(rect, 360, th, 6, win_w, win_h):
                assert covered == 0, f"{win} th={th} rect={rect} → 빈 자리가 있는데 {covered}px² 가림 ({spot})"

    def test_the_real_steps_always_get_a_clear_spot(self):
        """실제로 쓰는 설명 높이(최대 494px)와 실제 창 크기에서는 늘 빈 자리가 있다."""
        for win_w, win_h in [(1180, 820), (1040, 700), (1280, 1000)]:
            for th in (140, 188, 265, 290, 392, 494):
                rect = (41, 312, min(564, win_w // 2), 351)
                assert _clear_spot_exists(rect, 360, th, 6, win_w, win_h), f"{win_w}x{win_h} th={th}"

    def test_impossible_case_picks_the_least_bad_spot(self):
        """대상이 화면을 거의 다 차지하고 설명도 화면만큼 길면 어디에도 안 들어간다.

        그때는 "덮지 않는 자리"가 존재하지 않는다 — 아무것도 안 하고 대상 위에 얹는
        대신 가장 덜 가리는 자리를 고르고, 화면 안에는 반드시 넣는다.
        """
        rect = (22, 62, 1018, 640)  # 1040x700 창을 거의 채우는 대상
        spot, covered = place_tooltip(rect, tw=360, th=600, pad=6, win_w=1040, win_h=700)
        assert spot["left"] >= EDGE and spot["top"] >= EDGE
        assert spot["left"] + 360 <= 1040 - EDGE + 1
        # 네 후보 중 최소 가림이어야 한다
        others = []
        for cand in (
            {"top": 640 + 6 + GAP, "left": 22},
            {"top": 62 - 6 - GAP - 600, "left": 22},
            {"left": 1018 + 6 + GAP, "top": 62},
            {"left": 22 - 6 - GAP - 360, "top": 62},
        ):
            c = {"top": max(EDGE, min(cand["top"], 700 - 600 - EDGE)),
                 "left": max(EDGE, min(cand["left"], 1040 - 360 - EDGE))}
            ox = max(0, min(c["left"] + 360, 1018 + 6) - max(c["left"], 22 - 6))
            oy = max(0, min(c["top"] + 600, 640 + 6) - max(c["top"], 62 - 6))
            others.append(ox * oy)
        assert covered == min(others), f"가장 덜 가리는 자리가 아니다 {covered} vs {sorted(others)}"

    def test_stays_on_screen(self):
        for th in (140, 494, 600):
            spot, _ = place_tooltip((41, 312, 564, 351), 360, th, 6, 1040, 700)
            assert spot["left"] >= EDGE and spot["top"] >= EDGE
            assert spot["left"] + 360 <= 1040 - EDGE + 1


def test_js_and_python_rules_stay_in_sync():
    """이 테스트가 검사하는 규칙이 실제 JS와 같은지 — 상수가 갈리면 여기서 알려준다."""
    js = Path("leetkit_manager/ui/app.js").read_text(encoding="utf-8")
    body = js[js.index("function placeTooltip("):js.index("function positionTour(")]
    assert re.search(rf"const gap = {GAP};", body), "JS의 gap이 바뀌었다"
    assert re.search(rf"const edge = {EDGE};", body), "JS의 edge가 바뀌었다"
    # 네 방향을 다 보는지
    assert body.count("{ top:") + body.count("{ left:") >= 4, "후보 자리가 4개보다 적다"
