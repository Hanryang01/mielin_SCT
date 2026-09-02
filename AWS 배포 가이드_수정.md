# AWS EC2 배포 가이드 (mielin 서버 통합 배포)

이 문서는 로컬(Windows)에서 개발해온 `SCT 데이터 조회 / OCR 검수` 앱을,
**`mielin`(SCT 원본 데이터) DB가 이미 올라가 있는 기존 EC2 인스턴스**에
배포하는 절차입니다.

> **이전 버전 문서와 가장 크게 달라진 점**: 앱 전용 신규 EC2를 새로 띄우는 게
> 아니라, `mielin` DB가 이미 떠 있는 **기존 EC2에 앱만 추가로 얹습니다.** 이
> 전제 하나가 바뀌면서 MySQL 설치·계정·DB 이관 대상·`.env` 구조가 전부
> 영향을 받으므로, 아래 "변경된 핵심 전제"를 먼저 확인하세요.

## 변경된 핵심 전제 (반드시 먼저 확인)

| 항목 | 이전 문서(신규 EC2 가정) | 실제(이번 배포) |
|---|---|---|
| 배포 대상 | 앱 전용 신규 EC2 1대 | `mielin` DB가 이미 있는 **기존 EC2** |
| `mielin` DB 접근 | 원격 `43.201.172.24:3306` | 같은 인스턴스이므로 **`localhost`/`127.0.0.1`** |
| `ocr_review` 데이터 위치 | 신규 EC2에 별도 DB(`ocr_review`)로 새로 생성 | **`mielin` DB 안에 테이블로 추가** (별도 DB 아님) |
| MySQL 설치·설정 | 처음부터 설치·`mysql_secure_installation` 진행 | **이미 구성되어 운영 중 — 설치·재시작·재부팅 설정 불필요** |
| DB 계정 | 앱 전용 신규 계정(`sct_app`) 생성 | **신규 계정을 만들지 않는다 — 기존 mielin 접속 계정(광범위 권한)을 `MYSQL_USER`/`REVIEW_MYSQL_USER`에 그대로 재사용** — 아래 "DB 계정과 권한" 참고 |
| 웹 접속 통제 | 내부망/VPN 대역 허용 | **허용된 IP 화이트리스트** 기반 접근 |

> 참고: `mielin` 원격 주소로 알려진 `43.201.172.24`가 사실 이번에 배포하는
> **이 EC2 자신의 주소일 가능성이 높습니다.** 작업 시작 전 `curl ifconfig.me`
> 등으로 EC2의 실제 IP와 비교해 확인하세요.

---

## 소스 코드 확인 결과 (더 이상 "확인 필요" 아님)

이전 개정판에서 "소스를 봐야 확정할 수 있다"고 남겨뒀던 항목을 실제 코드
기준으로 확정합니다.

- **`MYSQL_*`와 `REVIEW_MYSQL_*`는 완전히 독립된 두 개의 pymysql 커넥션**입니다.
  `app/mysql_reader.py`의 `MysqlReader`(SCT 원본, `app/client.py`의
  `SctClient`가 사용)와 `app/review_client.py`의 `ReviewDbClient`(OCR 검수)로
  나뉘어 있고, 커넥션 풀이나 커넥션 객체를 공유하지 않습니다. 이전 문서가
  언급한 `app/db.py`는 존재하지 않습니다 — 실제 파일명은 위 두 개입니다.
- **`MysqlReader`는 코드 레벨에서 SELECT만 허용**합니다(`select_all()`이
  `SELECT`로 시작하지 않는 문장을 받으면 예외를 던집니다). `app/queries/sct_data.py`에도
  SELECT 문 외에는 없습니다. 즉 `MYSQL_USER`가 DB 권한상 쓰기 권한을 갖고
  있어도 이 앱 코드는 절대 쓰지 않지만, 그래도 계정 자체를 SELECT 전용으로
  발급하는 것을 원칙으로 합니다(방어 심층화 + 실수 방지).
- **`ReviewDbClient`는 SELECT/INSERT/UPDATE만 쓰고 DELETE는 전혀 쓰지
  않습니다** — 검수 기록은 append-only 설계입니다(`app/queries/ocr_review.py`
  확인 완료).
- 이번 배포 작업으로 **APP_LEVEL(dev/prod) 환경 분리**가 추가됐습니다.
  `APP_LEVEL=prod`일 때는 앱이 시작 시점에 `REVIEW_MYSQL_*` 6개 값을 전부
  무시하고 `MYSQL_*` 값을 그대로 재사용하도록 `app/config.py`에 코드로
  강제되어 있습니다 — 운영은 실제로 같은 서버·같은 mielin DB·같은 계정을
  쓰기 때문이고, `APP_LEVEL`을 나눈 목적 자체가 이 분기입니다(운영자가
  `.env`에 같은 값을 두 번 입력하다 어긋나는 사고를 막기 위함). `MysqlReader`/
  `ReviewDbClient`라는 두 연결 객체 자체는 여전히 분리되어 있습니다 —
  합쳐지는 건 접속 설정값(host/port/계정/DB명)뿐입니다. dev에서는
  `REVIEW_MYSQL_*`를 그대로 읽으므로 운영과 무관한 별도 DB/계정을 씁니다.
  자세한 내용은 아래 "APP_LEVEL 환경 분리" 절 참고.
- 이번 개정 결과 **API·SQL 쿼리·필터·정렬·페이지네이션·집계·인증 동작은
  전혀 변경되지 않았습니다** — 위 APP_LEVEL 도입은 접속 설정 로딩 단계에만
  영향을 주고, 라우트/쿼리 코드는 그대로입니다.

여전히 배포 담당자가 서버 상태를 직접 보고 확인해야 하는 항목만 남습니다:

