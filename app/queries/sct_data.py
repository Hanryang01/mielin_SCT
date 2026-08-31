# SCT 데이터 조회 쿼리 템플릿
#
# {where_sql} 은 Python 쪽에서 조건 리스트를 AND로 이어붙인 문자열로 채워지고,
# 값 자체는 항상 %(name)s 파라미터 바인딩으로 전달된다 (SQL 인젝션 방지).

RECORD_LIST_COUNT = """
SELECT COUNT(*) AS total
FROM sct_import_records r
WHERE {where_sql}
"""

RECORD_LIST = """
SELECT
    r.id,
    r.assessment_id,
    r.drawing_id,
    r.client_id,
    r.hospital_id,
    r.client_name,
    r.sct_age_group,
    r.question_number,
    r.sct_question,
    r.answer_index,
    r.ocr_text,
    r.ocr_failed,
    r.sct_na_reason,
    r.media_id,
    r.s3_key,
    r.local_image_path,
    r.vlm_provider,
    r.vlm_model,
    r.vlm_status,
    r.source_created_at,
    r.imported_at
FROM sct_import_records r
WHERE {where_sql}
ORDER BY COALESCE(r.source_created_at, r.imported_at) DESC, r.id DESC
LIMIT %(limit)s OFFSET %(offset)s
"""

RECORD_BY_KEYS = """
SELECT
    r.id,
    r.assessment_id,
    r.drawing_id,
    r.answer_index,
    r.hospital_id,
    r.sct_age_group,
    r.question_number,
    r.sct_question,
    r.ocr_text,
    r.ocr_failed,
    r.sct_na_reason,
    r.vlm_model,
    r.source_created_at,
    r.imported_at
FROM sct_import_records r
WHERE (r.assessment_id, r.drawing_id, r.answer_index) IN ({placeholders})
"""

FILTER_HOSPITAL_IDS = """
SELECT DISTINCT hospital_id
FROM sct_import_records
WHERE hospital_id IS NOT NULL
ORDER BY hospital_id
"""

FILTER_AGE_GROUPS = """
SELECT DISTINCT sct_age_group
FROM sct_import_records
WHERE sct_age_group IS NOT NULL AND sct_age_group != ''
ORDER BY sct_age_group
"""

FILTER_VLM_MODELS = """
SELECT DISTINCT vlm_model
FROM sct_import_records
WHERE vlm_model IS NOT NULL AND vlm_model != ''
ORDER BY vlm_model
"""

# admin 화면 상단 통계 카드용 — 검사(assessment) 수와 검사자(client) 수는
# 서로 다르다. 한 사람이 검사를 여러 번 받을 수 있어 client_id 기준으로
# 따로 센다.
STATS_TOTALS = """
SELECT
    COUNT(DISTINCT assessment_id) AS total_assessments,
    COUNT(DISTINCT client_id) AS total_clients,
    -- 실제 검수 대상이 되는 이미지 수. s3_key가 없는 레코드는 업로드된 이미지가
    -- 아예 없는 빈 건이라(media_id/vlm_status/ocr_text도 함께 NULL) 목록에서도
    -- has_image로 걸러진다 — 카드도 같은 기준으로 세야 화면과 숫자가 맞는다.
    SUM(CASE WHEN s3_key IS NOT NULL AND s3_key <> '' THEN 1 ELSE 0 END) AS total_images
FROM sct_import_records
"""
