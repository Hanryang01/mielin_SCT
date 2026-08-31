"""OCR 텍스트와 검수자 타이핑 텍스트의 글자 단위 차이 계산.

OCR 검수 시나리오.md §5.3 — 검수자가 대괄호를 직접 입력하는 게 아니라,
제출된 typed_text를 그 시점의 OCR 텍스트와 비교해서 서버가 자동으로 계산한다.
서버가 계산하는 이유는 두 가지다:

- 저장되는 값이 클라이언트가 보내온 것에 좌우되면, 화면 버그나 조작으로
  "차이 없음"이 쌓여도 알아챌 방법이 없다. diff는 분석의 근거 데이터라
  (§6) 서버가 단일 소스여야 한다.
- ocr_text_snapshot을 같이 저장하므로 나중에 언제든 재계산·검증이 가능하다.

화면(app/static/js/text-diff.js)에도 같은 로직이 있지만 그건 입력 중 실시간
하이라이트용이다. 저장되는 값은 항상 이 모듈이 만든 것이다. 두 구현의
세그먼트 경계가 드물게 다를 수 있는데(아래 SequenceMatcher 주석 참고),
화면은 미리보기이고 저장값은 이쪽이라 문제가 되지 않는다.
"""

from __future__ import annotations

import difflib
from typing import Any

# 이 길이를 넘으면 글자 단위 비교를 포기하고 "전체가 다름"으로 처리한다.
# SequenceMatcher는 최악의 경우 O(n*m)이라, SCT 답변으로는 나올 수 없는
# 길이(OCR 오류로 같은 문자가 수천 번 반복되는 등)가 들어오면 요청이 통째로
# 느려진다. 실제 답변은 대부분 수십 글자다.
MAX_DIFF_LENGTH = 2000


def _compact(text: str) -> tuple[str, list[int]]:
    """공백을 뺀 문자열과, 각 글자가 원문에서 몇 번째였는지의 인덱스 목록.

    비교는 이 압축본으로 하고, 화면 표기는 인덱스로 원문 위치를 되찾아 만든다.
    """
    chars: list[str] = []
    positions: list[int] = []
    for i, ch in enumerate(text):
        if not ch.isspace():
            chars.append(ch)
            positions.append(i)
    return "".join(chars), positions


def _spacing_differs(ocr: str, typed: str) -> bool:
    """띄어쓰기 패턴이 다른가 — 글자가 같은지와는 별개로 판단한다.

    "몇 번째 글자 뒤에 공백이 오는가"를 압축본 기준으로 비교한다. 원문 위치로
    비교하면 앞쪽 글자 수가 다를 때 전부 다르다고 나오므로 의미가 없다.
    """

    def signature(text: str) -> set[int]:
        seen = 0
        marks: set[int] = set()
        for ch in text:
            if ch.isspace():
                marks.add(seen)
            else:
                seen += 1
        return marks

    return signature(ocr) != signature(typed)