| # | 확인 필요 사항 | 해당 단계 |
|---|---|---|
| 1 | `43.201.172.24`가 실제로 이번 배포 EC2 자신의 IP인지 | 0단계 |
| 2 | `mielin` 접속 계정/신규 계정이 필요한 권한을 정확히 갖는지 (`SHOW GRANTS`로 확인) | 2단계 |
| 3 | `mielin` DB 안에 이관 대상 5개 테이블·1개 뷰와 이름이 겹치는 기존 객체가 없는지 | 3단계 |
| 4 | `mielin` DB에 이미 백업 정책(RDS 스냅샷 등)이 있는지, 있다면 새 cron 백업이 중복되지 않는지 | 11단계 |

---

## 환경 구조도

### 로컬 개발 환경 (지금)

```text
[ 로컬 Windows PC ]  APP_LEVEL=dev

  개발자 브라우저
       │  http://localhost:8011
       ▼
  FastAPI 앱 (uvicorn)  ── 평상시 요청 처리 경로 ──
       │
       ├──▶ MySQL, REVIEW_MYSQL_HOST (로컬 또는 별도 검수용 DB)
       │      └ 검수 데이터 (운영 mielin과 무관한 DB/계정)
       │
       └──▶ mielin 서버 MySQL, MYSQL_HOST (SELECT 전용 계정 권장 — 운영
              mielin 원본 또는 그 복제본/스냅샷)
              └ SCT 원본 데이터

  이미지 적재 작업 (SCT 이미지 최초 적재 시에만 실행 — 상시 트래픽 아님)
       │
       └──▶ S3 (HTTPS)
              └ cmaps-hub 버킷 ─ SCT 이미지 원본 저장소
```

특징: DB가 **두 군데**로 나뉘어 있습니다 — 검수 데이터는 dev 전용 DB,
SCT 원본 데이터는 운영 mielin(또는 복제본). `app/.env`에 `APP_LEVEL=dev`가
있으면 앱이 `REVIEW_MYSQL_*` 값을 그대로 사용합니다. **S3(`cmaps-hub`)는
원본 저장소일 뿐, 앱이 평상시 요청마다 접근하지 않습니다.**

### EC2 배포 후 (이번 배포)

```text
허용된 IP 화이트리스트 (지정된 사내 PC들)
       │  http://<EC2 IP>:8011
       │  보안그룹 인바운드로 제한
       ▼
[ EC2 인스턴스 — mielin 서버, 기존 ]  APP_LEVEL=prod

  FastAPI 앱 (uvicorn, systemd 관리)  ── 평상시 요청 처리 경로 ──
       │
       └──▶ MySQL, 127.0.0.1 (로컬 접속 — 원격 접속 아님)
              └ mielin DB  ─ SCT 원본 데이터 + OCR 검수 테이블 (같은 DB,
                 계정도 MYSQL_USER 값을 그대로 사용 — REVIEW_MYSQL_USER는
                 읽지 않음)

  이미지 적재 작업 (SCT 이미지 최초 적재 시에만 실행 — 상시 트래픽 아님)
       │
       └──▶ S3 (HTTPS)
              └ cmaps-hub 버킷
```

특징: 앱과 두 데이터(원본 + 검수)가 **한 인스턴스, 한 MySQL, 한 mielin DB,
한 계정**으로 모입니다. `APP_LEVEL=prod`이면 코드가 REVIEW 커넥션의
host/port/계정/DB명을 전부 MYSQL_*과 자동으로 맞춰주므로(`REVIEW_MYSQL_*`
값은 읽지 않음), `.env`에 같은 값을 두 번 입력하다 어긋나는 사고가 나지
않습니다. 커넥션 객체(`MysqlReader` vs `ReviewDbClient`)는 여전히
분리되어 있습니다 — 합쳐지는 건 접속 설정값뿐입니다.

---

## dev/운영 DB 연결 표

| | `MYSQL_*` (SCT 원본, 읽기 전용) | `REVIEW_MYSQL_*` (OCR 검수) |
|---|---|---|
| **dev** (`APP_LEVEL=dev`) | 운영 mielin 원본 또는 그 복제본/스냅샷을 SELECT 전용 계정으로 연결 (문서에서 우선 권장). 값은 `.env`에 적은 그대로 사용 | 운영과 무관한 별도 DB(로컬 MySQL 등)를 `REVIEW_MYSQL_HOST/PORT`에 적은 그대로 사용. **운영 mielin을 직접 가리키지 않는다** |
| **prod** (`APP_LEVEL=prod`) | `127.0.0.1`, DB `mielin`, 기존에 쓰이던 계정 | `REVIEW_MYSQL_*` 6개 값은 앱이 **전부 무시하고 `MYSQL_*` 값을 그대로 재사용**(코드 강제) → host/port/계정/DB명 모두 `MYSQL_*`과 완전히 동일해짐. `.env`에 `REVIEW_MYSQL_*`을 적어도 읽히지 않는다 |

---

## APP_LEVEL 환경 분리

- `app/.env` **하나의 파일** 안에 `APP_LEVEL=dev` 또는 `APP_LEVEL=prod`를
  적습니다 — 파일을 두 개로 나누지 않습니다.
- 값이 없거나 `dev`/`prod`가 아니면 **앱이 기동 자체를 거부**합니다
  (`app/config.py`). 기본값을 `prod`로 두지 않으므로, 값을 깜빡 지워도
  조용히 운영 모드로 뜨는 사고는 나지 않습니다 — 대신 즉시 에러를 내고 죽습니다.
- 로컬 개발 실행: `app/.env`에 `APP_LEVEL=dev`를 적어두고 평소처럼
  `uv run uvicorn app.main:app --reload --port 8011`로 실행합니다.
