-- ============================================================
-- 마이그레이션: admin 코멘트를 "검수자당 1건, 수정 가능"으로 변경
--
-- 지금까지는 같은 admin이 같은 레코드에 코멘트를 여러 번 남길 수 있었다
-- (누적 로그 방식). 검수자가 자기 의견을 수정하는 것과 같은 방식으로
-- 바꾸기로 해서 — admin당 레코드당 1건만 남기고, 그 1건을 계속 수정한다.
--
-- 먼저 기존에 쌓인 중복(같은 admin이 같은 레코드에 여러 번 남긴 것)을
-- 최신 것만 남기고 정리한 뒤 유니크 제약을 건다. 이미 정리된 DB에
-- 다시 실행해도 안전하다(삭제할 중복이 없으면 아무 일도 안 함).
--
-- 적용:
--   mysql --default-character-set=utf8mb4 -u root -p ocr_review < 07_add_admin_comment_unique.sql
-- ============================================================

DELETE t1 FROM ocr_admin_comments t1
JOIN ocr_admin_comments t2
  ON t1.assessment_id = t2.assessment_id
 AND t1.drawing_id = t2.drawing_id
 AND t1.answer_index = t2.answer_index
 AND t1.admin_id = t2.admin_id
 AND t1.id < t2.id;

SET @has_uq := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_admin_comments'
      AND INDEX_NAME = 'uq_admin_comment_per_admin'
);

SET @sql := IF(@has_uq = 0,
    'ALTER TABLE ocr_admin_comments
       ADD UNIQUE KEY uq_admin_comment_per_admin (assessment_id, drawing_id, answer_index, admin_id)',
    'SELECT ''uq_admin_comment_per_admin already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
