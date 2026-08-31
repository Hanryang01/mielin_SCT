-- 11_add_spacing_diff.sql
-- OCR 검수 시나리오.md §5.3 (2026-08-24)
--
-- 글자 비교에서 **공백을 제외**하고, 띄어쓰기 차이는 별도 컬럼으로 분리한다.
--
-- 배경: 공백을 한 글자로 세면 띄어쓰기만 다른 입력이 "OCR이 틀렸다"로 집계된다.
-- 실제로 같은 답변을 두 검수자가 각각 "있어야한다" / "있어야 한다"로 적어
-- 둘 다 차이 13자로 기록된 사례가 있었다. OCR의 띄어쓰기 오류는 글자 오인식과
-- 성격이 다르고 검수자마다 습관도 달라, 글자 정확도 지표에 섞이면 안 된다.
--
-- 다만 띄어쓰기도 OCR 품질의 일부라 버리지 않고 여기에 남긴다 — "OCR이
-- 띄어쓰기를 얼마나 틀리는가"를 §6에서 따로 볼 수 있어야 하기 때문이다.
--
-- 여러 번 실행해도 안전하다 (이미 있으면 건너뜀).

SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_review_comments'
      AND COLUMN_NAME = 'spacing_diff'
);

SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE ocr_review_comments
       ADD COLUMN spacing_diff TINYINT(1) NOT NULL DEFAULT 0
       COMMENT ''띄어쓰기 패턴이 OCR과 다른가 (글자 차이와 별개, §5.3 2026-08-24)''
       AFTER ocr_diff_char_count',
    'SELECT ''spacing_diff 컬럼이 이미 있습니다 — 건너뜁니다'' AS note');

PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 기존 행은 0(띄어쓰기 차이 없음)으로 남는다. 과거 diff는 공백을 포함해
-- 계산된 값이라 지금 기준과 다르지만, 재계산하면 "그때 무엇과 비교했는가"가
-- 바뀌므로 건드리지 않는다 — 분석 시 created_at으로 구분하면 된다.