- 운영 systemd 서비스: unit 파일에 `Environment=APP_LEVEL=prod`를 명시합니다
  (8단계 참고). systemd가 이미 프로세스 환경변수로 넘겨주므로 `.env`
  파일에도 같은 값을 적어 일치시켜 둡니다(둘 다 `prod`).
- 시작 로그에 `APP_LEVEL`과 두 DB의 `host:port/database`만 비밀번호 없이
  찍힙니다 — `sudo journalctl -u sct-review -n 20`으로 첫 줄만 봐도 잘못된
  `.env`로 떴는지(예: 운영인데 dev 값) 바로 알 수 있습니다.

---

## 0. 시작 전 확인할 것

- [ ] 배포 대상이 `mielin` DB가 이미 떠 있는 **기존 EC2**가 맞는지 재확인 (신규 인스턴스 아님)
- [ ] SSH 접속 정보(퍼블릭/프라이빗 IP, `.pem` 키) 확보
- [ ] 이 앱을 사용할 사람들의 IP 목록을 확보해 보안그룹 화이트리스트 등록 요청
- [ ] `mielin` DB 접속에 이미 쓰이고 있는 계정이 어떤 권한을 갖고 있는지 확인
- [ ] 로컬 PC → EC2로 파일을 옮길 수단(scp) 사용 가능한지
- [ ] 기존 `mielin` DB에 이관 대상 5개 테이블(`ocr_admin_comments`,
      `ocr_negative_keywords`, `ocr_review_comments`, `ocr_review_edits`,
      `ocr_reviewers`)과 뷰(`v_sct_review_status`)와 이름이 겹치는 기존
      객체가 없는지 (3단계에서 다시 한 번 확인)

---

## 1단계 — EC2 접속 및 기본 패키지 설치

```bash
ssh -i <key.pem> ubuntu@<EC2 퍼블릭 IP>

sudo apt update
sudo apt install -y build-essential pkg-config default-libmysqlclient-dev git
```

> ⚠️ **`sudo apt upgrade -y`는 이 배포 절차에 포함하지 않습니다.** 이
> 인스턴스는 `mielin`을 함께 운영 중이므로, 패키지 업그레이드는 배포와
> 분리된 **별도 점검 시간**에 mielin 담당자와 사전 조율 후 진행하세요
> (자세한 내용은 아래 "운영 체크리스트 — 보안 패치" 참고).

> `mysql-server`는 설치하지 않습니다 — 이 EC2에는 `mielin` DB용 MySQL이 이미
> 설치되어 운영 중입니다.

**Python 3.12 확인**: Ubuntu 24.04는 기본 저장소에 `python3.12`가 있습니다. Ubuntu
22.04라면 기본 저장소 버전이 더 낮을 수 있어 `deadsnakes` PPA가 필요할 수 있습니다.

```bash
python3 --version   # 3.12.x가 아니면
sudo add-apt-repository ppa:deadsnakes/ppa -y   # 22.04에서만 필요할 수 있음
sudo apt update
sudo apt install -y python3.12 python3.12-venv
```

**uv 설치** (이 프로젝트는 `uv`로 의존성을 관리합니다):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env   # 설치 스크립트가 안내하는 경로를 따를 것
uv --version
```

---

## 2단계 — MySQL 상태 확인 및 DB 계정 확인

이미 운영 중인 MySQL이므로 **설치, `mysql_secure_installation`, 재시작,
`systemctl enable` 모두 다시 할 필요가 없습니다.**

```bash
sudo systemctl status mysql          # active (running) 확인만
grep bind-address /etc/mysql/mysql.conf.d/mysqld.cnf   # 127.0.0.1인지 확인만, 값 변경 없음
```

### DB 계정 방침 (확정)

**신규 계정을 만들지 않습니다.** 기존에 `mielin` 접속에 쓰이던 admin 계정을
`MYSQL_USER`와 `REVIEW_MYSQL_USER` 양쪽에 그대로 재사용합니다 — 이 계정은
CREATE/DROP/DELETE를 포함한 광범위한 권한을 갖고 있으며, 이번 배포에서
권한을 축소(REVOKE)하지도 않습니다.

```bash
sudo mysql -u root -p
```
```sql
-- 신규 생성 없음 — 기존 계정의 현재 권한만 확인한다.
SHOW GRANTS FOR '<기존 admin 계정>'@'localhost';
```

이 결정에 따른 실질적인 보호장치는 **DB 권한이 아니라 앱 코드 레벨**에
있다는 점을 알아두세요:

- `MysqlReader.select_all()`(`app/mysql_reader.py`)이 `SELECT`로 시작하지
  않는 문장을 실행 시점에 거부합니다 — `MYSQL_USER`가 쓰기 권한을 갖고
  있어도, 이 앱을 통해서는 SCT 원본 데이터에 쓰기가 나가지 않습니다.
- `ReviewDbClient`(`app/review_client.py`)는 SELECT/INSERT/UPDATE 문만
  실행하고 DELETE 문 자체가 코드에 없습니다.
- 반대로 말하면, **앱 코드의 버그나 향후 변경이 이 가드를 우회하면 DB
  권한으로는 막히지 않습니다.** 계정 권한 자체로 최소화하고 싶다면
  부록 "선택 사항 — 계정 최소권한 분리"를 참고하세요(지금 배포에는
  적용하지 않기로 확정).
- 3단계의 덤프 import(`CREATE`/`DROP TABLE` 필요)도 이 계정으로 그대로
  수행할 수 있습니다 — 별도 마이그레이션 계정을 만들 필요가 없습니다.

---

## 3단계 — 로컬 DB → EC2 `mielin` DB로 이관 (테이블 추가)

이관 대상은 정확히 **아래 5개 테이블 + 1개 뷰**입니다:

- `ocr_admin_comments`, `ocr_negative_keywords`, `ocr_review_comments`,
  `ocr_review_edits`, `ocr_reviewers` (테이블)
- `v_sct_review_status` (뷰)

### 3-0. mielin 백업 (import 전 필수)

되돌릴 수 있는 지점을 먼저 만들어둡니다 — 아래 4개 항목이 전부 끝나기 전에는
백업 없이 import를 진행하지 않습니다.

```bash
# EC2에서 — import 직전에 mielin 전체를 백업
mkdir -p ~/backups
mysqldump --default-character-set=utf8mb4 -u root -p mielin \
  | gzip > ~/backups/mielin_pre_ocr_import_$(date +%F_%H%M).sql.gz
