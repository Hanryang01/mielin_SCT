# OCR 검수 DB(ocr_review, 03_ocr_review_schema.sql) 쿼리 템플릿.
# mielin(sct_data.py)과 달리 이 DB는 쓰기(INSERT)까지 한다.

INSERT_REVIEW = """
INSERT INTO ocr_review_comments
    (assessment_id, drawing_id, answer_index, vlm_model,
     reviewer_id, review_type, typed_text, ocr_text_snapshot,
     ocr_diff, ocr_diff_char_count, spacing_diff, ocr_difficulty_level,
     contains_negative_expression, comment)
VALUES
    (%(assessment_id)s, %(drawing_id)s, %(answer_index)s, %(vlm_model)s,
     %(reviewer_id)s, %(review_type)s, %(typed_text)s, %(ocr_text_snapshot)s,
     %(ocr_diff)s, %(ocr_diff_char_count)s, %(spacing_diff)s, %(ocr_difficulty_level)s,
     %(contains_negative_expression)s, %(comment)s)
"""

SELECT_REVIEWS_FOR_RECORD = """
SELECT
    id, assessment_id, drawing_id, answer_index, vlm_model,
    reviewer_id, review_type, typed_text, ocr_text_snapshot,
    ocr_diff, ocr_diff_char_count, spacing_diff,
    ocr_difficulty_level, contains_negative_expression, comment, created_at
FROM ocr_review_comments
WHERE assessment_id = %(assessment_id)s
  AND drawing_id = %(drawing_id)s
  AND answer_index = %(answer_index)s
ORDER BY created_at ASC
"""

SELECT_STATUS_FOR_RECORD = """
SELECT assessment_id, drawing_id, answer_index, review_count, status
FROM v_sct_review_status
WHERE assessment_id = %(assessment_id)s
  AND drawing_id = %(drawing_id)s
  AND answer_index = %(answer_index)s
"""

# 09_add_review_negative_expression.sql — 부정 표현 자동 감지 키워드 마스터.
# 목록 조회 시 OCR 텍스트를 이 키워드들로 매칭해 auto_negative_flag를 계산한다
# (app/main.py).
SELECT_NEGATIVE_KEYWORDS = """
SELECT keyword
FROM ocr_negative_keywords
WHERE is_active = 1
"""

# admin 화면(§4.5)이 쓰는 계정 목록.
#
# **비활성/삭제된 계정도 함께 내려보낸다**(2026-08-27). 예전에는 현역만 골라
# 내보냈는데, 검수 기록을 남긴 계정이 나중에 비활성화되면 화면이 그 이름을 몰라
# "검수자 의견" 칸에 `#4:`처럼 내부 id가 노출됐다. 필터 드롭다운과 타이핑 줄
# 자리는 화면이 is_active/is_deleted로 직접 걸러 현역만 쓴다(loadReviewers).
SELECT_REVIEWERS = """
SELECT id, name, role, is_active, is_deleted
FROM ocr_reviewers
ORDER BY (role = 'admin'), id
"""

# admin 화면(§4.5)용 — 처리 기록이 있는 답변 키를 최신순으로 스캔한다.
# 완료(2명 이상)뿐 아니라 진행중(1명)도 포함해서 admin이 진행 현황을 볼 수
# 있게 한다. 단, 진행중 건의 "의견 내용"은 서버가 가린다 (main.py 참고) —
# admin도 검수자를 겸할 수 있어서, 남의 의견을 먼저 읽고 자기 의견을 내면
# 독립성이 깨지기 때문이다.
#
# 대량 데이터 스케일에서는 이 스캔 자체를 페이지네이션해야 하지만, 지금은
# 스캔 후 파이썬에서 필터링/페이지네이션하는 단순한 방식으로 시작한다
# (OCR 검수 시나리오.md §7 성능 항목과 같은 종류의 "나중에 최적화" 대상).
SELECT_REVIEW_KEYS = """
SELECT assessment_id, drawing_id, answer_index, review_count, status
FROM v_sct_review_status
ORDER BY assessment_id DESC, drawing_id DESC, answer_index DESC
LIMIT %(scan_limit)s
"""

