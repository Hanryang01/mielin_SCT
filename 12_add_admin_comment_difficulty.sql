-- 12_add_admin_comment_difficulty.sql
-- OCR 검수 시나리오.md §4.5 (2026-08-24)
--
-- 관리자 코멘트에 **난이도(1~5)** 를 추가한다. 화면의 분류(classification)
-- 드롭다운을 대체하는 값이다 — 검수자 쪽이 분류를 난이도로 바꾼 것과 같은 흐름
-- (08_add_review_difficulty_level.sql).
--
-- **검수자 난이도와 같은 통에 넣지 않는 이유**: 관리자는 두 검수자의 의견을
-- 모두 본 뒤에 판단한다(§4.5 — 의견이 하나라도 있으면 공개). 즉 비블라인드
-- 판정이라, §1의 "독립 판단"이 전제인 ocr_review_comments.ocr_difficulty_level과
-- 섞으면 "독립적으로 매긴 난이도 N개"라는 통계가 오염된다. 그래서 별도 컬럼에
-- 담고, 용도는 **두 검수자가 다른 난이도를 냈을 때의 중재값**으로 삼는다.
--
-- 분석 시 "확정 난이도"는 이 우선순위로 계산한다:
--   1) 관리자 난이도가 있으면 그 값
--   2) 두 검수자가 일치하면 그 값
--   3) 불일치하고 관리자도 없으면 미확정
--
-- classification 컬럼은 남겨두되 새로 채우지 않는다 — 과거 데이터 표시용
-- (검수자 쪽 classification과 같은 처리 방식).
--
-- 완료 판정에는 영향이 없다: ocr_admin_comments는 v_sct_review_status 뷰에서
-- 아예 참조되지 않는다(03_ocr_review_schema.sql §5).
--
-- 여러 번 실행해도 안전하다 (이미 있으면 건너뜀).

SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_admin_comments'
      AND COLUMN_NAME = 'difficulty_level'
);

SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE ocr_admin_comments
       ADD COLUMN difficulty_level TINYINT NULL
       COMMENT ''관리자가 매긴 OCR 난이도 1~5 (비블라인드 중재값, §4.5). 검수자 난이도와 별개''
       AFTER comment,
       ADD KEY ix_admin_comment_difficulty (difficulty_level)',
    'SELECT ''difficulty_level 컬럼이 이미 있습니다 — 건너뜁니다'' AS note');

PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