```

### 3-1. 이관 대상 충돌 확인

```bash
mysql -u root -p mielin -e "
  SHOW TABLES LIKE 'ocr_admin_comments';
  SHOW TABLES LIKE 'ocr_negative_keywords';
  SHOW TABLES LIKE 'ocr_review_comments';
  SHOW TABLES LIKE 'ocr_review_edits';
  SHOW TABLES LIKE 'ocr_reviewers';
  SHOW FULL TABLES IN mielin LIKE 'v_sct_review_status';
"
```

**결과가 하나라도 나오면(=이미 존재하면) 즉시 멈추세요.** 아래 덤프 파일에는
이 5개 테이블 전부에 대해 **`DROP TABLE IF EXISTS`가 포함되어 있어**, 그대로
import하면 기존 데이터가 **삭제된 뒤 새로 만들어집니다.** 기존 객체가
있다면 바로 import하지 말고, 그 데이터가 무엇인지 먼저 확인해 병합 여부를
판단하세요(3-0에서 이미 백업을 떠 뒀으므로 실수해도 복구는 가능합니다).

### 3-2. cutover 직전 최종 덤프 (데이터 유실 구간 방지)

**로컬에서 검수 작업이 계속 진행 중이라면, 예전에 미리 떠둔 덤프를 그대로
쓰지 마세요.** import 직전에 아래 둘 중 하나를 반드시 지키세요.

- (권장) cutover 시각을 정해 팀에 공지하고, 그 시각부터 로컬 앱에서 검수
  입력을 중지시킨 뒤 덤프를 뜬다. 이후 로컬 앱은 재사용하지 않는다.
- 위가 어렵다면, import 직전에 **최종 덤프를 다시 한 번 떠서** 그 사이
  쌓인 데이터가 빠지지 않게 한다.

로컬(Windows) PC에서 최종 덤프:

```bash
"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqldump.exe" \
  --default-character-set=utf8mb4 -h 127.0.0.1 -u root -p \
  ocr_review ocr_admin_comments ocr_negative_keywords ocr_review_comments \
  ocr_review_edits ocr_reviewers v_sct_review_status > ocr_review_dump.sql
```

> ⚠️ **덤프 파일 자체에는 `CREATE DATABASE`/`USE` 구문이 없습니다**(확인
> 완료). 즉 아래 import 명령에서 지정한 DB(`mielin`)에 그대로 들어갑니다 —
> 파일 자체가 어느 DB로 갈지 정하지 않으므로, import 명령의 대상 DB명을
> 잘못 쓰면(예: `ocr_review`) 엉뚱한 DB에 생성됩니다.

> ⚠️ **뷰의 `DEFINER=root@localhost SQL SECURITY DEFINER`**: 덤프에 포함된
> `v_sct_review_status` 뷰는 로컬 개발 DB의 `root@localhost`로 정의돼
> 있습니다. 운영 EC2에도 같은 이름/권한의 `root@localhost`가 있다면 그대로
> 동작하지만, 다음 중 하나를 반드시 선택해 처리하세요.
> - **(A) import 전 치환**: 로컬에서 덤프 파일을 열어(민감 데이터가 있으니
>   에디터로만, 저장소에 올리지 말고) `DEFINER=`root`@`localhost`` 부분을
>   운영에서 실제로 쓸 계정(예: 마이그레이션 계정)으로 바꾼 뒤 import한다.
> - **(B) import 후 재생성**: 일단 그대로 import하고, 아래처럼 운영 계정으로
>   뷰를 명시적으로 다시 만든다(뷰 정의 SELECT문은 로컬에서
>   `SHOW CREATE VIEW v_sct_review_status;`로 뽑아서 그대로 옮긴다).
>   ```sql
>   DROP VIEW IF EXISTS mielin.v_sct_review_status;
>   CREATE DEFINER=`<운영에서 쓸 계정>`@`localhost` SQL SECURITY DEFINER
>   VIEW mielin.v_sct_review_status AS
>   <SHOW CREATE VIEW 결과에서 그대로 복사한 SELECT문>;
>   ```
> 어느 쪽이든 뷰를 SELECT하는 `REVIEW_MYSQL_USER` 계정이 DEFINER 계정의
> 권한으로 실행되므로, DEFINER 계정이 실제로 운영 EC2에 존재하고 잠겨있지
> 않은지 먼저 확인하세요.

로컬 → EC2로 전송:

```bash
scp -i <key.pem> ocr_review_dump.sql ubuntu@<EC2 퍼블릭 IP>:~/
```

**EC2에서** `root`(또는 마이그레이션 계정)로 `mielin` DB에 복원:

```bash
mysql --default-character-set=utf8mb4 -u root -p mielin < ~/ocr_review_dump.sql
```

### 3-3. import 후 검증 (row count만으로 끝내지 않는다)

**행 수 비교**:

```bash
mysql -u root -p mielin -e "
  SELECT 'ocr_reviewers' t, COUNT(*) c FROM ocr_reviewers
  UNION ALL SELECT 'ocr_review_comments', COUNT(*) FROM ocr_review_comments
  UNION ALL SELECT 'ocr_admin_comments', COUNT(*) FROM ocr_admin_comments
  UNION ALL SELECT 'ocr_review_edits', COUNT(*) FROM ocr_review_edits
  UNION ALL SELECT 'ocr_negative_keywords', COUNT(*) FROM ocr_negative_keywords;
