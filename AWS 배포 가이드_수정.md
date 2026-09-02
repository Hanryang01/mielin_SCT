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
| DB 계정 | 앱 전용 신규 계정(`sct_app`) 생성 | **기존 mielin 접속 계정을 그대로 사용 — 신규 생성 불필요** (단, 새 테이블에 대한 권한만 확인) |
| 웹 접속 통제 | 내부망/VPN 대역 허용 | **허용된 IP 화이트리스트** 기반 접근 |

> 참고: `mielin` 원격 주소로 알려진 `43.201.172.24`가 사실 이번에 배포하는
> **이 EC2 자신의 주소일 가능성이 높습니다.** 작업 시작 전 `curl ifconfig.me`
> 등으로 EC2의 실제 IP와 비교해 확인하세요.

---

## 문서 검토 결과 — 확정된 것 / 확인이 더 필요한 것

이번 개정에서 위 6가지 제보 내용은 문서 전반에 반영·확정했습니다. 다만 아래
항목들은 **실제 소스 코드나 서버 상태를 직접 보지 않고는 확정할 수 없어서**,
각 단계에 "확인 필요" 형태로 남겨뒀습니다. 배포 담당자가 진행하면서 반드시
짚고 넘어가야 합니다.

| # | 확인 필요 사항 | 해당 단계 |
|---|---|---|
| 1 | `43.201.172.24`가 실제로 이번 배포 EC2 자신의 IP인지 | 상단 참고, 0단계 |
| 2 | `mielin` 접속 계정이 새 검수용 테이블에 대해 `CREATE`/`SELECT`/`INSERT`/`UPDATE` 권한을 갖는지 (`SHOW GRANTS`로 확인) | 0, 2단계 |
| 3 | `mielin` DB 안에 `ocr_reviewers` 등과 이름이 겹치는 기존 테이블이 없는지 | 0, 3단계 |
| 4 | 앱 코드가 `MYSQL_*`/`REVIEW_MYSQL_*`를 완전히 독립된 두 커넥션으로 쓰는지 — 값만 맞추면 되는지, 커넥션 통합 리팩터링이 필요한지 | 5단계 |
| 5 | `mielin` DB에 이미 백업 정책(RDS 스냅샷 등)이 있는지, 있다면 새 cron 백업이 중복되지 않는지 | 10단계 |

위 5가지는 "제가 검토를 완료해 결론 낸 것"이 아니라 **"코드/서버를 직접 보지
않는 한 이 문서만으로는 결론 낼 수 없어 확인 필요로 남긴 것"**이라는 점을
분명히 해둡니다. 실제 프로젝트 소스와 서버 접근 권한이 주어지면 이 항목들도
마저 검증해 문서를 확정할 수 있습니다.

---

## 환경 구조도

### 로컬 개발 환경 (지금)

```text
[ 로컬 Windows PC ]

  개발자 브라우저
       │  http://localhost:8011
       ▼
  FastAPI 앱 (uvicorn)  ── 평상시 요청 처리 경로 ──
       │
       ├──▶ MySQL, 127.0.0.1 (로컬 접속)
       │      └ ocr_review DB  ─ 검수 데이터
       │
       └──▶ mielin 서버 MySQL, 43.201.172.24 (원격 접속, 3306)
              └ SCT 원본 데이터

  이미지 적재 작업 (SCT 이미지 최초 적재 시에만 실행 — 상시 트래픽 아님)
       │
       └──▶ S3 (HTTPS)
              └ cmaps-hub 버킷 ─ SCT 이미지 원본 저장소
                 (다운로드 → 로컬 저장 + DB에 경로 기록, 이후엔 로컬 파일로 서빙)
```

