-- ============================================================
-- OCR 검수 시스템 스키마
--
-- "OCR 검토용.sql" 초안을 정리한 버전. 자세한 워크플로우는
-- "OCR 검수 시나리오.md" 참고.
--
-- 중요: sct_import_records는 이 DB에 없다 (mielin 웨어하우스 DB에 있음,
-- 우리가 관리하는 DB가 아니라서 여기에 테이블을 얹지 않는다). 그래서 아래
-- ocr_review_comments는 sct_import_records를 실제 FK가 아니라
-- (assessment_id, drawing_id, answer_index) 자연 키로만 "논리적으로" 참조한다.
-- auto_increment id는 DB마다 값이 달라질 수 있어 참조 키로 쓰지 않는다.
--
-- 적용 시 주의 (Windows): mysql CLI의 기본 문자셋이 utf8mb4가 아니라서, 옵션
-- 없이 실행하면 아래 분류 라벨(한글) INSERT가 ERROR 1366으로 실패한다.
-- 반드시 --default-character-set=utf8mb4 를 붙일 것:
--
--   mysql --default-character-set=utf8mb4 -u root -p ocr_review < 03_ocr_review_schema.sql
--
-- 적용 순서:
--   - 새 DB          : 이 파일만 실행하면 된다 (username 컬럼 포함).
--   - 기존 DB        : 이 파일은 CREATE TABLE IF NOT EXISTS라서 이미 만들어진
--                      테이블에 새 컬럼을 반영하지 못한다. username 컬럼이
--                      없다면 04_add_reviewer_username.sql을,
--                      ocr_review_edits 테이블이 없다면
--                      05_add_review_edits.sql을 한 번 실행할 것.
--   - 계정 시딩      : 비밀번호를 bcrypt로 해싱해야 해서 SQL로 넣지 않는다.
--                      `uv run python -m scripts.seed_reviewers` 사용.
-- ============================================================


