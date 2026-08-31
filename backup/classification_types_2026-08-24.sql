-- MySQL dump 10.13  Distrib 8.4.9, for Win64 (x86_64)
--
-- Host: 127.0.0.1    Database: ocr_review
-- ------------------------------------------------------
-- Server version	8.4.9

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `ocr_classification_types`
--

DROP TABLE IF EXISTS `ocr_classification_types`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `ocr_classification_types` (
  `code` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '고유 코드, ocr_review_comments.classification에서 참조',
  `group_name` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'non_improvable(개선불가) 또는 improvable(개선가능)',
  `label` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '화면 표시용 한글 라벨',
  `display_order` int NOT NULL COMMENT '검수자 화면 드롭다운/버튼 노출 순서',
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`code`),
  KEY `ix_classification_group` (`group_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='분류 코드 마스터 (개선불가/개선가능 그룹 소속 정의)';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `ocr_classification_types`
--

LOCK TABLES `ocr_classification_types` WRITE;
/*!40000 ALTER TABLE `ocr_classification_types` DISABLE KEYS */;
INSERT INTO `ocr_classification_types` VALUES ('ai_error_char_confusion','improvable','비슷한 글자 오인식',3,1,'2026-08-14 17:36:24','2026-08-14 17:36:24'),('ai_error_correction','improvable','수정·덧쓰기 처리 오류',4,1,'2026-08-14 17:36:24','2026-08-14 17:36:24'),('ai_error_length_outlier','improvable','매우 짧거나 긴 응답 오류',8,1,'2026-08-14 17:36:24','2026-08-14 17:36:24'),('ai_error_mixed_script','improvable','영문·숫자·기호 혼합 오류',6,1,'2026-08-14 17:36:24','2026-08-14 17:36:24'),('ai_error_multiline','improvable','여러 줄 응답 처리 오류',5,1,'2026-08-14 17:36:24','2026-08-14 17:36:24'),('ai_error_negation','improvable','부정 표현·의미 반전 오류',7,1,'2026-08-14 17:36:24','2026-08-14 17:36:24'),('ai_error_other','improvable','기타',9,1,'2026-08-14 17:36:24','2026-08-14 17:36:24'),('not_text','non_improvable','텍스트 아님',2,1,'2026-08-14 17:36:24','2026-08-14 17:36:24'),('unreadable','non_improvable','읽기 불가 (알아볼 수 없는 필기)',1,1,'2026-08-14 17:36:24','2026-08-14 17:36:24');
/*!40000 ALTER TABLE `ocr_classification_types` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-08-26 16:31:21
