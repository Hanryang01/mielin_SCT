-- ============================================================
-- 1. 검사유형별 문항 마스터 (코드 테이블)
--    아동(33문항)/청소년(50문항)/성인(50문항) 별로 문항번호 - 질문내용을 관리
-- ============================================================
CREATE TABLE `ocr_question_master` (
  `oqm_id` INT NOT NULL AUTO_INCREMENT,
  `age_group` VARCHAR(20) NOT NULL COMMENT '검사유형 (아동/청소년/성인)',
  `question_number` INT NOT NULL COMMENT '문항번호',
  `question_text` VARCHAR(500) NOT NULL COMMENT '질문 내용 (예: 내가 가장 마음에 걸리는 것은 ______)',
 	 `test_type` VARCHAR(20) NOT NULL DEFAULT 'SCT' COMMENT '검사종류 (SCT, HTP, KFD 등 확장 대비)',
 	 `is_active` TINYINT(1) NOT NULL DEFAULT '1' COMMENT '사용여부',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_question_master_group_type_number` (`age_group`,`test_type`,`question_number`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='검사유형(연령대)별 문항 마스터 코드 테이블';


-- ============================================================
-- 2. OCR 원본 데이터 (기본 테이블, 매일 배치로 적재)
--    Mielin 원본 DB에서 넘어오는 assessment/media/OCR 결과
--    이미지 파일은 EC2 로컬 볼륨에 저장하고, 여기에는 경로(링크)만 저장
--
--		Batch 입력 테이블 구성 확인하고 추가 사항있으면 반영 
--
-- ============================================================
CREATE TABLE `sct_import_records` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  
  `review_status` VARCHAR(20) NOT NULL DEFAULT 'pending' COMMENT '검수 진행 상태 (pending/in_review/done) - 최종 채택 발생 시 done으로 갱신',
  -- 필요하면 최종 검수자 ID 추가 
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_ocr_raw_assessment_media` (`assessment_id`,`media_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='배치로 매일 적재되는 OCR 원본 데이터(기본 테이블)';


-- ============================================================
-- 3. 검수 작업자 테이블
--    코멘트/분류를 입력하는 여러 작업자를 구분하기 위함
--		웹사이트 로그인 포함
-- ============================================================
CREATE TABLE `ocr_reviewers` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `email` VARCHAR(255) NOT NULL COMMENT '로그인 아이디로 사용하는 이메일',
  `password_hash` VARCHAR(255) NOT NULL COMMENT '비밀번호 해시값 (bcrypt 등, 평문 저장 금지)',
  `name` VARCHAR(100) NOT NULL COMMENT '작업자명',
  `phone` VARCHAR(50) DEFAULT NULL COMMENT '연락처',
  `role` VARCHAR(50) NOT NULL DEFAULT 'annotator' COMMENT '역할 (annotator/admin 등, admin만 최종채택 권한 부여 가능)',
  `is_active` TINYINT(1) NOT NULL DEFAULT '1' COMMENT '계정 활성 여부 (비활성 시 로그인 차단)',
  `last_login_at` DATETIME DEFAULT NULL COMMENT '마지막 로그인 시각',
  `last_login_ip` VARCHAR(50) DEFAULT NULL COMMENT '마지막 로그인 IP (감사/보안용)',
  `failed_login_count` INT NOT NULL DEFAULT '0' COMMENT '연속 로그인 실패 횟수 (계정 잠금 정책용)',
  `locked_at` DATETIME DEFAULT NULL COMMENT '계정 잠금 시각 (실패 누적 시 잠금 처리)',
  `password_changed_at` DATETIME DEFAULT NULL COMMENT '비밀번호 최종 변경 시각 (주기적 변경 정책용)',
  `registered_by_id` INT DEFAULT NULL COMMENT '이 계정을 등록한 관리자 (reviewers.id, 셀프가입이면 NULL)',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `is_deleted` TINYINT(1) NOT NULL DEFAULT '0',
  `deleted_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_reviewers_email` (`email`),
  KEY `ix_reviewers_registered_by_id` (`registered_by_id`),
  CONSTRAINT `fk_reviewers_registered_by` FOREIGN KEY (`registered_by_id`) REFERENCES `reviewers` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='OCR 검수 작업자 (웹 로그인 계정 겸용)';


-- ============================================================
-- 4. OCR 검수 코멘트 (1건의 원본 데이터에 여러 작업자가 입력 가능)
--    그 중 하나를 is_final = 1 로 최종 채택
-- ============================================================
CREATE TABLE `ocr_review_comments` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `ocr_raw_data_id` BIGINT NOT NULL COMMENT 'ocr_raw_data.id 참조',
  `reviewer_id` INT NOT NULL COMMENT '작성한 작업자 (reviewers.id)',
  `classification` VARCHAR(30) NOT NULL COMMENT '분류 (OCR/SCT/OCR 데이터/텍스트 없음/삭제표시 인식/텍스트 아님 등)',
  `is_ocr_correct` TINYINT(1) DEFAULT NULL COMMENT 'OCR 판독 결과 일치 여부 (1=일치, 0=불일치, NULL=미판정)',
  `corrected_text` VARCHAR(1000) DEFAULT NULL COMMENT 'OCR이 틀린 경우 작업자가 직접 입력한 정답 텍스트',
  `difficulty_level` TINYINT NOT NULL COMMENT '필기 난이도 등급 (1~5)',
  `comment` VARCHAR(1000) DEFAULT NULL COMMENT '작업자 자유 코멘트/메모',
  `is_final` TINYINT(1) NOT NULL DEFAULT '0' COMMENT '여러 작업자 입력 중 최종 채택된 값인지 여부',
  `finalized_by_id` INT DEFAULT NULL COMMENT '최종 채택 처리한 사용자 (reviewers.id, 보통 admin)',
  `finalized_at` DATETIME DEFAULT NULL COMMENT '최종 채택된 시각',
  -- is_final=1 인 행만 값이 채워지는 생성 컬럼: raw_data_id 당 최종채택 1건만 허용하기 위한 유니크 제약용
  `final_flag_key` BIGINT GENERATED ALWAYS AS (CASE WHEN `is_final` = 1 THEN `ocr_raw_data_id` ELSE NULL END) STORED,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `is_deleted` TINYINT(1) NOT NULL DEFAULT '0',
  `deleted_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_ocr_review_one_final_per_raw_data` (`final_flag_key`),
  KEY `ix_ocr_review_raw_data_id` (`ocr_raw_data_id`),
  KEY `ix_ocr_review_reviewer_id` (`reviewer_id`),
  KEY `ix_ocr_review_classification` (`classification`),
  KEY `ix_ocr_review_difficulty` (`difficulty_level`),
  KEY `ix_ocr_review_finalized_by_id` (`finalized_by_id`),
  CONSTRAINT `fk_ocr_review_raw_data` FOREIGN KEY (`ocr_raw_data_id`) REFERENCES `ocr_raw_data` (`id`),
  CONSTRAINT `fk_ocr_review_reviewer` FOREIGN KEY (`reviewer_id`) REFERENCES `reviewers` (`id`),
  CONSTRAINT `fk_ocr_review_finalized_by` FOREIGN KEY (`finalized_by_id`) REFERENCES `reviewers` (`id`),
  CONSTRAINT `ck_ocr_review_difficulty_range` CHECK (`difficulty_level` BETWEEN 1 AND 5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='OCR 검수 코멘트 (한 건의 원본 데이터에 여러 작업자가 입력 가능, 그 중 1건만 최종 채택)';