-- 13_drop_classification.sql
-- OCR 검수 시나리오.md §5.1 / §4.5 (2026-08-24)
--
-- **분류(classification) 체계를 완전히 제거한다.**
--
-- 경위:
--   - 검수자 화면의 9종 분류는 08_add_review_difficulty_level.sql에서 난이도(1~5)로
--     대체됐다 — "9개 옵션 드롭다운보다 빠른 단일 클릭"이라는 검수자 의견 반영.
--   - admin 참고 코멘트만 분류를 계속 썼는데, 12_add_admin_comment_difficulty.sql로
--     그쪽도 난이도로 바뀌었다.
--   - 그 시점부터 분류 컬럼·마스터 테이블은 어느 화면에서도 쓰이지 않고
--     "과거 데이터 표시용"으로만 남아 있었다. 남겨두면 §6 분석에서 난이도와
--     분류 두 체계를 계속 함께 고려해야 해서, 정리하는 쪽이 낫다고 판단했다.
--
-- ⚠️ 되돌릴 수 없다. 지우기 전 값은 backup/ 아래에 받아뒀다
--    (classification_types_2026-08-24.sql, classification_values_2026-08-24.tsv).
--    지워지는 값은 전부 검수자 A/B와 admin의 테스트 데이터였다
--    (검수자 의견 16건, admin 코멘트 3건, 분류 마스터 9행).
--
-- 순서가 중요하다: 두 테이블의 FK를 먼저 떼야 마스터 테이블을 지울 수 있다.
--
-- 여러 번 실행해도 안전하다 (이미 없으면 건너뜀).

-- ---- 1) ocr_review_comments.classification (FK + 인덱스 + 컬럼) ----
SET @fk = (
    SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ocr_review_comments'
      AND COLUMN_NAME = 'classification' AND REFERENCED_TABLE_NAME IS NOT NULL
    LIMIT 1
);
SET @sql = IF(@fk IS NULL, 'DO 0',
    CONCAT('ALTER TABLE ocr_review_comments DROP FOREIGN KEY ', @fk));
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @col = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ocr_review_comments'
      AND COLUMN_NAME = 'classification'
);
SET @sql = IF(@col = 0, 'DO 0',
    'ALTER TABLE ocr_review_comments DROP COLUMN classification');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ---- 2) ocr_admin_comments.classification ----
SET @fk = (
    SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ocr_admin_comments'
      AND COLUMN_NAME = 'classification' AND REFERENCED_TABLE_NAME IS NOT NULL
    LIMIT 1
);
SET @sql = IF(@fk IS NULL, 'DO 0',
    CONCAT('ALTER TABLE ocr_admin_comments DROP FOREIGN KEY ', @fk));
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @col = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ocr_admin_comments'
      AND COLUMN_NAME = 'classification'
);
SET @sql = IF(@col = 0, 'DO 0',
    'ALTER TABLE ocr_admin_comments DROP COLUMN classification');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ---- 3) 분류 마스터 테이블 ----
DROP TABLE IF EXISTS ocr_classification_types;

-- ---- 4) 수정 이력의 prev_classification ----
-- 이력 테이블은 이제 이전 값을 저장하지 않으므로(§8, 2026-08-24) 이 컬럼도 불필요하다.
SET @col = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ocr_review_edits'
      AND COLUMN_NAME = 'prev_classification'
);
SET @sql = IF(@col = 0, 'DO 0',
    'ALTER TABLE ocr_review_edits DROP COLUMN prev_classification');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