특징: DB가 **두 군데**로 나뉘어 있습니다 — 검수 데이터(`ocr_review`)는 로컬
MySQL, SCT 원본 데이터는 원격 `mielin` 서버. 그래서 `app/.env`에도
`MYSQL_*`(mielin, 원격 접속)와 `REVIEW_MYSQL_*`(로컬 접속)가 서로 다른 값으로
따로 존재합니다. **S3(`cmaps-hub`)는 원본 저장소일 뿐, 앱이 평상시 요청마다
접근하지 않습니다** — SCT 이미지를 내려받아 적재하고 DB에 경로를 기록하는
시점에만 접근합니다.

### EC2 배포 후 (이번 배포)

```text
허용된 IP 화이트리스트 (지정된 사내 PC들)
       │  http://<EC2 IP>:8011
       │  보안그룹 인바운드로 제한
       ▼
[ EC2 인스턴스 — mielin 서버, 기존 ]

  FastAPI 앱 (uvicorn, systemd 관리)  ── 평상시 요청 처리 경로 ──
       │
       └──▶ MySQL, 127.0.0.1 (로컬 접속 — 원격 접속 아님)
              └ mielin DB  ─ SCT 원본 데이터 + ocr_review 관련 테이블 (통합)

  이미지 적재 작업 (SCT 이미지 최초 적재 시에만 실행 — 상시 트래픽 아님)
       │
       └──▶ S3 (HTTPS)
              └ cmaps-hub 버킷 ─ SCT 이미지 원본 저장소
                 (다운로드 → EC2에 저장 + DB에 경로 기록, 이후엔 로컬 파일로 서빙)
```

특징: 앱과 두 데이터(원본 + 검수)가 **한 인스턴스, 한 MySQL**로 모입니다.
앱 입장에서는 두 접속 모두 `127.0.0.1`(로컬)로 바뀌고, 검수 관련 테이블은
별도 DB가 아니라 **`mielin` DB 안에** 같이 생깁니다. 외부 접근 통제는
VPN 대역이 아니라 **허용된 IP만 통과시키는 화이트리스트**로 이루어집니다.
**S3 접근도 로컬과 동일하게 상시 경로가 아니라 이미지 적재 시점에만** 발생하므로,
평상시 EC2 아웃바운드 트래픽은 DB(로컬)만 오가고 S3 트래픽은 적재 작업을 돌릴
때만 발생합니다.

**두 그림을 나란히 비교하면**: 로컬은 화살표가 3방향(로컬 DB / 원격 mielin /
S3)으로 흩어지지만, EC2에서는 DB 화살표 2개가 **하나로 합쳐집니다** — 이게
이번 배포에서 `.env`와 DB 이관 작업이 필요한 이유입니다.

---

## 0. 시작 전 확인할 것

- [ ] 배포 대상이 `mielin` DB가 이미 떠 있는 **기존 EC2**가 맞는지 재확인 (신규 인스턴스 아님)
- [ ] SSH 접속 정보(퍼블릭/프라이빗 IP, `.pem` 키) 확보
- [ ] 이 앱을 사용할 사람들의 IP 목록을 확보해 보안그룹 화이트리스트 등록 요청
- [ ] `mielin` DB 접속에 이미 쓰이고 있는 계정이 어떤 권한을 갖고 있는지 확인
      (새 테이블 생성용 `CREATE`, 이후 운영용 `SELECT`/`INSERT`/`UPDATE`)
- [ ] 로컬 PC → EC2로 파일을 옮길 수단(scp) 사용 가능한지
- [ ] 기존 `mielin` DB에 `ocr_review_*` 등과 이름이 겹치는 테이블이 없는지
      (3단계 이관 전에 한 번 훑어보기)

---

## 1단계 — EC2 접속 및 기본 패키지 설치

```bash
ssh -i <key.pem> ubuntu@<EC2 퍼블릭 IP>

sudo apt update && sudo apt upgrade -y
sudo apt install -y build-essential pkg-config default-libmysqlclient-dev git
```

