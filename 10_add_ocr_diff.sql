-- ============================================================
-- 마이그레이션: OCR 텍스트 스냅샷 + 차이(diff) 자동 기록
--
-- 실제 검수자 의견: "OCR 텍스트와 입력한 텍스트가 다른 부분을 표시하고,
-- 그 부분을 자동으로 기록해서 나중에 필터링·분석에 쓰고 싶다"
-- (OCR 검수 시나리오.md §5.3 참고).
--
-- 예시 (검수자가 제시한 표기법):
--   공부                    -> [거짓]                      (전체가 다름)
--   사람대 매너가 있어야한다 -> 사람[대] 매너가 있어야한다  (OCR에만 있는 글자)
--   사람                    -> 사[랑]                      (한 글자 오인식)
--
-- 검수자가 대괄호를 직접 타이핑하는 게 아니다 — 서버가 typed_text와
-- ocr_text_snapshot을 글자 단위로 비교해서 자동 계산한다.
--
-- ocr_text_snapshot을 같이 저장하는 이유: 원본 sct_import_records는 다른
-- DB(mielin)에 있어서 SQL JOIN이 불가능하고, 나중에 OCR 텍스트가 갱신되면
-- "그때 검수자가 무엇과 비교했는가"를 재현할 수 없다. vlm_model을 스냅샷으로
-- 저장하는 것과 완전히 같은 이유다(03_ocr_review_schema.sql 참고). 이 컬럼이
-- 있어야 diff를 나중에 다시 계산하거나 검증할 수 있다.
--
-- ocr_diff는 JSON이다. 세그먼트 하나가 "다른 부분" 하나에 대응한다:
--   [{"op": "replace", "ocr": "람", "typed": "랑", "ocr_pos": 1, "typed_pos": 1}]
-- op는 replace(오인식) / delete(OCR에만 있음) / insert(OCR이 누락함) 세 가지.
-- 이 구조라면 "OCR이 '람'을 '랑'으로 읽은 사례 전체" 같은 조회를 JSON 함수로
-- 바로 뽑을 수 있다.
--
-- ocr_diff_char_count는 필터·정렬용 요약값이다. JSON 내부를 스캔하지 않고
-- "차이가 1글자인 것만" / "차이가 큰 것만"을 인덱스로 걸러내기 위해 둔다.
-- 0이면 타이핑 결과가 OCR과 완전히 같다는 뜻이다.
--
-- 적용:
--   mysql --default-character-set=utf8mb4 -u root -p ocr_review < 10_add_ocr_diff.sql
-- ============================================================

SET @has_snapshot := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_review_comments'
      AND COLUMN_NAME = 'ocr_text_snapshot'
);

SET @sql := IF(@has_snapshot = 0,
    "ALTER TABLE ocr_review_comments
       ADD COLUMN ocr_text_snapshot MEDIUMTEXT DEFAULT NULL
           COMMENT '검수 시점에 화면에 보인 OCR 텍스트 스냅샷 (diff 재현·검증용, mielin DB와 JOIN이 불가능해서 값을 복사해둔다)'
           AFTER typed_text",
    'SELECT ''ocr_text_snapshot column already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;


SET @has_diff := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_review_comments'
      AND COLUMN_NAME = 'ocr_diff'
);

SET @sql := IF(@has_diff = 0,
    "ALTER TABLE ocr_review_comments
       ADD COLUMN ocr_diff JSON DEFAULT NULL
           COMMENT 'OCR 텍스트와 타이핑 텍스트의 글자 단위 차이 세그먼트 (서버가 자동 계산)'
           AFTER ocr_text_snapshot,
       ADD COLUMN ocr_diff_char_count INT DEFAULT NULL
           COMMENT '차이 글자 수 요약 (필터·정렬용). 0이면 타이핑 결과가 OCR과 동일'
           AFTER ocr_diff",
    'SELECT ''ocr_diff columns already exist'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;


SET @has_key := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_review_comments'
      AND INDEX_NAME = 'ix_review_diff_char_count'
);

SET @sql := IF(@has_key = 0,
    'ALTER TABLE ocr_review_comments ADD KEY ix_review_diff_char_count (ocr_diff_char_count)',
    'SELECT ''ix_review_diff_char_count already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- 수정 이력에도 직전 값을 남긴다 (§1 "원래 판단을 수정 없이 보존").
-- diff는 typed_text에서 다시 계산할 수 있지만, 그러려면 그 시점의
-- ocr_text_snapshot이 필요하다 — 스냅샷도 같이 보존해야 이력이 자기완결적이다.
SET @has_edit_cols := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_review_edits'
      AND COLUMN_NAME = 'prev_ocr_diff'
);

SET @sql := IF(@has_edit_cols = 0,
    "ALTER TABLE ocr_review_edits
       ADD COLUMN prev_ocr_text_snapshot MEDIUMTEXT DEFAULT NULL AFTER prev_typed_text,
       ADD COLUMN prev_ocr_diff JSON DEFAULT NULL AFTER prev_ocr_text_snapshot,
       ADD COLUMN prev_ocr_diff_char_count INT DEFAULT NULL AFTER prev_ocr_diff",
    'SELECT ''prev_ocr_diff columns already exist'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
