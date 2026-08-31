-- ============================================================
-- 자체(로컬) MySQL 데이터베이스: SCT import 데이터 스키마
--
-- 01_import_sct.sql Step2 조회 결과 + 로컬에 다운로드한 이미지 경로를 저장하는 테이블.
-- (assessment_id, drawing_id, answer_index) 조합으로 SCT 문항/답변 레코드를 식별하며,
-- Airflow DAG가 매일 재실행되어도 동일 레코드는 upsert(ON DUPLICATE KEY UPDATE) 된다.
-- ============================================================

CREATE TABLE IF NOT EXISTS sct_import_records (
    id                          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    -- 운영 서비스 원본 식별자
    assessment_id               BIGINT UNSIGNED NOT NULL,
    drawing_id                  BIGINT UNSIGNED NOT NULL,

    -- client 정보
    client_id                   BIGINT UNSIGNED NOT NULL,
    hospital_id                 BIGINT UNSIGNED NULL,
    client_name                 VARCHAR(255) NULL,
    birth_date                  DATE NULL,
    gender                      VARCHAR(16) NULL,
    sct_age_group               VARCHAR(16) NULL,

    -- SCT 문항/답변
    question_number             INT NOT NULL,
    sct_question                TEXT NULL,
    answer_index                INT NOT NULL,
    ocr_text                    MEDIUMTEXT NULL,
    ocr_failed                  TINYINT(1) NULL,
    sct_na_reason                VARCHAR(255) NULL,

    -- 원본 이미지 정보
    media_id                    BIGINT UNSIGNED NULL,
    media_file_type             VARCHAR(64) NULL,
    s3_key                      VARCHAR(1024) NULL,
    local_image_path            VARCHAR(1024) NULL,

    -- VLM 분석 정보
    vlm_job_id                  BIGINT UNSIGNED NULL,
    vlm_provider                VARCHAR(64) NULL,
    vlm_model                   VARCHAR(128) NULL,
    vlm_status                  VARCHAR(32) NULL,
    vlm_requested_at            DATETIME NULL,
    vlm_completed_at            DATETIME NULL,
    vlm_processing_time_ms      INT NULL,
    vlm_input_tokens            INT NULL,
    vlm_output_tokens           INT NULL,
    vlm_cache_write_tokens      INT NULL,
    vlm_cache_read_tokens       INT NULL,
    vlm_error_message           TEXT NULL,

    -- 운영 원본 타임스탬프 (assessment_drawings.created_at / updated_at)
    source_created_at           DATETIME NULL,
    source_updated_at           DATETIME NULL,

    -- 적재(import) 메타데이터
    imported_at                 DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_assessment_drawing_answer (assessment_id, drawing_id, answer_index),
    KEY idx_client_id (client_id),
    KEY idx_hospital_id (hospital_id),
    KEY idx_assessment_id (assessment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