"
```

3-2에서 뜬 최종 덤프 시점 기준으로, **로컬에서 같은 쿼리를 먼저 돌려서** 값이
일치하는지 비교하세요(하루 수백 건씩 계속 쌓이는 데이터라 이 문서에 특정
숫자를 고정해두지 않습니다).

**스키마/인덱스/FK/뷰 검증** (행 수가 같아도 구조가 다르면 의미가 없습니다):

```bash
# 테이블 구조 비교 — 로컬/EC2 양쪽에서 각각 뽑아 diff
mysql -u root -p mielin -e "
  SHOW CREATE TABLE ocr_reviewers\G
  SHOW CREATE TABLE ocr_review_comments\G
  SHOW CREATE TABLE ocr_admin_comments\G
  SHOW CREATE TABLE ocr_review_edits\G
  SHOW CREATE TABLE ocr_negative_keywords\G
"

# 인덱스
mysql -u root -p mielin -e "
  SHOW INDEX FROM ocr_reviewers;
  SHOW INDEX FROM ocr_review_comments;
"

# FK (참조 관계가 깨지지 않았는지)
mysql -u root -p mielin -e "
  SELECT TABLE_NAME, COLUMN_NAME, CONSTRAINT_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
  FROM information_schema.KEY_COLUMN_USAGE
  WHERE TABLE_SCHEMA='mielin' AND REFERENCED_TABLE_NAME IS NOT NULL
    AND TABLE_NAME IN ('ocr_reviewers','ocr_review_comments','ocr_admin_comments','ocr_review_edits');
"

# 뷰 — 정의와 실제 조회 둘 다 확인
mysql -u root -p mielin -e "SHOW CREATE VIEW v_sct_review_status\G"
mysql -u root -p mielin -e "SELECT COUNT(*) FROM v_sct_review_status;"
```

### 3-4. 테스트/개발 계정 정리 (운영 반영 전 결정 필요)

```bash
mysql -u root -p mielin -e "
  SELECT id, username, role, is_active, is_deleted, last_login_at
  FROM ocr_reviewers ORDER BY username;
"
```

- [ ] **`reviewer-a`, `reviewer-b`**: 개발/테스트용 계정으로 보입니다. 운영
      반영 전에 **삭제할지 비활성화(`is_active=0`)할지 팀 확인 후 결정**하세요.
      앱 코드 자체는 DELETE 문을 실행하지 않으므로(화면/API로는 지울 수
      없음), 지우기로 했다면 아래처럼 `mysql` 클라이언트로 직접 실행합니다.
      ```sql
      -- 삭제 대신 비활성화(권장 — 이력 보존)
      UPDATE ocr_reviewers SET is_active = 0 WHERE username IN ('reviewer-a', 'reviewer-b');
      -- 완전 삭제가 필요하다면(연관 FK 데이터가 없을 때만)
      -- DELETE FROM ocr_reviewers WHERE username IN ('reviewer-a', 'reviewer-b');
      ```
- [ ] **`admin`**: 운영에 남길 계정인지 확인(계정명이 일반적이라 실수로
      낯선 사람이 접근하지 않게 비밀번호를 운영용으로 재발급하는 것을 권장).
- [ ] **`technonia01`, `technonia02`**: 운영 실사용 계정으로 보입니다.
      `is_active=1`, `is_deleted=0`인지 확인하고, **실제 브라우저에서
      화이트리스트 IP로 접속해 기존 비밀번호로 로그인이 되는지(bcrypt 해시
      검증) 반드시 테스트**하세요 — 비밀번호는 덤프에 그대로 딸려오므로
      새로 시딩할 필요는 없습니다.

### 3-5. 덤프 파일 정리 (커밋 금지 + 사용 후 삭제)

- 덤프 파일(`ocr_review_dump.sql`)은 **절대 git에 커밋하지 않습니다** —
  `.gitignore`에 `*dump*.sql` 패턴을 추가해 실수로 스테이징되는 것을 막아
  뒀습니다(그래도 `git status`로 한 번 더 확인하세요).
- 검증(3-3)까지 끝나고 이관이 확정되면, **로컬/EC2 양쪽에서 삭제**합니다.
  ```bash
  # EC2
  shred -u ~/ocr_review_dump.sql 2>/dev/null || rm -f ~/ocr_review_dump.sql
  # 로컬(PowerShell)
  Remove-Item ocr_review_dump.sql
  ```
- 롤백 가능성 때문에 잠시 보관해야 한다면, git 저장소 바깥의 별도
  안전한 위치(예: 암호화된 개인 백업 폴더)에만 두세요.

---

## 4단계 — 소스 코드 전달

로컬 프로젝트를 통째로 압축해 옮기되, **아래는 제외**하고 압축합니다:

| 제외 대상 | 이유 |
|---|---|
| `.venv/` | Windows용 가상환경, EC2에서 `uv sync`로 새로 만듦 |
| `__pycache__/`, `*.pyc`, `.pytest_cache/` | 캐시 |
| `app/.env` | 로컬 실제 자격증명 — 5단계에서 EC2용으로 새로 작성 |
| `.mysql/credentials.txt` | 로컬 MySQL root 비밀번호, EC2와 무관 |
| `.mysql/data/` | 로컬 MySQL 실제 데이터 파일(수백MB), 3단계 덤프로 이미 이관함 |
| `uvicorn_out.log` | 로컬 실행 로그 |
| `*.sql` 중 `*dump*` 패턴 파일 | 3-5에서 이미 정리했어야 함 — 남아 있으면 압축에서도 제외 |

```bash
scp -i <key.pem> mielin_SCT.tar.gz ubuntu@<EC2 퍼블릭 IP>:~/
ssh -i <key.pem> ubuntu@<EC2 퍼블릭 IP>
mkdir -p ~/mielin_SCT && tar -xzf mielin_SCT.tar.gz -C ~/mielin_SCT
cd ~/mielin_SCT
ls  # SCT Questions.xlsx가 포함되어 있는지 꼭 확인 — question_master.py가 이 파일을 읽음
```

---

## 5단계 — `app/.env` 새로 작성

```bash
cp app/.env.template app/.env
nano app/.env   # 또는 vim
```

| 항목 | 값 (EC2, `APP_LEVEL=prod`) |
|---|---|
| `APP_LEVEL` | `prod` |
| `MYSQL_HOST` | `127.0.0.1` |
| `MYSQL_DATABASE` | `mielin` |
| `MYSQL_USER` / `PASSWORD` | 이 EC2에서 mielin 접속에 이미 쓰이고 있는 계정 정보 그대로 |
| `REVIEW_MYSQL_*` (전체 6개) | `APP_LEVEL=prod`이면 앱이 이 값들을 전부 무시하고 `MYSQL_*` 값을 그대로 쓰므로 비워둬도 됩니다. 문서화를 위해 `MYSQL_*`과 같은 값(`127.0.0.1`/`mielin`/같은 계정)을 적어두는 것을 권장 |
| `AWS_ACCESS_KEY_ID` / `SECRET` | **로컬 키를 재사용하지 말고 새로 발급**한 키 사용 |
| `S3_BUCKET` / `AWS_REGION` | `cmaps-hub` / `ap-northeast-2` (로컬과 동일) |
| `SESSION_SECRET_KEY` | 새로 생성 — 아래 명령 참고 |
| `SESSION_MAX_AGE_SECONDS` | 로컬과 동일하게 두거나 필요에 맞게 조정 |

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
# 출력값을 SESSION_SECRET_KEY에 붙여넣기
```