> `mysql-server`는 설치하지 않습니다 — 이 EC2에는 `mielin` DB용 MySQL이 이미
> 설치되어 운영 중입니다. 아래는 (신규 EC2였다면 필요했을) 참고용 명령이며,
> **실제로 실행할 필요는 없습니다.**
> ```bash
> # 참고용 — 실행하지 않음
> # sudo apt install -y mysql-server
> ```

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

## 2단계 — MySQL 상태 확인 (신규 설정 아님)

이미 운영 중인 MySQL이므로 **설치, `mysql_secure_installation`, 재시작,
`systemctl enable` 모두 다시 할 필요가 없습니다.** 아래는 상태만 확인하는
단계입니다.

```bash
sudo systemctl status mysql          # active (running) 확인만
grep bind-address /etc/mysql/mysql.conf.d/mysqld.cnf   # 127.0.0.1인지 확인만, 값 변경 없음
```

**기존 계정 권한 확인** — 새 테이블(`ocr_review_*`)을 만들고 이후 앱이
사용할 만큼의 권한이 있는지만 봅니다. 신규 계정을 만들 필요는 없습니다.

```bash
sudo mysql -u root -p
```
```sql
SHOW GRANTS FOR '<mielin 접속에 이미 쓰이는 계정>'@'localhost';
```

권한이 부족하면(예: `CREATE`가 없어서 4단계 테이블 생성이 안 되는 경우) 그때만
최소한으로 추가합니다:

```sql
GRANT SELECT, INSERT, UPDATE, CREATE ON mielin.* TO '<계정>'@'localhost';
-- CREATE는 3단계 테이블 생성 1회만 필요하면, 이후 회수해도 됩니다.
-- DELETE는 주지 않습니다 — 검수 기록은 append-only 원칙 (README 참고)
FLUSH PRIVILEGES;
```

---

## 3단계 — 로컬 DB → EC2 `mielin` DB로 이관 (테이블 추가)

이전 문서와 달리 **새 DB(`ocr_review`)를 만드는 게 아니라, 기존 `mielin`
DB 안에 검수용 테이블을 추가**하는 작업입니다.

**로컬(Windows) PC에서** 스키마+데이터를 덤프합니다 (한글 깨짐 방지를 위해
`utf8mb4` 옵션 필수):

```bash
"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqldump.exe" \
  --default-character-set=utf8mb4 -h 127.0.0.1 -u root -p \
  ocr_review > ocr_review_dump.sql
```

> ⚠️ **덤프 파일 안의 `CREATE DATABASE`/`USE ocr_review` 구문 주의**
> 이 옵션으로 뜬 덤프 파일 안에는 로컬 DB 이름(`ocr_review`)을 기준으로 한
> `CREATE DATABASE`/`USE` 구문이 들어있지 않지만(위 예시엔 `--databases`를
> 쓰지 않았으므로 없음), 만약 다른 방식으로 다시 뜨게 되면 이 구문이 포함될 수
> 있습니다. 복원 전에 덤프 파일을 열어 `CREATE DATABASE`/`USE` 줄이 있는지
> 확인하고, 있다면 지운 뒤 진행하세요 — 그대로 실행하면 `mielin`이 아니라
> 별도의 `ocr_review` DB가 새로 생겨버립니다.

로컬 → EC2로 전송:

```bash
scp -i <key.pem> ocr_review_dump.sql ubuntu@<EC2 퍼블릭 IP>:~/
```

**EC2에서** `mielin` DB로 복원 (대상 DB명이 `ocr_review`가 아니라 `mielin`인
것에 주의):

```bash
mysql --default-character-set=utf8mb4 -u root -p mielin < ~/ocr_review_dump.sql
```

검증 (대상 DB도 `mielin`으로):

```bash
mysql -u <계정> -p mielin -e "
  SELECT 'ocr_reviewers' t, COUNT(*) c FROM ocr_reviewers
  UNION ALL SELECT 'ocr_review_comments', COUNT(*) FROM ocr_review_comments
  UNION ALL SELECT 'ocr_admin_comments', COUNT(*) FROM ocr_admin_comments;
"
```

