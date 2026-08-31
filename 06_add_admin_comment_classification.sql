-- ============================================================
-- 마이그레이션: admin 코멘트에 분류 추가 (ocr_admin_comments.classification)
--
-- admin도 참고용 코멘트를 남길 때 분류(개선불가/개선가능 세부유형)를 함께
-- 남길 수 있어야 한다는 요청. ocr_review_comments.classification과 같은
-- 마스터 테이블(ocr_classification_types)을 참조하되 완전히 별도 컬럼이다 —
-- admin은 최종 결정권자가 아니므로 이 값이 검수자 원본 의견에 영향을 주거나
-- 완료 판정에 관여하지 않는다(OCR 검수 시나리오.md §4.5).
--
-- MySQL 8.4에는 ADD COLUMN IF NOT EXISTS가 없어서 information_schema를 보고
-- 분기한다 (여러 번 실행해도 안전 — 04_add_reviewer_username.sql과 같은 패턴).
--
-- 적용:
--   mysql --default-character-set=utf8mb4 -u root -p ocr_review < 06_add_admin_comment_classification.sql
-- ============================================================

SET @has_col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_admin_comments'
      AND COLUMN_NAME = 'classification'
);

SET @sql := IF(@has_col = 0,
    "ALTER TABLE ocr_admin_comments
       ADD COLUMN classification VARCHAR(40) DEFAULT NULL
           COMMENT 'ocr_classification_types.code 참조 (참고용, 검수자 원본과 무관)'
           AFTER comment",
    'SELECT ''classification column already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @has_key := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_admin_comments'
      AND INDEX_NAME = 'ix_admin_comment_classification'
);

SET @sql := IF(@has_key = 0,
    'ALTER TABLE ocr_admin_comments ADD KEY ix_admin_comment_classification (classification)',
    'SELECT ''ix_admin_comment_classification already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @has_fk := (
    SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_admin_comments'
      AND CONSTRAINT_NAME = 'fk_admin_comment_classification'
);

SET @sql := IF(@has_fk = 0,
    'ALTER TABLE ocr_admin_comments
       ADD CONSTRAINT fk_admin_comment_classification
           FOREIGN KEY (classification) REFERENCES ocr_classification_types (code)',
    'SELECT ''fk_admin_comment_classification already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