파일 권한을 제한합니다(비밀번호가 든 파일이므로):

```bash
chmod 600 app/.env
```

> **참고**: `AWS_ACCESS_KEY_ID`/`SECRET`을 아예 없애고 EC2 인스턴스 프로파일(IAM
> 역할)로 대체하는 방법이 보안상 더 낫지만, 지금 코드(`app/s3_client.py`)는 두 값이
> 비어 있으면 S3 기능 자체를 꺼버리도록 돼 있어 이 방식을 쓰려면 코드를 약간
> 고쳐야 합니다. 지금은 새 키를 발급해 `.env`에 넣는 방식으로 진행하고, 필요하면
> 이후에 별도로 IAM 역할 전환 작업을 하는 걸 권합니다.

---

## 6단계 — 파이썬 의존성 설치 (lockfile 고정)

`uv.lock`에 고정된 버전 그대로, lockfile을 갱신하지 않고 설치합니다
(`pyproject.toml`과 `uv.lock`이 어긋나 있으면 여기서 바로 실패해야
합니다 — 조용히 다른 버전이 깔리는 것을 막기 위함):

```bash
cd ~/mielin_SCT
uv sync --frozen
```

`pyproject.toml`에 `requires-python = ">=3.12,<3.13"`으로 고정돼 있으므로, 1단계에서
Python 3.12가 제대로 설치됐어야 이 단계가 성공합니다.

---

## 7단계 — 배포 전 필수 게이트: 테스트 실행

**아래 명령이 실패하면 8단계(systemd 재시작)로 넘어가지 않습니다.** 테스트는
실제 MySQL/AWS에 접속하지 않으므로 이 EC2에서 그대로 돌려도 안전합니다.

```bash
uv run pytest -q
```

- 실패 시: 실패한 테스트를 먼저 확인하고 원인을 해결한 뒤 다시 실행합니다.
  절대 `systemctl restart sct-review`로 넘어가지 마세요 — 이미 떠 있는
  이전 버전이 계속 서비스되게 두는 편이, 검증 안 된 새 코드를 올리는 것보다
  안전합니다.
- (선택) lockfile이 실제로 최신인지 한 번 더 확인하려면:
  ```bash
  uv lock --check
  ```

---

## 8단계 — systemd 서비스로 등록

로컬 개발에서 쓰던 `watchfiles`(코드 저장 시 자동 재시작)는 개발 편의용이라
운영에는 필요 없습니다. 운영은 순수 `uvicorn`을 systemd로 관리해 죽으면 자동
재시작되게 합니다.

```bash
sudo nano /etc/systemd/system/sct-review.service
```