**하루 약 520건씩 계속 쌓이는 데이터라 특정 숫자를 이 문서에 고정해두면 금방
틀린 값이 됩니다** — 덤프 뜨기 직전에 **로컬에서 같은 쿼리를 먼저 돌려서** 그
값과 위 EC2 결과가 같은지 비교하세요:

```powershell
"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe" -h 127.0.0.1 -u root -p ocr_review -e "
  SELECT 'ocr_reviewers' t, COUNT(*) c FROM ocr_reviewers
  UNION ALL SELECT 'ocr_review_comments', COUNT(*) FROM ocr_review_comments
  UNION ALL SELECT 'ocr_admin_comments', COUNT(*) FROM ocr_admin_comments;
"
```

계정 비밀번호(bcrypt 해시)도 그대로 옮겨지므로 `technonia01`/`technonia02`/`admin`
계정은 기존 비밀번호로 바로 로그인할 수 있습니다 — 새로 시딩할 필요 없습니다.

> ⚠️ **덤프 전에 `ocr_reviewers`를 한 번 훑어보세요.** 이 프로젝트와 같은 로컬
> MySQL을 공유하는 다른 프로젝트(예: 실험용 자동화 스크립트)가 실수로 이 DB에
> 테스트 계정·데이터를 직접 써넣는 사고가 실제로 있었습니다. `role='annotator'
> AND is_active=1`인데 `technonia01`/`technonia02`가 아닌 계정이 있다면, 그건
> 검수자 화면에 즉시 노출되는 상태이므로 프로덕션으로 옮기기 전에 지우거나
> 비활성화하세요.

> ⚠️ **테이블명 충돌 확인**: 이제 검수용 테이블이 `mielin` DB 안, 즉 SCT 원본
> 테이블들과 같은 네임스페이스에 생깁니다. 복원 전에 `SHOW TABLES FROM mielin;`
> 으로 `ocr_reviewers`, `ocr_review_comments`, `ocr_admin_comments` 등과 겹치는
> 이름이 이미 없는지 확인하세요.

덤프 파일(`ocr_review_dump.sql`)은 검수 데이터 원본이 그대로 들어있으니 이관이
끝나면 로컬/EC2 양쪽에서 삭제하거나 안전한 곳으로만 옮겨두세요.

---

## 4단계 — 소스 코드 전달

로컬 프로젝트를 통째로 압축해 옮기되, **아래는 제외**하고 압축합니다 (이미
`.gitignore`에 의도가 드러나 있는 항목들 + 배포 소스에 불필요한 것들):

| 제외 대상 | 이유 |
|---|---|
| `.venv/` | Windows용 가상환경, EC2에서 `uv sync`로 새로 만듦 |
| `__pycache__/`, `*.pyc` | 캐시 |
| `app/.env` | 로컬 실제 자격증명 — 5단계에서 EC2용으로 새로 작성 |
| `.mysql/credentials.txt` | 로컬 MySQL root 비밀번호, EC2와 무관 |
| `.mysql/data/` | 로컬 MySQL 실제 데이터 파일(수백MB), 3단계 덤프로 이미 이관함 |
| `uvicorn_out.log` | 로컬 실행 로그 |

PowerShell/Git Bash에서 `tar`/`zip`으로 위 목록만 빼고 압축한 뒤 scp로
전송하고, EC2에서 압축을 풉니다:

```bash
scp -i <key.pem> mielin_SCT.tar.gz ubuntu@<EC2 퍼블릭 IP>:~/
ssh -i <key.pem> ubuntu@<EC2 퍼블릭 IP>
mkdir -p ~/mielin_SCT && tar -xzf mielin_SCT.tar.gz -C ~/mielin_SCT
cd ~/mielin_SCT
ls  # SCT Questions.xlsx가 포함되어 있는지 꼭 확인 — question_master.py가 이 파일을 읽음
```

---