# admin 진행 현황 요약 — 개별 의견 내용을 노출하지 않으므로 블라인드와 무관하다.
#
# 2026-08-24 — **패스도 "처리한 것"으로 센다.** 예전에는 타이핑이 하나도 없는
# 레코드(패스만 있는 건)를 집계에서 뺐는데, 그러면 카드가 실제 진행 상황보다
# 훨씬 작게 나온다(진행중 25 vs 실제 97). 검수자가 이미지를 보고 "OCR이 맞다"고
# 판정한 것도 분명히 처리한 것이고, 목록 화면의 전체 건수와도 어긋났다.
# 이제 완료 + 진행중 = 검수 기록이 있는 전체 레코드가 되어 목록과 맞는다.
SELECT_PROGRESS_SUMMARY = """
SELECT status, COUNT(*) AS records
FROM (
    SELECT
        assessment_id, drawing_id, answer_index,
        CASE WHEN COUNT(*) >= 2 THEN 'completed' ELSE 'needs_second_opinion' END AS status
    FROM ocr_review_comments
    GROUP BY assessment_id, drawing_id, answer_index
) t
GROUP BY status
"""

# admin 진행률 카드의 "진행중"을 검수자별로 쪼개서 보여주기 위한 집계
# (2026-08-31) — 의견이 정확히 1개뿐인 레코드만 대상이므로, 그 하나뿐인
# reviewer_id가 곧 "누가 처리했는지"다(MIN은 값이 하나뿐이라 그대로 꺼내는
# 용도일 뿐이다). 완료(2명 이상)는 여기 포함되지 않는다 — SELECT_PROGRESS_SUMMARY
# 와 기준이 같아야 두 숫자를 더했을 때 어긋나지 않는다.
SELECT_IN_PROGRESS_BY_REVIEWER = """
SELECT reviewer_id, COUNT(*) AS records
FROM (
    SELECT assessment_id, drawing_id, answer_index, MIN(reviewer_id) AS reviewer_id
    FROM ocr_review_comments
    GROUP BY assessment_id, drawing_id, answer_index
    HAVING COUNT(*) = 1
) t
GROUP BY reviewer_id
"""

INSERT_ADMIN_COMMENT = """
INSERT INTO ocr_admin_comments
    (assessment_id, drawing_id, answer_index, admin_id, comment, difficulty_level)
VALUES
    (%(assessment_id)s, %(drawing_id)s, %(answer_index)s, %(admin_id)s, %(comment)s,
     %(difficulty_level)s)
"""

# admin당 레코드당 코멘트 1건(uq_admin_comment_per_admin, 07_add_admin_comment_unique.sql)
# — 검수자가 본인 타이핑을 수정하는 것과 같은 방식으로, 본인 코멘트만 다시 고친다.
SELECT_ADMIN_COMMENT_BY_ID = """
SELECT id, assessment_id, drawing_id, answer_index, admin_id, comment,
       difficulty_level
FROM ocr_admin_comments
WHERE id = %(id)s
"""

UPDATE_ADMIN_COMMENT = """
UPDATE ocr_admin_comments
SET comment = %(comment)s,
    difficulty_level = %(difficulty_level)s
WHERE id = %(id)s
"""

# ---- 로그인 (ocr_reviewers가 웹 로그인 계정 소스) ----
# username은 04_add_reviewer_username.sql에서 추가한 로그인 아이디 컬럼.
# is_active/is_deleted로 걸러진 계정은 로그인 자체가 불가능해야 하므로,
# 여기서는 조회만 하고 판정은 auth.py에서 한다 (비활성 사유를 구분하기 위함).
SELECT_REVIEWER_BY_USERNAME = """
SELECT id, username, name, role, password_hash, is_active, is_deleted
FROM ocr_reviewers
WHERE username = %(username)s
"""

UPDATE_LOGIN_SUCCESS = """
UPDATE ocr_reviewers
   SET last_login_at = CURRENT_TIMESTAMP,
       last_login_ip = %(ip)s,
       failed_login_count = 0
 WHERE id = %(id)s
"""

UPDATE_LOGIN_FAILURE = """
UPDATE ocr_reviewers
   SET failed_login_count = failed_login_count + 1
 WHERE id = %(id)s
"""

