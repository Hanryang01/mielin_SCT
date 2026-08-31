-- ============================================================
-- 마이그레이션: 검수자 분류(classification)를 OCR 난이도(1~5)로 대체
--
-- 실제 검수자 의견: "9종 분류 대신 OCR 난이도를 입력하면 좋겠다"는 요청을
-- 받아들여 검수자가 직접 입력하는 필드를 분류 → 난이도로 완전히 바꾼다
-- (OCR 검수 시나리오.md §5 참고).
--
-- ocr_review_comments.classification 컬럼/ocr_classification_types 테이블은
-- 지우지 않는다 — 이미 쌓인 과거 의견의 분류값은 그대로 조회 가능해야
-- 하고(§1 "수정 없이 보존"), admin 참고 코멘트(ocr_admin_comments.classification)
-- 는 이번 변경과 무관하게 계속 그 테이블을 참조한다. 신규 검수자 제출에서만
-- classification 대신 ocr_difficulty_level을 받는다 — 애플리케이션 레이어
-- (app/main.py)에서 강제한다.
--
-- 레벨 정의 (검수자 화면 버튼 라벨과 동일):
--   1 매우 쉬움 (정자체 및 완벽한 인식 수준)
--   2 쉬움 (일반적인 필기체)
--   3 보통 (주의 및 문맥 파악 필요)
--   4 어려움 (심한 악필 및 복잡한 구조)
--   5 매우 어려움 / 판독 불가
--
-- MySQL 8.4에는 ADD COLUMN IF NOT EXISTS가 없어서 information_schema를 보고
-- 분기한다 (여러 번 실행해도 안전 — 04/06과 같은 패턴).
--
-- 적용:
--   mysql --default-character-set=utf8mb4 -u root -p ocr_review < 08_add_review_difficulty_level.sql
-- ============================================================

SET @has_col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_review_comments'
      AND COLUMN_NAME = 'ocr_difficulty_level'
);

SET @sql := IF(@has_col = 0,
    "ALTER TABLE ocr_review_comments
       ADD COLUMN ocr_difficulty_level TINYINT DEFAULT NULL
           COMMENT '검수자가 매긴 필기 판독 난이도 1~5 (review_type=transcription일 때만, classification을 대체함)'
           AFTER classification,
       ADD CONSTRAINT chk_review_difficulty_level
           CHECK (ocr_difficulty_level IS NULL OR ocr_difficulty_level BETWEEN 1 AND 5)",
    'SELECT ''ocr_difficulty_level column already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @has_key := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_review_comments'
      AND INDEX_NAME = 'ix_review_difficulty_level'
);

SET @sql := IF(@has_key = 0,
    'ALTER TABLE ocr_review_comments ADD KEY ix_review_difficulty_level (ocr_difficulty_level)',
    'SELECT ''ix_review_difficulty_level already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- ocr_review_edits(수정 이력, 05_add_review_edits.sql)도 난이도 이전 값을
-- 보존해야 한다 — prev_classification과 같은 역할.
SET @has_edit_col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_review_edits'
      AND COLUMN_NAME = 'prev_ocr_difficulty_level'
);

SET @sql := IF(@has_edit_col = 0,
    "ALTER TABLE ocr_review_edits
       ADD COLUMN prev_ocr_difficulty_level TINYINT DEFAULT NULL AFTER prev_classification",
    'SELECT ''prev_ocr_difficulty_level column already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
