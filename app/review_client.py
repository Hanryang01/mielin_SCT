from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

try:
    import pymysql
    from pymysql.cursors import DictCursor
    from pymysql.err import IntegrityError
except ModuleNotFoundError:  # pragma: no cover - depends on runtime environment
    pymysql = None
    DictCursor = None
    IntegrityError = Exception

# pymysql은 중복 키(1062)와 FK 위반(1452)을 똑같이 IntegrityError로 올린다.
# errno로 구분하지 않으면, 존재하지 않는 reviewer_id나 오타 난 classification
# 코드를 보냈을 때 "이미 의견을 남겼습니다"라는 엉뚱한 409가 나가서 디버깅이
# 크게 어긋난다.
ER_DUP_ENTRY = 1062
ER_NO_REFERENCED_ROW_2 = 1452

from .config import Settings
from .queries import ocr_review as queries
from .text_diff import compute_diff


def _key_of(item: dict[str, Any]) -> dict[str, int]:
    return {
        "assessment_id": item["assessment_id"],
        "drawing_id": item["drawing_id"],
        "answer_index": item["answer_index"],
    }


def _dump_diff(segments: list[dict[str, Any]] | None) -> str | None:
    """diff 세그먼트를 JSON 컬럼에 넣을 문자열로 만든다.

    ensure_ascii=False로 한글을 그대로 저장한다 — DB에서 직접 조회할 때
    (§6의 분석 쿼리) \\uXXXX 이스케이프가 보이면 읽을 수 없다.
    """
    if not segments:
        return None
    return json.dumps(segments, ensure_ascii=False)


def _load_diff(value: Any) -> list[dict[str, Any]]:
    """JSON 컬럼 값을 파이썬 리스트로 되돌린다.

    PyMySQL은 JSON 컬럼을 드라이버/서버 조합에 따라 str로 주기도 하고 이미
    파싱된 객체로 주기도 한다. 어느 쪽이 와도 화면에 같은 모양으로 내려가야
    하므로 여기서 흡수한다.
    """
    if value is None:
        return []
    if isinstance(value, (list, dict)):
        return value if isinstance(value, list) else [value]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


class DuplicateReviewError(Exception):
    """동일 검수자가 같은 답변에 이미 의견을 남긴 경우 (uq_review_per_reviewer)."""


class InvalidReferenceError(Exception):
    """reviewer_id가 실제로 없는 값인 경우 (FK 위반)."""


class ReviewNotFoundError(Exception):
    """수정 대상 의견이 존재하지 않는 경우."""


class NotOwnerError(Exception):
    """남이 남긴 의견을 수정하려는 경우 — §1의 원본 보존 원칙상 절대 허용 안 됨."""


class DuplicateAdminCommentError(Exception):
    """같은 admin이 같은 레코드에 이미 코멘트를 남긴 경우 (uq_admin_comment_per_admin)."""


class AdminCommentNotFoundError(Exception):
    """수정 대상 admin 코멘트가 존재하지 않는 경우."""


