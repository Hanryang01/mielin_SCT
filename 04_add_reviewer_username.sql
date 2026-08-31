-- ============================================================
-- 마이그레이션: ocr_reviewers.username 추가 (웹 로그인 아이디)
--
-- 03_ocr_review_schema.sql은 CREATE TABLE IF NOT EXISTS라서 이미 만들어진
-- DB에는 새 컬럼이 반영되지 않는다. 기존 DB에는 이 파일을 한 번 실행할 것.
-- (새로 만드는 DB는 03만 실행하면 username이 이미 들어 있다.)
--
-- 로그인 아이디를 email이 아니라 별도 컬럼으로 둔 이유: 실제 운영 계정이
-- admin / technonia01 / technonia02 처럼 이메일 형식이 아니다.
--
-- 계정 자체(비밀번호 해시 포함)는 SQL로 넣지 않는다 — bcrypt 해시를 SQL에서
-- 만들 수 없기 때문. `uv run python -m scripts.seed_reviewers`로 시딩할 것.
--
-- MySQL 8.4에는 ADD COLUMN IF NOT EXISTS가 없어서 information_schema를 보고
-- 분기한다 (여러 번 실행해도 안전).
-- ============================================================

SET @has_username := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_reviewers'
      AND COLUMN_NAME = 'username'
);

SET @sql := IF(@has_username = 0,
    "ALTER TABLE ocr_reviewers
       ADD COLUMN username VARCHAR(100) NOT NULL DEFAULT '' COMMENT '웹 로그인 아이디 (예: technonia01)' AFTER id",
    'SELECT ''username column already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 기존 행은 email 로컬파트로 임시 채움 (빈 값이면 UNIQUE 제약을 걸 수 없음).
-- 실제 계정명은 scripts/seed_reviewers.py가 덮어쓴다.
UPDATE ocr_reviewers SET username = SUBSTRING_INDEX(email, '@', 1) WHERE username = '';

SET @has_uq := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ocr_reviewers'
      AND INDEX_NAME = 'uq_reviewers_username'
);

SET @sql := IF(@has_uq = 0,
    'ALTER TABLE ocr_reviewers ADD UNIQUE KEY uq_reviewers_username (username)',
    'SELECT ''uq_reviewers_username already exists'' AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