## 5단계 — `app/.env` 새로 작성 (두 DB 설정을 하나로 정렬)

로컬 `.env`에는 두 세트의 DB 설정이 분리되어 있었습니다:

- `MYSQL_*` — `mielin` 원본 데이터 접속용 (로컬에서는 원격 `43.201.172.24`)
- `REVIEW_MYSQL_*` — `ocr_review` 검수 데이터 접속용 (로컬에서는 로컬 MySQL `127.0.0.1`, DB명 `ocr_review`)

EC2에서는 두 데이터가 물리적으로 **같은 MySQL, 같은 `mielin` DB**에 있으므로,
이 두 세트를 아래처럼 맞춥니다:

```bash
cp app/.env.template app/.env
nano app/.env   # 또는 vim
```

| 항목 | 값 (EC2) |
|---|---|
| `MYSQL_HOST` | `127.0.0.1` |
| `MYSQL_DATABASE` | `mielin` |
| `MYSQL_USER` / `PASSWORD` | 기존에 이미 쓰이고 있는 mielin 접속 계정 정보 그대로 |
| `REVIEW_MYSQL_HOST` | `127.0.0.1` |
| `REVIEW_MYSQL_DATABASE` | `mielin` (기존 `ocr_review`가 아니라 **`mielin`으로 변경**) |
| `REVIEW_MYSQL_USER` / `PASSWORD` | 위 `MYSQL_USER`와 동일한 계정 재사용 (신규 발급 불필요) |
| `AWS_ACCESS_KEY_ID` / `SECRET` | **로컬 키를 재사용하지 말고 새로 발급**한 키 사용 |
| `S3_BUCKET` / `AWS_REGION` | `cmaps-hub` / `ap-northeast-2` (로컬과 동일) |
| `SESSION_SECRET_KEY` | 새로 생성 — 아래 명령 참고 |
| `SESSION_MAX_AGE_SECONDS` | 로컬과 동일하게 두거나 필요에 맞게 조정 |

> ⚠️ **소스 코드 확인이 한 번 더 필요합니다**: 앱 코드가 `MYSQL_*`과
> `REVIEW_MYSQL_*`를 완전히 독립된 두 개의 DB 커넥션(풀)으로 다루고 있다면,
> 위 표처럼 값만 맞춰주는 것으로 충분히 동작합니다 — 같은 서버의 같은 DB에
> 커넥션이 두 개 열리는 것뿐이라 문제는 없습니다. 다만 두 커넥션이 이제
> 완전히 동일한 대상을 가리키게 되므로, 이 기회에 커넥션을 하나로 합치는
> 리팩터링을 할지는 실제 커넥션 생성 코드(`app/db.py` 등)를 보고 판단하세요.
> 이 문서만으로는 코드 구조까지 단정할 수 없어 값 정렬까지만 반영했습니다.

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

## 6단계 — 파이썬 의존성 설치

```bash
cd ~/mielin_SCT
uv sync
```

`pyproject.toml`에 `requires-python = ">=3.12,<3.13"`으로 고정돼 있으므로, 1단계에서
Python 3.12가 제대로 설치됐어야 이 단계가 성공합니다.

---

## 7단계 — systemd 서비스로 등록

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
ExecStart=/home/ubuntu/.local/bin/uv run uvicorn app.main:app --host 0.0.0.0 --port 8011
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

- `ExecStart`의 `uv` 경로는 `which uv`로 실제 경로를 확인해 맞추세요.
- `User`는 소스가 있는 계정과 맞춰야 합니다(예시는 `ubuntu`).
- `mysql.service`는 이미 이 인스턴스에서 다른 용도로도 쓰이고 있으므로,
  이 앱의 systemd 유닛을 내려도(`stop`) MySQL 자체는 영향받지 않습니다.

```bash
sudo systemctl daemon-reload
sudo systemctl enable sct-review
sudo systemctl start sct-review
sudo systemctl status sct-review   # active (running) 확인
```

---