class ReviewDbClient:
    """OCR 검수 DB(ocr_review, 쓰기 가능) 전용 클라이언트.

    mielin(MysqlReader, SELECT 전용)과 완전히 분리된 별도 연결을 쓴다 —
    검수 의견은 INSERT가 필요하기 때문. REVIEW_MYSQL_*이 비어 있으면
    `enabled`가 False가 되고, 이 DB를 쓰는 라우트는 503으로 응답한다.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._connection: Any | None = None
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self.settings.review_mysql.configured

    def submit_review(
        self,
        *,
        assessment_id: int,
        drawing_id: int,
        answer_index: int,
        reviewer_id: int,
        review_type: str,
        vlm_model: str | None = None,
        typed_text: str | None = None,
        ocr_text_snapshot: str | None = None,
        ocr_diff: list[dict[str, Any]] | None = None,
        ocr_diff_char_count: int | None = None,
        spacing_diff: bool = False,
        ocr_difficulty_level: int | None = None,
        contains_negative_expression: bool = False,
        comment: str | None = None,
    ) -> int:
        params = {
            "assessment_id": assessment_id,
            "drawing_id": drawing_id,
            "answer_index": answer_index,
            "vlm_model": vlm_model,
            "reviewer_id": reviewer_id,
            "review_type": review_type,
            "typed_text": typed_text,
            "ocr_text_snapshot": ocr_text_snapshot,
            "ocr_diff": _dump_diff(ocr_diff),
            "ocr_diff_char_count": ocr_diff_char_count,
            "spacing_diff": 1 if spacing_diff else 0,
            "ocr_difficulty_level": ocr_difficulty_level,
            "contains_negative_expression": 1 if contains_negative_expression else 0,
            "comment": comment,
        }
        with self._lock:
            connection = self._get_connection()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(queries.INSERT_REVIEW, params)
                    return cursor.lastrowid
            except IntegrityError as exc:
                errno = exc.args[0] if exc.args else None
                if errno == ER_DUP_ENTRY:
                    raise DuplicateReviewError(
                        "이 검수자는 이미 이 답변에 의견을 남겼습니다"
                    ) from exc
                if errno == ER_NO_REFERENCED_ROW_2:
                    raise InvalidReferenceError(
                        "존재하지 않는 검수자입니다"
                    ) from exc
                raise

    def submit_passes_bulk(
        self, *, items: list[dict[str, Any]], reviewer_id: int
    ) -> list[dict[str, Any]]:
        """§4.2 일괄 패스 — 여러 건을 한 번의 요청으로 패스 처리한다.

        건별로 성공/중복/오류를 따로 담아 돌려준다. 한 건이 중복(이미 처리함)
        이라고 나머지를 되돌리면, 목록을 다시 그리는 사이 다른 검수자가 끼어든
        경우에 사용자가 아무것도 저장하지 못한다. 그래서 all-or-nothing으로
        묶지 않고 건별로 처리한다.

        커넥션 락은 한 번만 잡는다 — 건마다 잡으면 다른 요청과 번갈아 끼어들어
        일괄 처리의 이점이 사라진다.
        """
        outcomes: list[dict[str, Any]] = []
        with self._lock:
            connection = self._get_connection()
            for item in items:
                params = {
                    "assessment_id": item["assessment_id"],
                    "drawing_id": item["drawing_id"],
                    "answer_index": item["answer_index"],
                    "vlm_model": item.get("vlm_model"),
                    "reviewer_id": reviewer_id,
                    "review_type": "normal_check",
                    "typed_text": None,
                    # 패스는 타이핑이 없어 diff가 없지만, "무엇을 보고 맞다고
                    # 판단했는가"는 남겨둘 가치가 있어 스냅샷은 저장한다.
                    "ocr_text_snapshot": item.get("ocr_text_snapshot"),
                    "ocr_diff": None,
                    "ocr_diff_char_count": None,
                    # 패스는 비교 대상이 없다 — 띄어쓰기 차이도 판정하지 않는다.
                    "spacing_diff": 0,
                    "ocr_difficulty_level": item.get("ocr_difficulty_level"),
                    "contains_negative_expression": 1 if item.get("contains_negative_expression") else 0,
                    "comment": None,
                }
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(queries.INSERT_REVIEW, params)
                    outcomes.append({**_key_of(item), "result": "created"})
                except IntegrityError as exc:
                    errno = exc.args[0] if exc.args else None
                    if errno == ER_DUP_ENTRY:
                        outcomes.append({**_key_of(item), "result": "duplicate"})
                    elif errno == ER_NO_REFERENCED_ROW_2:
                        outcomes.append({**_key_of(item), "result": "invalid_reference"})
                    else:
                        raise
        return outcomes

    def update_review(
        self,
        *,
        review_id: int,
        reviewer_id: int,
        typed_text: str,
        ocr_difficulty_level: int,
        contains_negative_expression: bool,
        comment: str | None,
        resolve_ocr_text: Callable[[int, int, int], str | None] | None = None,
    ) -> dict[str, Any]:
        """§4.3 — 본인이 남긴 의견을 수정한다 (2026-08-21부터 패스도 대상).

        수정 직전 값을 ocr_review_edits에 먼저 남긴 뒤 UPDATE한다. 그래야 §1의
        "원래 독립 판단을 수정 없이 보존" 원칙이 지켜진다. 두 작업은 한 트랜잭션
        으로 묶는다 — 이력만 남고 본문이 안 바뀌거나 그 반대가 되면 안 된다.

        review_type은 저장된 값이 아니라 **매번 typed_text/ocr_difficulty_level
        에서 다시 계산**한다 — 패스였던 의견도 이제 난이도를 갖고 있어서, "원래
        뭐였는가"가 아니라 "지금 뭘 제출했는가"만으로 판단할 수 있다:
          - typed_text가 있고 OCR과 diff가 0이 아니면 → transcription
          - typed_text가 있지만 OCR과 글자 하나도 다르지 않으면(diff=0) →
            normal_check (패스) — §5.5(2026-08-21). 직접 다시 쳐서 확인했든
            패스 버튼을 눌렀든 결과("OCR이 맞다")는 같은데 review_type이
            다르면 §6 분석에서 "OCR이 틀렸다"로 잘못 집계된다. typed_text
            자체는 지우지 않고 그대로 남긴다.
          - 텍스트 없고 난이도가 5(판독 불가)면 → transcription
          - 텍스트 없고 1~4면 → normal_check (패스)
        난이도는 이제 항상 필수라 "둘 다 없음"으로 패스 전환을 유추하던 예전
        분기(§8 변경이력)는 없어졌다 — 대신 "텍스트 없음 + 1~4"가 그 자리를
        대신한다. contains_negative_expression은 전환 여부와 무관하게 검수자가
        마지막으로 남긴 값을 그대로 유지한다.

        diff(§5.3)는 수정된 typed_text와 **최초 저장된 ocr_text_snapshot**을
        다시 비교해서 계산한다 — 스냅샷은 갱신하지 않는다. 비교 대상 OCR
        텍스트는 처음 검수할 때 화면에 보였던 그 값이어야 하기 때문이다.

        반환값의 was_completed는 "완료(2/2) 상태에서의 수정"인지를 알려준다
        (호출부가 사용자에게 경고하거나 분석에서 걸러낼 수 있도록) — 상대
        검수자의 내용을 실제로 봤다는 뜻은 아니다(§4.4, 완료 후에도 상대
        내용은 공개되지 않는다).
        """
        typed_text = typed_text.strip()
        new_typed_text = typed_text or None
        new_difficulty = ocr_difficulty_level
        new_negative_flag = 1 if contains_negative_expression else 0

        with self._lock:
            connection = self._get_connection()
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(queries.SELECT_REVIEW_BY_ID, {"id": review_id})
                    row = cursor.fetchone()
                    if row is None:
                        raise ReviewNotFoundError("수정할 검수 의견을 찾을 수 없습니다")
                    if row["reviewer_id"] != reviewer_id:
                        raise NotOwnerError("본인이 남긴 의견만 수정할 수 있습니다")

                    # 완료(2/2) 여부를 수정 시점에 기록해둔다 — 상대 내용을
                    # 봤다는 뜻은 아니다(완료 후에도 공개되지 않는다)
                    cursor.execute(
                        queries.SELECT_STATUS_FOR_RECORD,
                        {
                            "assessment_id": row["assessment_id"],
                            "drawing_id": row["drawing_id"],
                            "answer_index": row["answer_index"],
                        },
                    )
                    status_row = cursor.fetchone()
                    was_completed = bool(status_row and status_row["status"] == "completed")

                    # 최초 검수 때 화면에 보였던 OCR 텍스트와 다시 비교한다.
                    # 텍스트가 없으면(패스가 됐거나, 판독 불가처럼 타이핑이어도
                    # 텍스트가 없는 경우) 비교 대상이 없다 — "전부 다르다"가
                    # 아니라 "비교 자체를 안 했다"는 뜻이어야 한다 (main.py의
                    # submit_review와 같은 이유).
                    # 스냅샷이 비어 있으면(2026-08-21 이전 데이터) 비교 대상이
                    # 없어 입력 전체가 "다르다"로 기록되는 문제가 있었다. 그럴
                    # 때만 호출부가 준 조회 함수로 원본 OCR을 가져와 기준으로
                    # 삼고, 그 값을 스냅샷에 채운다(빈 칸을 메우는 것이라 "비교
                    # 기준은 최초 시점으로 고정" 원칙과 어긋나지 않는다).
                    ocr_basis = row["ocr_text_snapshot"]
                    if ocr_basis is None and resolve_ocr_text is not None:
                        ocr_basis = resolve_ocr_text(
                            row["assessment_id"], row["drawing_id"], row["answer_index"]
                        )

                    if not new_typed_text:
                        new_diff: list[dict[str, Any]] | None = None
                        new_diff_count: int | None = None
                        new_spacing_diff = 0
                    else:
                        diff = compute_diff(ocr_basis, new_typed_text)
                        new_diff = diff["segments"] or None
                        new_diff_count = diff["char_count"]
                        new_spacing_diff = 1 if diff["spacing_diff"] else 0

                    # §5.5 — diff가 0(타이핑했지만 OCR과 완전히 같음)이면 패스와
                    # 같은 결과라 패스로 재분류한다. 판독 불가(난이도 5)는 텍스트
                    # 없이도 항상 타이핑이다(main.py의 submit_review와 같은 규칙).
                    new_review_type = (
                        "transcription"
                        if (new_typed_text and new_diff_count) or new_difficulty == 5
                        else "normal_check"
                    )
                    is_pass_now = new_review_type == "normal_check"
                    new_comment = None if is_pass_now else comment

                    cursor.execute(
                        queries.INSERT_REVIEW_EDIT,
                        {
                            "review_id": review_id,
                            "edited_by_id": reviewer_id,
                            "was_completed": 1 if was_completed else 0,
                        },
                    )
                    cursor.execute(
                        queries.UPDATE_REVIEW,
                        {
                            "id": review_id,
                            "reviewer_id": reviewer_id,
                            "review_type": new_review_type,
                            "typed_text": new_typed_text,
                            "ocr_diff": _dump_diff(new_diff),
                            "ocr_diff_char_count": new_diff_count,
                            "spacing_diff": new_spacing_diff,
                            "ocr_text_snapshot": ocr_basis,
                            "ocr_difficulty_level": new_difficulty,
                            "contains_negative_expression": new_negative_flag,
                            "comment": new_comment,
                        },
                    )
                connection.commit()
            except IntegrityError as exc:
                connection.rollback()
                errno = exc.args[0] if exc.args else None
                if errno == ER_NO_REFERENCED_ROW_2:
                    raise InvalidReferenceError("존재하지 않는 참조값입니다") from exc
                raise
            except Exception:
                connection.rollback()
                raise

        return {
            "assessment_id": row["assessment_id"],
            "drawing_id": row["drawing_id"],
            "answer_index": row["answer_index"],
            "was_completed": was_completed,
            "turned_into_pass": is_pass_now,
        }

    def fetch_edit_summary(self, review_ids: list[int]) -> dict[int, dict[str, Any]]:
        """의견별 수정 **요약**을 가져온다 (2026-08-24).

        예전에는 직전 값 전체를 돌려줬지만, 최종 검수 결과를 얻는 것이 목표라
        입력 도중의 오타 수정 기록까지 보여줄 이유가 없다고 판단해 내용은
        저장·조회 모두 그만뒀다(§8). 대신 남기는 두 가지:

        - edit_count : 몇 번 고쳤는가. 여러 번 고쳤다는 사실 자체가 "검수 기준이
          안 잡혔다"는 관리 신호라 지표로서 가치가 있다.
        - edited_after_completed : 완료(2명 이상) 이후의 수정이 한 번이라도
          있었는가. 상대 의견을 볼 수 있게 된 뒤의 수정인지 알려주는 값이라
          블라인드가 지켜졌는지 확인하는 근거가 된다.
        """
        if not review_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(review_ids))
        sql = queries.SELECT_REVIEW_EDIT_SUMMARY.format(placeholders=placeholders)
        rows = self._select_all(sql, [int(rid) for rid in review_ids])
        return {
            row["review_id"]: {
                "edit_count": int(row["edit_count"]),
                "edited_after_completed": bool(row["edited_after_completed"]),
            }
            for row in rows
        }

    def fetch_reviews(
        self, *, assessment_id: int, drawing_id: int, answer_index: int
    ) -> list[dict[str, Any]]:
        rows = self._select_all(
            queries.SELECT_REVIEWS_FOR_RECORD,
            {
                "assessment_id": assessment_id,
                "drawing_id": drawing_id,
                "answer_index": answer_index,
            },
        )
        for row in rows:
            row["ocr_diff"] = _load_diff(row["ocr_diff"])
        return rows

    def fetch_status(
        self, *, assessment_id: int, drawing_id: int, answer_index: int
    ) -> dict[str, Any] | None:
        rows = self._select_all(
            queries.SELECT_STATUS_FOR_RECORD,
            {
                "assessment_id": assessment_id,
                "drawing_id": drawing_id,
                "answer_index": answer_index,
            },
        )
        return rows[0] if rows else None

    def fetch_negative_keywords(self) -> list[str]:
        """09_add_review_negative_expression.sql — 부정 표현 자동 감지용
        활성 키워드 목록. 목록 조회 시 OCR 텍스트 매칭에 쓴다 (app/main.py)."""
        rows = self._select_all(queries.SELECT_NEGATIVE_KEYWORDS, {})
        return [row["keyword"] for row in rows]

    def fetch_reviewers(self) -> list[dict[str, Any]]:
        return self._select_all(queries.SELECT_REVIEWERS, {})

    def fetch_reviewer_by_username(self, username: str) -> dict[str, Any] | None:
        rows = self._select_all(
            queries.SELECT_REVIEWER_BY_USERNAME, {"username": username}
        )
        return rows[0] if rows else None

    def record_login_success(self, *, reviewer_id: int, ip: str | None) -> None:
        self._execute(queries.UPDATE_LOGIN_SUCCESS, {"id": reviewer_id, "ip": ip})

    def record_login_failure(self, *, reviewer_id: int) -> None:
        self._execute(queries.UPDATE_LOGIN_FAILURE, {"id": reviewer_id})

    def fetch_my_reviewed_keys(
        self,
        *,
        reviewer_id: int,
        review_type: str | None = None,
        difficulty_levels: list[int] | None = None,
        negative_flagged: bool = False,
        unreadable_only: bool = False,
        limit: int = 20000,
    ) -> tuple[list[tuple[int, int, int]], bool]:
        """§4.1 "내가 아직 처리하지 않은 것" 필터용 — 내가 이미 처리한 자연 키.

        review_type("패스/타이핑만")과 difficulty_level("전체 난이도")은 둘 다
        내가 이미 남긴 내 의견을 좁히는 것이라 블라인드 원칙과 무관하다.

        두 번째 반환값은 상한(limit)에 걸려 잘렸는지 여부다. 잘린 채로 조용히
        넘어가면 "이미 처리한 항목이 미검토로 다시 보이는" 조용한 오류가 되므로,
        호출부가 사용자에게 알릴 수 있도록 그대로 올려보낸다.
        """
        filters = ["reviewer_id = %(reviewer_id)s"]
        params: dict[str, Any] = {"reviewer_id": reviewer_id, "limit": limit}
        if review_type is not None:
            filters.append("review_type = %(review_type)s")
            params["review_type"] = review_type
        if difficulty_levels:
            # 난이도는 여러 개를 동시에 고를 수 있다(2026-08-24) — "1,2만 보기"
            # 처럼 좁혀 보기 위함이다. 아무것도 안 고르면 필터 자체를 걸지
            # 않는다("전체 난이도"). 1~5를 전부 고른 것과는 다르다 — 그러면
            # 난이도가 없는 과거 데이터가 조용히 빠지기 때문이다.
            names = [f"%(lvl_{i})s" for i in range(len(difficulty_levels))]
            filters.append(f"ocr_difficulty_level IN ({', '.join(names)})")
            for i, level in enumerate(difficulty_levels):
                params[f"lvl_{i}"] = level

        if unreadable_only:
            # 판독 불가 = 패스가 아니면서 옮겨 적은 내용이 없는 의견 (§5.1).
            # 난이도 5를 직접 보지 않는 이유는 main.py의 _is_unreadable 주석 참고.
            filters.append(
                "review_type = 'transcription' AND (typed_text IS NULL OR typed_text = '')"
            )

        if negative_flagged:
            # §5.2 — 내가 "부정 표현"으로 확정한 건. 자동 감지값이 아니라 내가
            # 최종적으로 켠 값이므로, 자동 감지가 못 잡은 것도 여기 포함되고
            # 자동 감지됐지만 내가 끈 것은 빠진다.
            filters.append("contains_negative_expression = 1")

        rows = self._select_all(
            queries.SELECT_MY_REVIEWED_KEYS_TEMPLATE.format(where_sql=" AND ".join(filters)),
            params,
        )
        keys = [
            (row["assessment_id"], row["drawing_id"], row["answer_index"])
            for row in rows
        ]
        return keys, len(rows) >= limit

    def fetch_review_keys(
        self, *, scan_limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        """처리 기록이 있는 답변 키 (완료 + 진행중). admin 화면(§4.5)용.

        두 번째 반환값은 상한에 걸려 **잘렸는지** 여부다. 예전에는 상한이
        1,000건으로 고정이었고 잘려도 아무 표시가 없어서, 검수가 쌓이면
        admin이 오래된 기록을 조용히 못 보게 되는 구조였다(2026-08-24).
        검수자 화면의 fetch_my_reviewed_keys와 같은 방식으로 호출부에
        올려보내 사용자에게 알린다.
        """
        rows = self._select_all(queries.SELECT_REVIEW_KEYS, {"scan_limit": scan_limit})
        return rows, len(rows) >= scan_limit

    def fetch_progress_summary(self) -> dict[str, int]:
        rows = self._select_all(queries.SELECT_PROGRESS_SUMMARY, {})
        return {row["status"]: row["records"] for row in rows}

    def fetch_in_progress_by_reviewer(self) -> dict[int, int]:
        rows = self._select_all(queries.SELECT_IN_PROGRESS_BY_REVIEWER, {})
        return {row["reviewer_id"]: row["records"] for row in rows}

    def fetch_reviews_for_keys(
        self, keys: list[tuple[int, int, int]]
    ) -> list[dict[str, Any]]:
        if not keys:
            return []
        placeholders = ", ".join(["(%s, %s, %s)"] * len(keys))
        sql = f"""
            SELECT
                id, assessment_id, drawing_id, answer_index, vlm_model,
                reviewer_id, review_type, typed_text, ocr_text_snapshot,
                ocr_diff, ocr_diff_char_count, spacing_diff,
                ocr_difficulty_level, contains_negative_expression, comment, created_at
            FROM ocr_review_comments
            WHERE (assessment_id, drawing_id, answer_index) IN ({placeholders})
            ORDER BY assessment_id DESC, drawing_id DESC, answer_index DESC, created_at ASC
        """
        flat_params = [value for key in keys for value in key]
        rows = self._select_all(sql, flat_params)
        for row in rows:
            row["ocr_diff"] = _load_diff(row["ocr_diff"])
        return rows

    def fetch_admin_comments_for_keys(
        self, keys: list[tuple[int, int, int]]
    ) -> list[dict[str, Any]]:
        if not keys:
            return []
        placeholders = ", ".join(["(%s, %s, %s)"] * len(keys))
        sql = f"""
            SELECT id, assessment_id, drawing_id, answer_index, admin_id, comment,
                   difficulty_level, created_at
            FROM ocr_admin_comments
            WHERE (assessment_id, drawing_id, answer_index) IN ({placeholders})
            ORDER BY created_at ASC
        """
        flat_params = [value for key in keys for value in key]
        return self._select_all(sql, flat_params)

    def add_admin_comment(
        self,
        *,
        assessment_id: int,
        drawing_id: int,
        answer_index: int,
        admin_id: int,
        comment: str,
        difficulty_level: int | None = None,
    ) -> int:
        params = {
            "assessment_id": assessment_id,
            "drawing_id": drawing_id,
            "answer_index": answer_index,
            "admin_id": admin_id,
            "comment": comment,
            "difficulty_level": difficulty_level,
        }
        with self._lock:
            connection = self._get_connection()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(queries.INSERT_ADMIN_COMMENT, params)
                    return cursor.lastrowid
            except IntegrityError as exc:
                errno = exc.args[0] if exc.args else None
                if errno == ER_DUP_ENTRY:
                    raise DuplicateAdminCommentError(
                        "이 레코드에 이미 코멘트를 남겼습니다 — 수정해주세요"
                    ) from exc
                if errno == ER_NO_REFERENCED_ROW_2:
                    raise InvalidReferenceError("존재하지 않는 관리자 계정입니다") from exc
                raise

    def update_admin_comment(
        self,
        *,
        comment_id: int,
        admin_id: int,
        comment: str,
        difficulty_level: int | None,
    ) -> None:
        """admin 본인이 남긴 코멘트/분류를 수정한다 (검수자의 §4.3과 같은 방식).

        admin 코멘트는 검수자 원본과 달리 §1의 "독립 판단 보존" 대상이 아니라서
        (참고용일 뿐 완료 판정에 관여하지 않음), ocr_review_edits 같은 별도
        이력 테이블 없이 그냥 덮어쓴다.
        """
        with self._lock:
            connection = self._get_connection()
            with connection.cursor() as cursor:
                cursor.execute(queries.SELECT_ADMIN_COMMENT_BY_ID, {"id": comment_id})
                row = cursor.fetchone()
                if row is None:
                    raise AdminCommentNotFoundError("수정할 코멘트를 찾을 수 없습니다")
                if row["admin_id"] != admin_id:
                    raise NotOwnerError("본인이 남긴 코멘트만 수정할 수 있습니다")

                try:
                    cursor.execute(
                        queries.UPDATE_ADMIN_COMMENT,
                        {
                            "id": comment_id,
                            "comment": comment,
                            "difficulty_level": difficulty_level,
                        },
                    )
                except IntegrityError as exc:
                    errno = exc.args[0] if exc.args else None
                    if errno == ER_NO_REFERENCED_ROW_2:
                        raise InvalidReferenceError("존재하지 않는 관리자 계정입니다") from exc
                    raise

    def close(self) -> None:
        with self._lock:
            if self._connection is None:
                return
            self._connection.close()
            self._connection = None

    def _execute(self, sql: str, params: dict[str, Any]) -> None:
        with self._lock:
            connection = self._get_connection()
            with connection.cursor() as cursor:
                cursor.execute(sql, params)

    def _select_all(
        self, sql: str, params: dict[str, Any] | list[Any]
    ) -> list[dict[str, Any]]:
        with self._lock:
            connection = self._get_connection()
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def _get_connection(self) -> Any:
        if pymysql is None or DictCursor is None:
            raise RuntimeError("PyMySQL is required. Install it with `uv sync`.")

        review = self.settings.review_mysql
        if not review.configured:
            raise RuntimeError(
                "REVIEW_MYSQL_HOST, REVIEW_MYSQL_USER, REVIEW_MYSQL_DATABASE must be set"
            )

        if self._connection is not None:
            self._connection.ping(reconnect=True)
            return self._connection

        self._connection = pymysql.connect(
            host=review.host,
            port=review.port,
            user=review.user,
            password=review.password,
            database=review.database,
            charset=review.charset,
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=self.settings.request_timeout_seconds,
            read_timeout=self.settings.request_timeout_seconds,
            write_timeout=self.settings.request_timeout_seconds,
        )
        return self._connection
