"""app.text_diff.compute_diff — DB 없이 검증 가능한 핵심 도메인 로직."""

from __future__ import annotations

from app.text_diff import compute_diff, render_marked


def test_identical_text_has_no_diff():
    result = compute_diff("사과", "사과")
    assert result["char_count"] == 0
    assert result["segments"] == []
    assert result["spacing_diff"] is False


def test_spacing_only_difference_is_not_counted_as_char_diff():
    result = compute_diff("있어야한다", "있어야 한다")
    assert result["char_count"] == 0
    assert result["spacing_diff"] is True


def test_single_char_substitution_is_detected():
    result = compute_diff("사과", "사고")
    assert result["char_count"] == 1
    assert result["segments"][0]["op"] == "replace"
    assert "[" in result["marked"]


def test_missing_char_in_typed_is_delete_segment():
    result = compute_diff("사람다움", "사람움")
    assert result["char_count"] >= 1
    assert any(seg["op"] == "delete" for seg in result["segments"])


def test_none_inputs_are_treated_as_empty_strings():
    result = compute_diff(None, None)
    assert result["char_count"] == 0
    assert result["marked"] == ""


def test_oversized_input_falls_back_to_single_replace_segment():
    long_text = "가" * 2001
    result = compute_diff(long_text, "다른내용")
    assert len(result["segments"]) == 1
    assert result["segments"][0]["truncated"] is True


def test_render_marked_reconstructs_bracket_notation_from_segments():
    original = compute_diff("사과", "사고")
    rebuilt = render_marked("사고", original["segments"])
    assert rebuilt == original["marked"]
