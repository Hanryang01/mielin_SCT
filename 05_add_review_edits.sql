-- ============================================================
-- 마이그레이션: 검수 의견 수정 이력 (ocr_review_edits)
--
-- 검수자가 자기 타이핑 내용을 나중에 고칠 수 있게 하면서도, OCR 검수 시나리오
-- §1의 "각자의 독립적인 의견을 있는 그대로, 수정 없이 보존하는 것이 최우선"을
-- 지키기 위한 테이블이다.
--
-- ocr_review_comments의 행은 "현재 값"을 들고 있고, 수정이 일어나면 **수정 직전
-- 값**을 이 테이블에 append한다. 그래서 원래 독립 판단이 무엇이었는지는 영구히
-- 남는다 (append-only, UPDATE/DELETE 하지 않는다).
--
-- 특히 중요한 경우: 레코드가 이미 완료(2명 처리)된 뒤의 수정이다. 그때 고친
-- 값은 "독립적인 판단"이 아닐 수 있으므로, 분석할 때 was_completed = 1인
-- 수정은 따로 취급해야 한다.
--
-- [2026-08-21 갱신] 완료 후에도 상대방 의견 자체는 절대 공개되지 않도록
-- 서버가 강제한다(app/main.py의 _blind_state) — 그래서 "상대 의견을 보고
-- 맞춰 고쳤다"는 위험은 없다. was_completed는 "완료됐다는 사실 자체를
-- 알고 있었는가"만 남기는 값이다.
--
-- 적용:
--   mysql --default-character-set=utf8mb4 -u root -p ocr_review < 05_add_review_edits.sql
-- (여러 번 실행해도 안전 — CREATE TABLE IF NOT EXISTS)
-- ============================================================

CREATE TABLE IF NOT EXISTS ocr_review_edits (
    id                  BIGINT NOT NULL AUTO_INCREMENT,

    review_id           BIGINT NOT NULL COMMENT '수정된 ocr_review_comments.id',
    edited_by_id        INT NOT NULL COMMENT '수정한 사람 (본인만 수정 가능하므로 원 작성자와 같다)',

    -- 수정 직전 값 스냅샷
    prev_typed_text     MEDIUMTEXT DEFAULT NULL,
    prev_classification VARCHAR(40) DEFAULT NULL,
    prev_comment        VARCHAR(1000) DEFAULT NULL,

    was_completed       TINYINT(1) NOT NULL DEFAULT 0
        COMMENT '수정 시점에 이 레코드가 완료(2명 이상) 상태였는가. 1이면 상대 의견을 본 뒤의 수정이라 독립 판단으로 보기 어렵다',

    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY ix_review_edits_review_id (review_id),
    KEY ix_review_edits_editor (edited_by_id),
    KEY ix_review_edits_was_completed (was_completed),

    CONSTRAINT fk_review_edits_review
        FOREIGN KEY (review_id) REFERENCES ocr_review_comments (id) ON DELETE CASCADE,
    CONSTRAINT fk_review_edits_editor
        FOREIGN KEY (edited_by_id) REFERENCES ocr_reviewers (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='검수 의견 수정 이력 (append-only, 원본 독립 판단 보존용)';
