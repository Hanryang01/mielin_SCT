-- ============================================================
-- 마이그레이션: 부정 표현(죽음/자살/우울 등) 자동 감지 체크박스
--
-- 실제 검수자 의견: "OCR 텍스트에 부정 표현이 있으면 자동으로 표시하고,
-- 검수자가 직접 체크/해제도 할 수 있게 해달라"는 요청 (OCR 검수
-- 시나리오.md §5 참고). 기존 분류 'ai_error_negation'(AI가 부정 표현을
-- 잘못 인식한 오류)과는 완전히 다른 축이다 — 이건 "내용 자체에 위험
-- 신호가 있는가"를 놓치지 않기 위한 것이라, classification 폐지(08번
-- 마이그레이션)와 무관하게 별도 컬럼으로 둔다.
--
-- 패스(normal_check)/타이핑(transcription) 모두에 적용된다 — OCR 텍스트
-- 자체에 위험 신호가 있으면 검수자가 패스하더라도 놓치면 안 되기 때문이다.
--
-- 키워드는 코드 배포 없이 추가/비활성화할 수 있도록 마스터 테이블로 둔다
-- (ocr_classification_types와 같은 패턴). 서버가 목록 조회 시 OCR
-- 텍스트를 이 키워드로 매칭해 auto_negative_flag를 미리 계산해 내려주고,
-- 화면은 그 값으로 체크박스를 미리 켜둔 채 보여준다 (app/main.py).
--
-- 적용:
--   mysql --default-character-set=utf8mb4 -u root -p ocr_review < 09_add_review_negative_expression.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS ocr_negative_keywords (
    id              INT NOT NULL AUTO_INCREMENT,
    keyword         VARCHAR(50) NOT NULL COMMENT '부정 표현 자동 감지용 키워드 (부분 문자열 매칭)',
    is_active       TINYINT(1) NOT NULL DEFAULT 1,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_negative_keyword (keyword)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='부정/위험 표현 자동 감지 키워드 마스터 (배포 없이 추가/비활성화 가능)';

INSERT INTO ocr_negative_keywords (keyword) VALUES
    ('죽음'), ('죽고'), ('죽어'), ('죽을'),
    ('자살'),
    ('우울'),
    ('죽이고'), ('죽이는'),
    ('살고싶지'), ('살기싫')
ON DUPLICATE KEY UPDATE keyword = VALUES(keyword);


SET @has_col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_review_comments'
      AND COLUMN_NAME = 'contains_negative_expression'
);

SET @sql := IF(@has_col = 0,
    "ALTER TABLE ocr_review_comments
       ADD COLUMN contains_negative_expression TINYINT(1) NOT NULL DEFAULT 0
           COMMENT '죽음/자살/우울 등 부정 표현 포함 여부. 자동 감지값을 기본으로 검수자가 직접 켜고 끌 수 있음'
           AFTER ocr_difficulty_level",
    'SELECT ''contains_negative_expression column already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @has_key := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_review_comments'
      AND INDEX_NAME = 'ix_review_negative_expression'
);

SET @sql := IF(@has_key = 0,
    'ALTER TABLE ocr_review_comments ADD KEY ix_review_negative_expression (contains_negative_expression)',
    'SELECT ''ix_review_negative_expression already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- ocr_review_edits에도 수정 직전 값을 남긴다 (§1 "수정 없이 보존" 원칙).
SET @has_edit_col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_review_edits'
      AND COLUMN_NAME = 'prev_contains_negative_expression'
);

SET @sql := IF(@has_edit_col = 0,
    "ALTER TABLE ocr_review_edits
       ADD COLUMN prev_contains_negative_expression TINYINT(1) DEFAULT NULL AFTER prev_ocr_difficulty_level",
    'SELECT ''prev_contains_negative_expression column already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
