from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .config import Settings
from .mysql_reader import MysqlReader
from .queries import sct_data


class SctClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._reader = MysqlReader(settings)

    def fetch_filter_options(self) -> dict[str, Any]:
        hospital_ids = [
            row["hospital_id"]
            for row in self._reader.select_all(sct_data.FILTER_HOSPITAL_IDS)
        ]
        age_groups = [
            row["sct_age_group"]
            for row in self._reader.select_all(sct_data.FILTER_AGE_GROUPS)
        ]
        vlm_models = [
            row["vlm_model"]
            for row in self._reader.select_all(sct_data.FILTER_VLM_MODELS)
        ]
        return {
            "hospital_ids": hospital_ids,
            "age_groups": age_groups,
            "vlm_models": vlm_models,
        }

    def fetch_stats(self) -> dict[str, Any]:
        """admin 화면 상단 통계 카드용 — 전체 검사 수 / 전체 검사자 수."""
        rows = self._reader.select_all(sct_data.STATS_TOTALS)
        return rows[0] if rows else {"total_assessments": 0, "total_clients": 0}

    def fetch_records(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        hospital_id: int | None = None,
        age_group: str | None = None,
        ocr_failed: bool | None = None,
        vlm_model: str | None = None,
        date_start: date | None = None,
        date_end: date | None = None,
        keyword: str | None = None,
        has_image: bool | None = None,
        negative_keywords: list[str] | None = None,
        # 부정 표현 필터를 "내 판단 우선"으로 만드는 두 키 목록 (§5.2, 아래 참고)
        negative_flagged_keys: list[tuple[int, int, int]] | None = None,
        negative_reviewed_keys: list[tuple[int, int, int]] | None = None,
        exclude_keys: list[tuple[int, int, int]] | None = None,
        include_keys: list[tuple[int, int, int]] | None = None,
    ) -> dict[str, Any]:
        filters: list[str] = ["1=1"]
        params: dict[str, Any] = {}

        if has_image is not None:
            # s3_key가 없는 레코드는 media_id/vlm_status/ocr_text도 함께 NULL이다
            # (업로드된 이미지가 아예 없는 빈 레코드 — 확인 결과 1,353건 전부 일치).
            # 검수할 대상이 아니라서 검수 화면에서는 기본적으로 제외한다.
            if has_image:
                filters.append("r.s3_key IS NOT NULL AND r.s3_key <> ''")
            else:
                filters.append("(r.s3_key IS NULL OR r.s3_key = '')")

        if include_keys is not None:
            # "내가 패스한 것만" / "내가 타이핑한 것만" — 해당 키만 남긴다.
            # 빈 목록이면 결과도 0건이어야 하므로 절대 참이 될 수 없는 조건을 넣는다
            # (조건을 아예 빼면 전체가 나와서 정반대 결과가 된다).
            if not include_keys:
                filters.append("1=0")
            else:
                tuples = ", ".join(
                    f"({int(a)}, {int(d)}, {int(i)})" for a, d, i in include_keys
                )
                filters.append(
                    f"(r.assessment_id, r.drawing_id, r.answer_index) IN ({tuples})"
                )

        if exclude_keys:
            # §4.1 "내가 아직 처리하지 않은 것" — 검수 DB는 별도 서버일 수 있어
            # JOIN이 안 되므로, 내가 처리한 자연 키를 받아서 여기서 제외한다.
            #
            # 이 값만 %(name)s 바인딩이 아니라 리터럴로 박는다: 키 개수가
            # 수천 개까지 갈 수 있어 바인딩 파라미터로 풀면 쿼리 준비 비용이
            # 커지기 때문이다. int()를 거치므로 정수가 아닌 값은 여기서
            # ValueError로 죽고 문자열이 SQL에 섞일 수 없다 (인젝션 불가).
            tuples = ", ".join(
                f"({int(a)}, {int(d)}, {int(i)})" for a, d, i in exclude_keys
            )
            filters.append(
                f"(r.assessment_id, r.drawing_id, r.answer_index) NOT IN ({tuples})"
            )

        if hospital_id is not None:
            filters.append("r.hospital_id = %(hospital_id)s")
            params["hospital_id"] = hospital_id

        if age_group:
            filters.append("r.sct_age_group = %(age_group)s")
            params["age_group"] = age_group

        if ocr_failed is not None:
            filters.append("r.ocr_failed = %(ocr_failed)s")
            params["ocr_failed"] = 1 if ocr_failed else 0

        if vlm_model:
            filters.append("r.vlm_model = %(vlm_model)s")
            params["vlm_model"] = vlm_model

        if date_start is not None:
            filters.append("COALESCE(r.source_created_at, r.imported_at) >= %(date_start)s")
            params["date_start"] = date_start

        if date_end is not None:
            # half-open range: 종료일 다음날 00:00 미만
            filters.append("COALESCE(r.source_created_at, r.imported_at) < %(date_end_exclusive)s")
            params["date_end_exclusive"] = date_end + timedelta(days=1)

        if keyword:
            filters.append(
                "(r.client_name LIKE %(keyword)s "
                "OR r.ocr_text LIKE %(keyword)s "
                "OR CAST(r.assessment_id AS CHAR) LIKE %(keyword)s)"
            )
            params["keyword"] = f"%{keyword}%"

        if negative_keywords:
            # §5.2 "부정 표현 포함" (2026-08-24 재정의) — 두 기준을 OR로 합친다:
            #
            #   (1) 내가 부정 표현으로 **확정한** 건 (negative_flagged_keys)
            #   (2) 아직 내가 처리하지 않았고 OCR 텍스트에 키워드가 있는 건
            #
            # 예전에는 (2)만 봤다. 그래서 키워드가 없는 답변에 검수자가 직접
            # 체크해도 이 필터에 안 잡히고, 반대로 자동 감지된 걸 체크 해제해도
            # 계속 잡히는 문제가 있었다 — 사람의 판단이 무시된 셈이다.
            # (1)로 처리 후의 판단을 존중하고, (2)로 처리 전 "미리 훑어보기"
            # 용도를 유지한다. admin 화면은 처리된 것만 다루므로 (1)만 쓴다.
            or_clauses = []

            keyword_clauses = []
            for i, kw in enumerate(negative_keywords):
                key = f"neg_kw_{i}"
                keyword_clauses.append(f"r.ocr_text LIKE %({key})s")
                params[key] = f"%{kw}%"
            keyword_sql = f"({' OR '.join(keyword_clauses)})"

            if negative_reviewed_keys:
                # 이미 처리한 건은 키워드가 아니라 내 판단을 따라야 하므로,
                # 자동 감지 경로에서는 빼둔다 (아래 (1)에서 다시 들어온다).
                tuples = ", ".join(
                    f"({int(a)}, {int(d)}, {int(i)})" for a, d, i in negative_reviewed_keys
                )
                keyword_sql = (
                    f"({keyword_sql} AND (r.assessment_id, r.drawing_id, r.answer_index)"
                    f" NOT IN ({tuples}))"
                )
            or_clauses.append(keyword_sql)

            if negative_flagged_keys:
                tuples = ", ".join(
                    f"({int(a)}, {int(d)}, {int(i)})" for a, d, i in negative_flagged_keys
                )
                or_clauses.append(
                    f"(r.assessment_id, r.drawing_id, r.answer_index) IN ({tuples})"
                )

            filters.append(f"({' OR '.join(or_clauses)})")

        where_sql = " AND ".join(filters)

        total_row = self._reader.select_all(
            sct_data.RECORD_LIST_COUNT.format(where_sql=where_sql), params
        )
        total = total_row[0]["total"] if total_row else 0

        list_params = dict(params)
        list_params["limit"] = page_size
        list_params["offset"] = (page - 1) * page_size
        items = self._reader.select_all(
            sct_data.RECORD_LIST.format(where_sql=where_sql), list_params
        )

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        }

    def fetch_records_by_keys(
        self, keys: list[tuple[int, int, int]]
    ) -> dict[tuple[int, int, int], dict[str, Any]]:
        """admin 화면(§4.5)에서 검수 DB의 완료 레코드에 OCR 텍스트/이미지를
        붙여줄 때 쓴다. mielin은 우리가 관리하는 DB가 아니라서(별도 서버일
        수 있음) 검수 DB와 SQL JOIN이 안 되므로, 자연 키로 배치 조회해서
        애플리케이션 레이어에서 합친다."""
        if not keys:
            return {}
        placeholders = ", ".join(["(%s, %s, %s)"] * len(keys))
        sql = sct_data.RECORD_BY_KEYS.format(placeholders=placeholders)
        flat_params = [value for key in keys for value in key]
        rows = self._reader.select_all(sql, flat_params)
        return {
            (row["assessment_id"], row["drawing_id"], row["answer_index"]): row
            for row in rows
        }

    def fetch_record_image_key(self, record_id: int) -> str | None:
        rows = self._reader.select_all(
            "SELECT s3_key FROM sct_import_records WHERE id = %(id)s",
            {"id": record_id},
        )
        if not rows:
            return None
        return rows[0]["s3_key"]
