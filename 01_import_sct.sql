-- ============================================================
-- OCR 데이터웨어하우스 Airflow DAG용 쿼리 모음
--
-- DAG 흐름:
--   Step 1) 오늘 진행된 assessment 중 SCT가 포함된 assessment_id 목록 추출
--   Step 2) Step 1의 assessment_id 목록을 기준으로 상세 정보 조회
--           (02_sct_analysis_queries.sql의 "(B) 삭제되지 않은 평가 중 SCT가 있는
--            항목 상세 조회" 쿼리를 기반으로 하되, VLM 분석 정보(provider/model 등)를
--            함께 조회)
--
-- 기준 시점: assessments.assessment_date (검사 진행일, KST)
-- ============================================================


-- ────────────────────────────────────────────────────────────────────────────
-- Step 1) 오늘 진행한 assessment 중 SCT를 포함하는 assessment_id 목록
--   - 삭제되지 않은 assessment (a.is_deleted = 0)
--   - assessment_date가 오늘(KST)인 건
--   - assessment_drawings에 test_type = 'sct' 인 항목이 하나라도 있는 assessment
--   - Airflow PythonOperator/SQLOperator에서 이 쿼리 결과의 assessment_id 리스트를
--     XCom으로 넘겨 Step 2 쿼리의 IN 절 파라미터로 사용
-- ────────────────────────────────────────────────────────────────────────────
SELECT DISTINCT a.id AS assessment_id
FROM assessments a
JOIN assessment_drawings ad
    ON ad.assessment_id = a.id
   AND ad.test_type = 'sct'
WHERE a.is_deleted = 0
  AND a.assessment_date >= CURDATE()
  AND a.assessment_date <  CURDATE() + INTERVAL 1 DAY
ORDER BY a.id;


-- ────────────────────────────────────────────────────────────────────────────
-- Step 2) Step 1에서 추출한 assessment_id 목록 기준 상세 정보 조회
--   - 02_sct_analysis_queries.sql (B) 쿼리 기반: client 정보, SCT 문항/답변/OCR 결과,
--     이미지 S3 경로
--   - vlm_analysis_jobs를 LEFT JOIN하여 해당 SCT 문항(drawing_id 매칭)을 처리한
--     VLM 모델 정보(provider, model, 토큰, 처리시간, 상태)를 함께 조회
--   - :assessment_ids 는 Airflow 템플릿(Jinja) 또는 파라미터 바인딩으로 Step 1의
--     결과(콤마로 구분된 id 목록)를 주입하는 placeholder
-- ────────────────────────────────────────────────────────────────────────────
SELECT
    a.id                                                                      AS assessment_id,

    -- client 정보
    c.id                                                                      AS client_id,
    c.hospital_id,
    c.name                                                                    AS client_name,
    c.birth_date,
    c.gender,

    -- SCT age_group 계산 — 운영 서버의 resolve_sct_age_group() 판정 기준표(2026-09-08
    -- 제공)를 그대로 옮긴다. y = 기준 연도(여기서는 검사 진행일 assessment_date의
    -- 연도), birth_year = 생년.
    --   아동   : birth_year >= y - 12  (연도 차이 12 이하 → 2026년 기준 2014년생 이후)
    --   청소년 : 위가 아니고 birth_year >= y - 18 (연도 차이 13~18 → 2008~2013년생)
    --   성인   : 위 두 조건에 해당하지 않음 (연도 차이 19 이상 → 2007년생 이전)
    --   생년월일 없음 : 성인으로 처리
    --
    -- **만 나이가 아니라 "연도 차이"다** — 생일이 그 해에 지났는지는 안 본다. 한 번은
    -- 이걸 "생일 미도래분 보정 안 된 버그"로 보고 TIMESTAMPDIFF(만 나이)로 고쳤었는데
    -- (2026-09-07), 운영 기준표를 보니 애초에 만 나이가 아니라 연도 차이 기준이었고
    -- 경계값도 달랐다(운영은 12/18 이하, 예전 코드는 11/17 이하) — TIMESTAMPDIFF로
    -- 바꾼 것 자체가 운영 기준과 어긋나는 방향이었다. 2026-09-08 기준 48개
    -- assessment_id(1,872건)가 이 기준표와 달라 재수정했다(assessment_id=2743
    -- 확인 과정에서 발견).
    CASE
        WHEN c.birth_date IS NULL THEN '성인'
        WHEN YEAR(c.birth_date) >= YEAR(a.assessment_date) - 12 THEN '아동'
        WHEN YEAR(c.birth_date) >= YEAR(a.assessment_date) - 18 THEN '청소년'
        ELSE '성인'
    END                                                                       AS sct_age_group,

    ad.id                                                                     AS drawing_id,
    ad.sequence_number                                                        AS question_number,
    ics.prompt_text                                                           AS sct_question,
    ad.answer_index,
    ad.ocr_text,
    ad.ocr_failed,
    ad.sct_na_reason,

    -- 이미지 S3 경로 (result_image_media_id -> media.file_path)
    m.id                                                                      AS media_id,
    m.file_type                                                               AS media_file_type,
    m.file_path                                                               AS s3_key,

    -- VLM 분석 정보 (해당 SCT 문항의 OCR을 처리한 job; drawing_id로 매칭)
    vaj.id                                                                    AS vlm_job_id,
    vaj.provider                                                              AS vlm_provider,
    vaj.model                                                                 AS vlm_model,
    vaj.status                                                                AS vlm_status,
    vaj.requested_at                                                          AS vlm_requested_at,
    vaj.completed_at                                                          AS vlm_completed_at,
    vaj.processing_time_ms                                                    AS vlm_processing_time_ms,
    COALESCE(vaj.step1_input_tokens, 0)                                       AS vlm_input_tokens,
    COALESCE(vaj.step1_output_tokens, 0)                                      AS vlm_output_tokens,
    COALESCE(vaj.step1_cache_write_tokens, 0)                                 AS vlm_cache_write_tokens,
    COALESCE(vaj.step1_cache_read_tokens, 0)                                  AS vlm_cache_read_tokens,
    vaj.error_message                                                         AS vlm_error_message,

    ad.created_at,
    ad.updated_at
FROM assessments a
JOIN clients c
    ON c.id = a.client_id
JOIN assessment_drawings ad
    ON ad.assessment_id = a.id
   AND ad.test_type = 'sct'
LEFT JOIN inspection_content_sct ics
    ON ics.hospital_id = c.hospital_id
   AND ics.question_number = ad.sequence_number
   AND ics.age_group = CASE
        WHEN c.birth_date IS NULL THEN '성인'
        WHEN YEAR(c.birth_date) >= YEAR(a.assessment_date) - 12 THEN '아동'
        WHEN YEAR(c.birth_date) >= YEAR(a.assessment_date) - 18 THEN '청소년'
        ELSE '성인'
   END
   AND ics.is_active = 1
   AND ics.is_deleted = 0
LEFT JOIN media m
    ON m.id = ad.result_image_media_id
LEFT JOIN vlm_analysis_jobs vaj
    ON vaj.drawing_id = ad.id
   AND vaj.test_type = 'sct'
WHERE a.is_deleted = 0
  AND a.id IN (:assessment_ids)
ORDER BY a.id, ad.sequence_number, ad.answer_index;