# ---- §4.1 "내가 아직 처리하지 않은 것" 기본 필터 ----
# mielin(원본 SCT)과 검수 DB가 서로 다른 서버일 수 있어 SQL JOIN이 안 된다.
# 그래서 내가 이미 처리한 자연 키를 여기서 먼저 뽑고, mielin 쪽 쿼리에서
# NOT IN으로 제외한다 (client.exclude_keys). 데이터가 많아지면 이 목록이
# 커지므로 상한을 두고, 잘렸는지 여부를 호출부에 알려준다
# (OCR 검수 시나리오.md §7 성능 항목 참고).
# review_type("내가 패스한/타이핑한 것만")·ocr_difficulty_level("전체 난이도")로
# 선택적으로 좁힐 수 있게 WHERE 절을 동적으로 조립한다
# (review_client.fetch_my_reviewed_keys).
SELECT_MY_REVIEWED_KEYS_TEMPLATE = """
SELECT assessment_id, drawing_id, answer_index
FROM ocr_review_comments
WHERE {where_sql}
ORDER BY id DESC
LIMIT %(limit)s
"""

# ---- 검수 의견 수정 (§4.3 — 본인 것만) ----
# 원본 독립 판단을 잃지 않기 위해, UPDATE 전에 직전 값을 ocr_review_edits로
# 옮겨 담는다 (05_add_review_edits.sql).
SELECT_REVIEW_BY_ID = """
SELECT id, assessment_id, drawing_id, answer_index, reviewer_id, review_type,
       typed_text, ocr_text_snapshot, ocr_diff, ocr_diff_char_count, spacing_diff,
       ocr_difficulty_level, contains_negative_expression, comment
FROM ocr_review_comments
WHERE id = %(id)s
"""

# 2026-08-24 — 이전 값(prev_*)은 더 이상 저장하지 않는다. 최종 검수 결과를
# 얻는 것이 목표이고, 빠르게 입력하다 생긴 오타를 고친 기록까지 남길 이유가
# 없다는 판단이다(§8). 다만 **수정 횟수**와 **완료 후 수정 여부**는 남긴다 —
# 여러 번 고쳤다는 사실 자체가 "검수 기준이 안 잡혔다"는 관리 신호이고,
# was_completed는 상대 의견을 볼 수 있게 된 뒤의 수정인지 알려주는(=블라인드가
# 지켜졌는지 확인하는) 유일한 근거이기 때문이다.
# prev_* 컬럼은 과거 데이터가 들어 있어 남겨두되 새로 채우지 않는다.
INSERT_REVIEW_EDIT = """
INSERT INTO ocr_review_edits
    (review_id, edited_by_id, was_completed)
VALUES
    (%(review_id)s, %(edited_by_id)s, %(was_completed)s)
"""

UPDATE_REVIEW = """
UPDATE ocr_review_comments
   SET review_type = %(review_type)s,
       typed_text = %(typed_text)s,
       ocr_diff = %(ocr_diff)s,
       ocr_diff_char_count = %(ocr_diff_char_count)s,
       spacing_diff = %(spacing_diff)s,
       -- 스냅샷은 **비어 있을 때만** 채운다. 이미 값이 있으면 절대
       -- 덮어쓰지 않는다 — 비교 기준은 최초 검수 시점으로 고정되어야 한다.
       ocr_text_snapshot = COALESCE(ocr_text_snapshot, %(ocr_text_snapshot)s),
       ocr_difficulty_level = %(ocr_difficulty_level)s,
       contains_negative_expression = %(contains_negative_expression)s,
       comment = %(comment)s
 WHERE id = %(id)s
   AND reviewer_id = %(reviewer_id)s
"""

# 이전에 뭐라고 입력했었는지를 실제로 볼 수 있어야 "수정 이력 보존"이 의미가
# 있으므로, 건수뿐 아니라 전체 내용을 가져온다.
# 이전 값이 아니라 **요약**만 읽는다 (2026-08-24) — 화면은 "수정 2회"처럼
# 횟수만 보여주고, 완료 후 수정이 한 번이라도 있었는지만 함께 알려준다.
SELECT_REVIEW_EDIT_SUMMARY = """
SELECT review_id,
       COUNT(*) AS edit_count,
       MAX(was_completed) AS edited_after_completed
FROM ocr_review_edits
WHERE review_id IN ({placeholders})
GROUP BY review_id
"""