```ini
[Unit]
Description=SCT 데이터 조회 / OCR 검수 웹앱
After=network.target mysql.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/mielin_SCT
Environment=APP_LEVEL=prod
ExecStart=/home/ubuntu/.local/bin/uv run uvicorn app.main:app --host 0.0.0.0 --port 8011
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

- `Environment=APP_LEVEL=prod`를 반드시 넣으세요 — 이게 없고 `.env`에도
  `APP_LEVEL`이 비어 있으면 서비스가 기동하지 못하고 즉시 죽습니다(의도된
  동작입니다 — 3번 "APP_LEVEL 환경 분리" 참고).
- `ExecStart`의 `uv` 경로는 `which uv`로 실제 경로를 확인해 맞추세요.
- `User`는 소스가 있는 계정과 맞춰야 합니다(예시는 `ubuntu`).
- `mysql.service`는 이미 이 인스턴스에서 다른 용도로도 쓰이고 있으므로,
  이 앱의 systemd 유닛을 내려도(`stop`) MySQL 자체는 영향받지 않습니다.

```bash
sudo systemctl daemon-reload
sudo systemctl enable sct-review
sudo systemctl start sct-review
sudo systemctl status sct-review   # active (running) 확인
sudo journalctl -u sct-review -n 20 --no-pager
# 첫 줄에 [startup] APP_LEVEL=prod MYSQL=127.0.0.1:3306/mielin ... 이 보이는지 확인
# (dev 값이 보이면 잘못된 .env가 배포된 것 — 즉시 중단하고 5단계부터 재확인)
```

---

## 9단계 — 보안그룹/방화벽 재확인 (IP 화이트리스트 기준)

- **8011 인바운드**: **허용된 IP 화이트리스트만** (개별 IP 또는 소규모 목록으로
  등록, `0.0.0.0/0` 금지)
- **22(SSH) 인바운드**: 관리자 IP만 허용
- **3306(MySQL)**: 인바운드 규칙 자체를 만들지 않습니다 — `bind-address 127.0.0.1`로
  이미 외부 접속이 막혀 있으므로 이중으로 안전합니다.
- **아웃바운드**: S3(HTTPS/443)로 나가는 트래픽만 확인하면 됩니다.

---

## 10단계 — 동작 확인

EC2 내부에서:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8011/
# 307(로그인 화면 리다이렉트)이면 정상
sudo journalctl -u sct-review -n 50 --no-pager   # 에러 없는지 확인
```

화이트리스트에 등록된 PC에서 브라우저로 `http://<EC2 퍼블릭 IP>:8011`
접속 → 로그인 화면이 뜨는지, 기존 계정(`technonia01`/`technonia02`/`admin`)으로
로그인이 되는지, `/review`·`/admin` 화면에서 목록·이미지가 정상적으로 보이는지
확인합니다. 이미지가 안 보이면 대개 `.env`의 S3 관련 값 또는 IAM 키 권한 문제입니다.

---

## 11단계 — 운영 체크리스트

- **정기 백업**: `mielin` DB에 이미 백업 정책(예: RDS 스냅샷, 별도 cron 등)이
  있는지 먼저 확인하세요. 검수 테이블이 이제 `mielin` DB 안에 있으므로, 기존
  백업 범위에 자동으로 포함됩니다. 없다면 아래처럼 새로 구성합니다.
  ```bash
  crontab -e
  # 예: 매일 새벽 3시
  0 3 * * * mysqldump --default-character-set=utf8mb4 -u root -p'비밀번호' mielin | gzip > /home/ubuntu/backups/mielin_$(date +\%F).sql.gz
  ```
- **로그 확인**: `sudo journalctl -u sct-review -f`
- **재부팅 후 자동 기동 확인**: `systemctl is-enabled sct-review`
- **보안 패치(`apt upgrade`)**: 배포 절차에 포함하지 않습니다. `mielin`을
  함께 운영 중인 인스턴스이므로, **별도로 잡은 점검 시간에만** mielin
  담당자와 조율 후 `sudo apt update && sudo apt upgrade -y`를 실행하세요.
- **디스크 용량**: `/var/lib/mysql` 용량 모니터링 — 검수 데이터 증가분도
  같은 볼륨에 쌓입니다.
- **배포 전 게이트 재확인**: 다음 배포부터도 7단계(`uv run pytest -q`)가
  통과하지 않으면 `systemctl restart sct-review`를 하지 않습니다.

---

## HTTP 8011의 보안 한계 — VPN/HTTPS 권고

지금 구성(화이트리스트 + 평문 HTTP)에는 실질적인 한계가 있습니다.

- IP가 화이트리스트에 있어도, **그 사용자와 EC2 사이의 네트워크 경로가
  안전하다는 뜻은 아닙니다** — 사내망이 아닌 공용 와이파이·중간 프록시 등에서
  평문 HTTP 트래픽(로그인 비밀번호, 세션 쿠키)이 노출될 수 있습니다.
- 세션 쿠키가 `https_only` 없이 발급되므로, HTTPS로 전환하기 전까지는
  이 위험이 구조적으로 남아 있습니다.

권장 순서(우선순위):

1. **VPN/사내망 경유로 전환** — 8011 포트를 인터넷에 직접 노출하지 않고
   VPN 뒤로 옮기는 것이 화이트리스트보다 근본적인 해결책입니다.
2. **nginx 리버스 프록시 + Let's Encrypt(`certbot`)로 HTTPS 적용** — 도메인이
   있다면 이 방법으로 전환하고, `app/main.py`의 `SessionMiddleware`에
   `https_only=True`를 추가하세요(지금은 HTTP 전용이라 꺼져 있는 게 맞습니다).
3. 위 두 가지가 당장 어렵다면, 최소한 화이트리스트를 **고정 IP(사무실
   고정 회선 등)로만** 제한하고, 이 상태를 임시 조치로 취급해 오래
   유지하지 마세요.

---

## 롤백 절차

문제가 생기면 아래 순서로 되돌립니다. 3-0에서 뜬 백업(`mielin_pre_ocr_import_*.sql.gz`)이
이 절차의 전제입니다 — 백업 없이 진행했다면 먼저 가능한 현재 상태를 백업한 뒤
진행하세요.

1. **서비스 중지**: `sudo systemctl stop sct-review` (사용자가 깨진 화면을
   보지 않도록 먼저 내립니다)