-- ============================================================
-- 1. 검수자 계정 (웹 로그인 계정 소스)
--
-- 이 테이블이 곧 웹 로그인 계정이다. 로그인 계정을 따로 두지 않는 이유:
-- 아래 ocr_review_comments.reviewer_id와 ocr_admin_comments.admin_id가 이미
-- 이 테이블을 FK로 참조하므로, 계정이 분리되면 "로그인한 사람"과 "의견을
-- 남긴 사람"의 매핑을 이중으로 관리해야 한다. 세션에 reviewer_id를 담아두면
-- 그 매핑이 아예 필요 없어진다.
-- ============================================================
CREATE TABLE IF NOT EXISTS ocr_reviewers (
    id                    INT NOT NULL AUTO_INCREMENT,
    username              VARCHAR(100) NOT NULL COMMENT '웹 로그인 아이디 (예: technonia01). email과 별개 — 실제 운영 계정이 이메일 형식이 아니라서 분리했다',
    email                 VARCHAR(255) NOT NULL COMMENT '연락용 이메일 (로그인 아이디는 username을 쓴다)',
    password_hash         VARCHAR(255) NOT NULL COMMENT 'bcrypt 해시 (평문 저장 금지). scripts/seed_reviewers.py가 생성한다',
    name                  VARCHAR(100) NOT NULL COMMENT '검수자명',
    phone                 VARCHAR(50) DEFAULT NULL,
    role                  VARCHAR(50) NOT NULL DEFAULT 'annotator' COMMENT '역할 (annotator/admin). admin만 Admin 열람 화면(§4.5)에 접근 가능 — 단, "최종 채택" 권한은 아님(그런 개념 자체가 없음)',
    is_active             TINYINT(1) NOT NULL DEFAULT 1,
    last_login_at         DATETIME DEFAULT NULL,
    last_login_ip         VARCHAR(50) DEFAULT NULL,
    failed_login_count    INT NOT NULL DEFAULT 0,
    locked_at             DATETIME DEFAULT NULL,
    password_changed_at   DATETIME DEFAULT NULL,
    registered_by_id      INT DEFAULT NULL COMMENT '이 계정을 등록한 관리자 (ocr_reviewers.id, 셀프가입이면 NULL)',
    created_at            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_deleted            TINYINT(1) NOT NULL DEFAULT 0,
    deleted_at            DATETIME DEFAULT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_reviewers_username (username),
    UNIQUE KEY uq_reviewers_email (email),
    KEY ix_reviewers_registered_by_id (registered_by_id),
    CONSTRAINT fk_reviewers_registered_by
        FOREIGN KEY (registered_by_id) REFERENCES ocr_reviewers (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='OCR 검수 작업자 계정 (웹 로그인 겸용)';


-- ============================================================
-- 2. 분류 코드 마스터
--
-- "개선불가/개선가능" 그룹 소속을 여기서만 관리한다. 새 유형이 필요하면
-- INSERT 한 줄만 추가하면 되고(배포/스키마 변경 불필요), 기존 값의 의미는
-- 절대 안 바뀐다. 제외 규칙 등 그룹 판단은 항상 group_name으로 한다
-- (숫자 레벨 비교 금지).
-- ============================================================
CREATE TABLE IF NOT EXISTS ocr_classification_types (
    code            VARCHAR(40) NOT NULL COMMENT '고유 코드, ocr_review_comments.classification에서 참조',
    group_name      VARCHAR(20) NOT NULL COMMENT 'non_improvable(개선불가) 또는 improvable(개선가능)',
    label           VARCHAR(100) NOT NULL COMMENT '화면 표시용 한글 라벨',
    display_order   INT NOT NULL COMMENT '검수자 화면 드롭다운/버튼 노출 순서',
    is_active       TINYINT(1) NOT NULL DEFAULT 1,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (code),
    KEY ix_classification_group (group_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='분류 코드 마스터 (개선불가/개선가능 그룹 소속 정의)';

INSERT INTO ocr_classification_types (code, group_name, label, display_order) VALUES
    ('unreadable',              'non_improvable', '읽기 불가 (알아볼 수 없는 필기)', 1),
    ('not_text',                'non_improvable', '텍스트 아님',                     2),
    ('ai_error_char_confusion', 'improvable',     '비슷한 글자 오인식',              3),
    ('ai_error_correction',     'improvable',     '수정·덧쓰기 처리 오류',           4),
    ('ai_error_multiline',      'improvable',     '여러 줄 응답 처리 오류',          5),
    ('ai_error_mixed_script',   'improvable',     '영문·숫자·기호 혼합 오류',        6),
    ('ai_error_negation',       'improvable',     '부정 표현·의미 반전 오류',        7),
    ('ai_error_length_outlier', 'improvable',     '매우 짧거나 긴 응답 오류',        8),
    ('ai_error_other',          'improvable',     '기타',                            9)
ON DUPLICATE KEY UPDATE
    group_name = VALUES(group_name),
    label = VALUES(label),
    display_order = VALUES(display_order);


-- ============================================================
-- 3. 검수 의견 (핵심 테이블)
--
-- 레코드 1건 = 검수자 1명이 SCT 답변 1건에 대해 남긴 의견 1건.
-- 같은 답변에 여러 검수자가 각자 독립적으로 입력하며, 합의/최종채택 개념 없음
-- (둘의 classification이 달라도 그대로 각자 저장).
--
-- review_type='normal_check'  : 패스 (OCR과 이미지가 일치한다고 판단, 타이핑 불필요)
-- review_type='transcription' : 타이핑 (확신 안 서서 직접 입력, 이어서 분류 선택)
--
-- 둘 중 어느 쪽이든, 같은 답변에 대해 독립된 검수자 2명의 처리가 모여야
-- 완료로 취급한다 (한 명의 패스만으로 완료되지 않음 — §5 뷰 참고).
--
-- vlm_model: sct_import_records(mielin DB)는 우리 DB와 분리되어 있어 리뷰
-- 시점 이후에 JOIN으로 다시 값을 가져올 수 있다는 보장이 없다(다른 DB일 수
-- 있음). 그래서 검수자가 처리하는 시점에 그 답변의 vlm_model 값을 스냅샷으로
-- 같이 저장해둔다 — 나중에 모델 버전별 정확도 추이를 비교할 때 이 컬럼만으로
-- 바로 집계할 수 있게 하기 위함이다 (§6 참고).
-- ============================================================
CREATE TABLE IF NOT EXISTS ocr_review_comments (
    id                  BIGINT NOT NULL AUTO_INCREMENT,

    -- sct_import_records(mielin DB) 자연 키 참조 — FK 아님 (다른 DB에 있음)
    assessment_id       BIGINT UNSIGNED NOT NULL,
    drawing_id          BIGINT UNSIGNED NOT NULL,
    answer_index        INT NOT NULL,

    -- 리뷰 시점의 sct_import_records.vlm_model 스냅샷 (FK 아님, 값 그대로 복사)
    vlm_model           VARCHAR(128) DEFAULT NULL COMMENT '이 답변의 OCR을 생성한 VLM 모델 (sct_import_records.vlm_model 스냅샷, 모델별 정확도 추이 분석용)',

    reviewer_id         INT NOT NULL COMMENT '작성한 검수자 (ocr_reviewers.id). 클라이언트가 보내는 값이 아니라 로그인 세션에서 채운다',
    review_type         ENUM('normal_check', 'transcription') NOT NULL,

    -- review_type='transcription'일 때만 사용
    typed_text          MEDIUMTEXT DEFAULT NULL COMMENT '검수자가 이미지를 보고 직접 입력한 텍스트 (OCR 결과 보지 않고 독립 판단)',

    -- typed_text가 OCR 텍스트와 다를 때만 채워짐 (일치하면 분류 불필요, 정상으로 간주)
    classification      VARCHAR(40) DEFAULT NULL COMMENT 'ocr_classification_types.code 참조',

    comment             VARCHAR(1000) DEFAULT NULL COMMENT '자유 코멘트 (특히 기타 분류 시)',

    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    -- 동일 검수자가 같은 답변에 두 번 의견을 남길 수 없음 (독립성 보장의 핵심 제약)
    UNIQUE KEY uq_review_per_reviewer (assessment_id, drawing_id, answer_index, reviewer_id),

    KEY ix_review_reviewer_id (reviewer_id),
    KEY ix_review_classification (classification),
    KEY ix_review_type (review_type),
    KEY ix_review_vlm_model (vlm_model),

    CONSTRAINT fk_review_reviewer
        FOREIGN KEY (reviewer_id) REFERENCES ocr_reviewers (id),
    CONSTRAINT fk_review_classification
        FOREIGN KEY (classification) REFERENCES ocr_classification_types (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='검수자별 SCT 답변 검토 의견 (여러 명이 각자 독립적으로 남김)';


-- ============================================================
-- 3-1. 검수 의견 수정 이력 (append-only)
--
-- 검수자가 자기 타이핑 내용의 오타 등을 나중에 고칠 수 있게 하면서도, §1의
-- "각자의 독립적인 의견을 있는 그대로, 수정 없이 보존" 원칙을 지키기 위한
-- 테이블이다. ocr_review_comments는 "현재 값"을 들고 있고, 수정이 일어나면
-- **수정 직전 값**을 여기에 append한다 (UPDATE/DELETE 하지 않는다).
--
-- was_completed: 수정 시점에 그 레코드가 이미 완료(2명 처리)였는가. 1이면
-- 상대방 의견이 공개된 뒤의 수정이라 "독립적인 판단"으로 보기 어렵다 —
-- 분석할 때 따로 취급해야 한다.
-- ============================================================
CREATE TABLE IF NOT EXISTS ocr_review_edits (
    id                  BIGINT NOT NULL AUTO_INCREMENT,

    review_id           BIGINT NOT NULL COMMENT '수정된 ocr_review_comments.id',
    edited_by_id        INT NOT NULL COMMENT '수정한 사람 (본인만 수정 가능하므로 원 작성자와 같다)',

    prev_typed_text     MEDIUMTEXT DEFAULT NULL,
    prev_classification VARCHAR(40) DEFAULT NULL,
    prev_comment        VARCHAR(1000) DEFAULT NULL,

    was_completed       TINYINT(1) NOT NULL DEFAULT 0
        COMMENT '수정 시점에 완료(2명 이상) 상태였는가. 1이면 상대 의견을 본 뒤의 수정',

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


-- ============================================================
-- 4. Admin 코멘트 (열람 중 참고용 메모, 최종 결정 아님)
--
-- OCR 검수에는 정해진 정답이 없다는 전제 하에, admin은 검수자 A/B의 의견 중
-- 하나를 채택하거나 그룹을 확정하는 결정권자가 아니다. 그저 쌓인 의견을
-- 열람하다가 필요하면 메모를 남기는 역할일 뿐이다.
--
-- 그래서 검수자 원본(ocr_review_comments)과 완전히 분리된 별도 테이블로 둔다:
--   - 검수자 A/B의 원본 classification은 절대 덮어쓰거나 바꾸지 않는다
--     (블라인드 독립 판단이라는 데이터 자체가 훼손되면 안 됨)
--   - 코멘트는 완료(completed) 상태 판정에 전혀 영향을 주지 않는다
--     (§5 뷰는 이 테이블을 아예 참조하지 않음)
--   - 레코드당 코멘트 개수 제한 없음 (같은 admin이 여러 번 남기거나, 여러
--     admin이 각자 남길 수 있음 — 단일 확정값이 아니므로 유니크 제약 없음)
-- ============================================================
CREATE TABLE IF NOT EXISTS ocr_admin_comments (
    id                  BIGINT NOT NULL AUTO_INCREMENT,

    -- sct_import_records(mielin DB) 자연 키 참조 — FK 아님 (다른 DB에 있음)
    assessment_id       BIGINT UNSIGNED NOT NULL,
    drawing_id          BIGINT UNSIGNED NOT NULL,
    answer_index        INT NOT NULL,

    admin_id            INT NOT NULL COMMENT '코멘트를 남긴 admin 계정 (ocr_reviewers.id, role=admin). 로그인 세션에서 채운다',
    comment             VARCHAR(1000) NOT NULL COMMENT '검수자 의견에 대한 참고용 메모 (최종 결정 아님)',

    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    KEY ix_admin_comment_record (assessment_id, drawing_id, answer_index),
    KEY ix_admin_comment_admin_id (admin_id),

    CONSTRAINT fk_admin_comment_admin
        FOREIGN KEY (admin_id) REFERENCES ocr_reviewers (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='검수자 의견 열람 중 admin이 남기는 참고용 코멘트 (최종 결정 아님)';


-- ============================================================
-- 5. 검토 상태 뷰 (참고/편의용)
--
-- "미검토만 보기" 필터에 사용할 수 있는 계산된 상태.
-- 완료 판정은 오직 "독립된 검수자 몇 명이 처리했는가"만 본다 — 패스/타이핑
-- 구분, 분류 그룹 일치 여부, admin 코멘트 존재 여부는 전혀 관여하지 않는다
-- (admin은 결정권자가 아니므로 ocr_admin_comments는 이 뷰에서 참조하지 않음).
--
-- 주의 1: ocr_review_comments에 행이 하나도 없는(완전 미검토) 답변은 이 뷰에
-- 아예 나타나지 않는다 (GROUP BY 특성상). 조회 결과에 없으면 'pending'으로
-- 취급해야 한다.
--
-- 주의 2: sct_import_records는 다른 DB(mielin)에 있어서 이 뷰와 SQL JOIN을
-- 할 수 없다. 두 쪽을 합치는 일은 애플리케이션 레이어에서 자연 키로 한다
-- (app/main.py). "미검토만 보기"도 JOIN이 아니라, 내가 처리한 키를 여기서
-- 뽑아 원본 쿼리에서 NOT IN으로 제외하는 방식이다.
--
-- 주의 3: 검수 화면의 목록 조회는 이 뷰를 쓰지 않는다. 블라인드 원칙(§4.3)상
-- "내가 제출했는지"에 따라 응답을 달리해야 해서, ocr_review_comments의 행을
-- 직접 읽어 요청자 기준으로 상태를 계산한다. 이 뷰는 단건 상태 조회와 admin
-- 화면의 완료 레코드 스캔에 쓴다.
--
-- 상태값:
--   completed            : 독립된 검수자 2명 이상이 처리 완료 (패스/타이핑 무관)
--   needs_second_opinion : 아직 1명만 처리함
--
-- 데이터가 많아져서 이 뷰가 느려지면, 같은 로직을 별도 상태 테이블로 옮기고
-- ocr_review_comments에 INSERT될 때 애플리케이션이 그 테이블을 갱신하는
-- 방식으로 바꿀 수 있다 (지금은 뷰로 시작).
-- ============================================================
CREATE OR REPLACE VIEW v_sct_review_status AS
SELECT
    c.assessment_id,
    c.drawing_id,
    c.answer_index,
    COUNT(*) AS review_count,
    CASE
        WHEN COUNT(*) >= 2 THEN 'completed'
        ELSE 'needs_second_opinion'
    END      AS status
FROM ocr_review_comments c
GROUP BY c.assessment_id, c.drawing_id, c.answer_index;


-- ============================================================
-- 6. 로우데이터 (참고)
--
-- 이 시스템은 "AI 개선 작업에 쓸 데이터"를 미리 걸러서 만들어두지 않는다.
-- ocr_review_comments(검수자 의견)와 ocr_admin_comments(admin 코멘트)에
-- 빠짐없이 쌓아두기만 하고, 그중 무엇을 가져다 쓸지는 이 시스템이 정하지
-- 않는다.
--
-- 데이터가 충분히 쌓인 뒤, admin/개발자가 그 시점에 필요한 기준(예: 두
-- 검수자 모두 개선불가로 분류한 것만 제외, 특정 세부유형만 추출, 검수자
-- 간 의견이 갈린 것만 조회, vlm_model별 정확도 추이 비교 등)으로 직접
-- 쿼리를 작성해서 사용한다. 미리 고정된 참고 쿼리를 이 파일에 두지 않는다
-- (OCR 검수 시나리오.md §6 참고).
-- ============================================================