## 8단계 — 보안그룹/방화벽 재확인 (IP 화이트리스트 기준)

- **8011 인바운드**: **허용된 IP 화이트리스트만** (개별 IP 또는 소규모 목록으로
  등록, `0.0.0.0/0` 금지)
- **22(SSH) 인바운드**: 관리자 IP만 허용
- **3306(MySQL)**: 인바운드 규칙 자체를 만들지 않습니다 — `bind-address 127.0.0.1`로
  이미 외부 접속이 막혀 있으므로 이중으로 안전합니다. (기존 `mielin` 운영
  설정을 그대로 유지 — 새로 손댈 필요 없음)
- **아웃바운드**: S3(HTTPS/443)로 나가는 트래픽만 확인하면 됩니다 — `mielin`
  DB가 이제 로컬 접속이므로, 이전 문서에 있던 "mielin 쪽 방화벽이 EC2
  아웃바운드를 허용하는지" 확인은 **더 이상 필요 없습니다.**

---

## 9단계 — 동작 확인

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

## 10단계 — 운영 체크리스트

- **정기 백업**: `mielin` DB에 이미 백업 정책(예: RDS 스냅샷, 별도 cron 등)이
  있는지 먼저 확인하세요. 검수 테이블이 이제 `mielin` DB 안에 있으므로, 기존
  백업 범위에 자동으로 포함됩니다. 없다면 아래처럼 새로 구성합니다.
  ```bash
  crontab -e
  # 예: 매일 새벽 3시
  0 3 * * * mysqldump --default-character-set=utf8mb4 -u root -p'비밀번호' mielin | gzip > /home/ubuntu/backups/mielin_$(date +\%F).sql.gz
  ```
- **로그 확인**: `sudo journalctl -u sct-review -f`
- **재부팅 후 자동 기동 확인**: `systemctl is-enabled sct-review` (`mysql`은
  기존 운영 설정을 그대로 따르므로 별도로 건드리지 않습니다)
- **보안 패치**: `sudo apt update && sudo apt upgrade` 주기적으로 적용 —
  단, `mielin`을 함께 운영 중인 인스턴스이므로 패치 전 담당자와 조율
- **디스크 용량**: `/var/lib/mysql` 용량 모니터링 — 이제 검수 데이터 증가분도
  같은 볼륨에 쌓입니다

---

## 나중에 도메인/HTTPS를 붙이게 되면

지금은 화이트리스트 기반 HTTP 전용이라 그대로 두면 되지만, 나중에 외부 도메인
+ HTTPS로 바꾸게 되면 아래 두 가지를 같이 챙기세요.

1. nginx를 리버스 프록시로 앞에 두고 Let's Encrypt(`certbot`)로 TLS 인증서 발급.
2. `app/main.py`의 `SessionMiddleware`에 `https_only=True`를 추가해 세션 쿠키가
   HTTPS로만 전송되게 해야 합니다(지금은 HTTP 전용 환경이라 꺼져 있는 게 맞습니다).

---

## 부록 — 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 브라우저에서 접속 자체가 안 됨 | 보안그룹 8011 인바운드에 접속 IP가 화이트리스트로 등록돼 있는지, `systemctl status sct-review` |
| 로그인 화면은 뜨는데 로그인 실패 | `.env`의 `REVIEW_MYSQL_*` 값, `mysql -u <계정> -p mielin`로 직접 접속 테스트 |
| 목록 조회 시 500/빈 화면 | `.env`의 `MYSQL_*` 값, `mielin` DB 테이블이 3단계에서 정상 이관됐는지 |
| 이미지가 안 보임 | `.env`의 `AWS_*`/`S3_BUCKET` 값, IAM 키 권한(cmaps-hub 버킷 GetObject) |
| 롤백이 필요할 때 | 3단계에서 만든 덤프로 `mielin` DB의 검수 테이블만 다시 복원 (SCT 원본 테이블은 건드리지 않도록 주의) |