2. **DB 원복**:
   - 이관 대상 5개 테이블/뷰가 **원래 없었다면**(3-1에서 충돌 없음을 확인한
     경우): 새로 생긴 객체만 제거합니다.
     ```sql
     DROP VIEW IF EXISTS mielin.v_sct_review_status;
     DROP TABLE IF EXISTS mielin.ocr_admin_comments, mielin.ocr_negative_keywords,
       mielin.ocr_review_comments, mielin.ocr_review_edits, mielin.ocr_reviewers;
     ```
   - 기존 객체와 **병합/충돌이 있었다면**: 전체 백업으로 복원합니다.
     ```bash
     gunzip -c ~/backups/mielin_pre_ocr_import_<날짜>.sql.gz | mysql -u root -p mielin
     ```
3. **애플리케이션 원복**: 이전 배포 소스/`.env`가 남아 있다면 그것으로
   되돌리고 `uv sync --frozen` 후 7단계(pytest) → 8단계(systemd) 순서를
   그대로 다시 밟습니다. 새 코드에 문제가 있었다면 원인 파악 전까지
   재배포하지 않습니다.
4. **검증**: 10단계의 동작 확인을 다시 수행해 정상 응답을 확인합니다.
5. **사후 기록**: 무엇이 실패했는지, 어느 단계에서 발견했는지 남겨 다음
   배포 때 같은 문제가 반복되지 않게 합니다.

---

## 부록 — 선택 사항: 계정 최소권한 분리 (이번 배포에는 적용하지 않음)

지금은 기존 admin 계정(CREATE/DROP/DELETE 포함 광범위 권한)을
`MYSQL_USER`/`REVIEW_MYSQL_USER` 양쪽에 그대로 재사용하기로 확정했습니다.
DB 권한 자체로 더 좁히고 싶어지면(다른 프로젝트가 같은 계정을 공유하지 않게
되는 시점 등), 아래처럼 **신규 계정을 만들지 않고 기존 계정 권한을 REVOKE로
축소**하거나, 완전히 분리하고 싶다면 신규 계정 2개를 만드는 방법이 있습니다.
둘 다 지금 당장 실행하는 단계가 아니라 향후 참고용입니다.

```sql
-- 방법 A: 계정은 그대로 두고 권한만 축소 (다른 용도로 이 계정을 쓰는
-- 곳이 없는지 먼저 확인 — 있다면 그쪽이 깨질 수 있음)
REVOKE DELETE, CREATE, DROP, ALTER ON mielin.* FROM '<admin 계정>'@'localhost';
FLUSH PRIVILEGES;

-- 방법 B: 완전히 분리된 신규 계정 (SCT 조회는 SELECT 전용,
-- OCR 검수는 이관 대상 5개 테이블 + 뷰에만)
CREATE USER 'sct_reader'@'localhost' IDENTIFIED BY '<strong-password>';
GRANT SELECT ON mielin.* TO 'sct_reader'@'localhost';

CREATE USER 'ocr_review_app'@'localhost' IDENTIFIED BY '<strong-password>';
GRANT SELECT, INSERT, UPDATE ON mielin.ocr_admin_comments     TO 'ocr_review_app'@'localhost';
GRANT SELECT, INSERT, UPDATE ON mielin.ocr_negative_keywords  TO 'ocr_review_app'@'localhost';
GRANT SELECT, INSERT, UPDATE ON mielin.ocr_review_comments    TO 'ocr_review_app'@'localhost';
GRANT SELECT, INSERT, UPDATE ON mielin.ocr_review_edits       TO 'ocr_review_app'@'localhost';
GRANT SELECT, INSERT, UPDATE ON mielin.ocr_reviewers          TO 'ocr_review_app'@'localhost';
GRANT SELECT ON mielin.v_sct_review_status TO 'ocr_review_app'@'localhost';
FLUSH PRIVILEGES;
```

**주의**: 지금 `app/config.py`는 `APP_LEVEL=prod`일 때 `REVIEW_MYSQL_*` 값을
아예 읽지 않고 `MYSQL_*`을 그대로 재사용하도록 코드로 고정돼 있습니다. 위
방법 B(완전 분리)로 전환하려면 `.env`에 `REVIEW_MYSQL_USER`/`PASSWORD`를
새로 채우는 것만으로는 반영되지 않고, `app/config.py`의 prod 분기(REVIEW_MYSQL_*을
무시하고 MYSQL_*을 재사용하는 부분)를 되돌리는 코드 수정이 함께 필요합니다.

---

## 부록 — 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 서비스가 즉시 죽음(active (running)이 안 됨) | `sudo journalctl -u sct-review -n 50` — `APP_LEVEL` 또는 `SESSION_SECRET_KEY` 관련 에러 메시지가 원인일 가능성이 높음(8단계 참고) |
| 브라우저에서 접속 자체가 안 됨 | 보안그룹 8011 인바운드에 접속 IP가 화이트리스트로 등록돼 있는지, `systemctl status sct-review` |
| 로그인 화면은 뜨는데 로그인 실패 | `.env`의 `REVIEW_MYSQL_*`(및 prod에서 실제로 쓰이는 `MYSQL_HOST/PORT`) 값, `mysql -u <admin 계정> -p mielin`로 직접 접속 테스트 |
| 목록 조회 시 500/빈 화면 | `.env`의 `MYSQL_*` 값, `mielin` DB 테이블이 3단계에서 정상 이관됐는지 |
| 이미지가 안 보임 | `.env`의 `AWS_*`/`S3_BUCKET` 값, IAM 키 권한(cmaps-hub 버킷 GetObject) |
| 시작 로그의 APP_LEVEL/host가 기대와 다름 | 잘못된 `.env`가 배포됐거나 systemd `Environment=`가 빠진 것 — 즉시 서비스를 내리고 5·8단계 재확인 |
| 롤백이 필요할 때 | 위 "롤백 절차" 참고 |