def compute_diff(ocr_text: str | None, typed_text: str | None) -> dict[str, Any]:
    """OCR 텍스트 대비 타이핑 텍스트의 차이를 계산한다.

    반환값:
        segments      : 차이 구간 목록. op는 replace(오인식) / delete(OCR에만
                        있는 글자) / insert(OCR이 빠뜨린 글자).
        char_count    : 차이 **글자** 수 (필터·정렬용 요약값). 0이면 글자는 일치.
        spacing_diff  : 띄어쓰기 패턴이 다른가 (글자 일치 여부와 별개).
        marked        : 검수자가 제시한 대괄호 표기 문자열 (화면·내보내기용).

    **공백은 글자 비교에서 제외한다** (2026-08-24). OCR의 띄어쓰기 오류는
    글자 오인식과 성격이 다르고, 검수자마다 띄어쓰기 습관이 달라 같은 판단인데
    다른 결과가 나오기 때문이다 — 실제로 한 답변을 두 검수자가 각각
    "있어야한다" / "있어야 한다"로 적어 둘 다 "OCR이 틀렸다"로 집계된 적이
    있다. 다만 띄어쓰기도 OCR 품질의 일부라 아예 버리지 않고 spacing_diff로
    따로 남긴다 — 그래야 "OCR이 띄어쓰기를 얼마나 틀리는가"도 나중에 볼 수 있다.

    delete는 타이핑 쪽에 대응하는 글자가 없으므로 marked에는 OCR에만 있던
    글자를 대괄호로 감싸 보여준다 — 그래야 "사람[대] 매너가..."처럼 무엇이
    빠졌는지 읽을 수 있다.
    """
    ocr = ocr_text or ""
    typed = typed_text or ""

    spacing_diff = _spacing_differs(ocr, typed)
    ocr_c, _ = _compact(ocr)
    typed_c, typed_pos = _compact(typed)

    if ocr_c == typed_c:
        # 글자는 완전히 같다. 띄어쓰기만 다를 수 있는데, 그건 char_count가 아니라
        # spacing_diff로 알린다 (§5.5의 "diff 0이면 패스로 재분류"도 그대로 적용).
        return {
            "segments": [],
            "char_count": 0,
            "spacing_diff": spacing_diff,
            "marked": typed,
        }

    if len(ocr_c) > MAX_DIFF_LENGTH or len(typed_c) > MAX_DIFF_LENGTH:
        # 글자 단위로 쪼개지 않고 통째로 하나의 replace로 기록한다.
        # 정밀도를 잃지만, 이 길이는 정상적인 SCT 답변이 아니다.
        return {
            "segments": [
                {
                    "op": "replace",
                    "ocr": ocr,
                    "typed": typed,
                    "ocr_pos": 0,
                    "typed_pos": 0,
                    "truncated": True,
                }
            ],
            "char_count": max(len(ocr_c), len(typed_c)),
            "spacing_diff": spacing_diff,
            "marked": f"[{typed}]",
        }

    # autojunk=False가 중요하다. 기본값(True)이면 SequenceMatcher가 200자
    # 이상 시퀀스에서 자주 등장하는 글자를 "junk"로 보고 매칭에서 빼버린다.
    # 한글 답변에서는 흔한 조사·어미가 그렇게 취급되어, 실제로는 한 글자만
    # 다른데 문장 전체가 다르다고 나오는 경우가 생긴다.
    matcher = difflib.SequenceMatcher(None, ocr_c, typed_c, autojunk=False)

    segments: list[dict[str, Any]] = []
    marked_parts: list[str] = []
    char_count = 0

    # 압축본 인덱스 -> 원문 typed의 슬라이스 경계. 공백을 건너뛰었으므로
    # 되돌릴 때는 "그 글자가 원문에서 있던 자리"를 써야 한다.
    def typed_slice(j1: int, j2: int) -> tuple[int, int]:
        if j1 >= j2:
            start = typed_pos[j1] if j1 < len(typed_pos) else len(typed)
            return start, start
        return typed_pos[j1], typed_pos[j2 - 1] + 1

    cursor = 0
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        start, end = typed_slice(j1, j2)
        if op == "equal":
            # 원문 그대로(공백 포함) 이어 붙인다 — 표기에서 띄어쓰기가 사라지면
            # 검수자가 자기가 입력한 문장을 알아보기 어렵다.
            marked_parts.append(typed[cursor:end])
            cursor = end
            continue

        segments.append(
            {
                "op": op,
                "ocr": ocr_c[i1:i2],
                "typed": typed_c[j1:j2],
                "ocr_pos": i1,
                "typed_pos": start,
            }
        )
        # delete는 타이핑 쪽이 비어 있으니 OCR에만 있던 글자를 보여준다.
        if op == "delete":
            shown = ocr_c[i1:i2]
        else:
            marked_parts.append(typed[cursor:start])
            shown = typed[start:end]
            cursor = end
        marked_parts.append(f"[{shown}]")
        char_count += max(i2 - i1, j2 - j1)

    if cursor < len(typed):
        marked_parts.append(typed[cursor:])

    return {
        "segments": segments,
        "char_count": char_count,
        "spacing_diff": spacing_diff,
        "marked": "".join(marked_parts),
    }


def render_marked(typed_text: str | None, segments: list[dict[str, Any]] | None) -> str:
    """저장된 세그먼트로 대괄호 표기 문자열을 되만든다.

    compute_diff를 다시 돌리지 않고 **저장된 값**에서 복원하는 이유는, 화면에
    보이는 표기가 DB에 실제로 들어있는 세그먼트와 항상 같아야 하기 때문이다.
    diff 알고리즘이 나중에 바뀌면 재계산 결과가 과거 저장값과 달라질 수 있는데,
    그때 화면이 저장값과 다른 걸 보여주면 분석 근거를 신뢰할 수 없게 된다.
    """
    typed = typed_text or ""
    if not segments:
        return typed

    def end_of_span(start: int, compact_len: int) -> int:
        """start부터 공백을 건너뛰며 compact_len개의 실제 글자를 지나간
        원문 위치를 돌려준다.

        세그먼트의 `typed`는 공백을 뺀 압축본이라(_compact 참고), 원문
        기준 길이가 `len(typed_part)`보다 길 수 있다 — 예를 들어 "애들 앞"의
        압축본은 "애들앞"(3자)이지만 원문에서는 공백까지 포함해 4자를
        차지한다. 예전에는 `pos + len(typed_part)`로 커서를 옮겼는데, 압축본
        길이만큼만 옮기면 공백 개수만큼 못 미쳐서 다음 글자가 대괄호
        안팎에 중복 표시됐다(2026-08-28 발견, 예: "[애들앞]앞에서").
        """
        i = start
        consumed = 0
        n = len(typed)
        while i < n and consumed < compact_len:
            if not typed[i].isspace():
                consumed += 1
            i += 1
        return i

    parts: list[str] = []
    cursor = 0
    for seg in sorted(segments, key=lambda s: s.get("typed_pos", 0)):
        pos = seg.get("typed_pos", 0)
        if pos > cursor:
            parts.append(typed[cursor:pos])
        typed_part = seg.get("typed") or ""
        shown = (seg.get("ocr") or "") if seg.get("op") == "delete" else typed_part
        parts.append(f"[{shown}]")
        cursor = end_of_span(pos, len(typed_part))

    if cursor < len(typed):
        parts.append(typed[cursor:])
    return "".join(parts)
