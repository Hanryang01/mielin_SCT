# AWS EC2 배포 가이드

이 문서는 로컬(Windows)에서 개발/운영해온 `SCT 데이터 조회 / OCR 검수` 앱을 AWS EC2로
옮기는 절차입니다. 아래 전제를 기준으로 작성했습니다 — 전제가 바뀌면 해당 단계만
다시 확인하면 됩니다.

- EC2 OS: **Ubuntu 22.04 또는 24.04**
- 웹 접속: **내부망/VPN에서만 HTTP** (도메인·TLS 없음, 지금 로컬처럼 포트 하나로 직접 접속)
- **새 EC2가 아니라, `mielin` 원본 DB가 이미 떠 있는 기존 EC2(`43.201.172.24`)를
  그대로 재사용한다** (2026-08-31 확정 — 이 서버를 우리가 직접 관리하게 됨).
  MySQL도 새로 설치하지 않고 **이미 떠 있는 MySQL 서버에 `ocr_review` 데이터베이스만
  추가**한다 (RDS 아님).
- **`mielin`은 그대로 둔다** — 여전히 SELECT 전용으로만 접근하고, 스키마·데이터는
  건드리지 않는다. 이 서버를 우리가 관리하게 됐다고 해서 mielin을 채우는 원본
  파이프라인(다른 프로세스/스케줄)에 손대는 건 아니다.

전체 그림: **EC2 인스턴스 1대**(기존 mielin 서버) 안에 (1) 이 앱(FastAPI/uvicorn),
(2) 원래 있던 `mielin` MySQL, (3) 새로 추가하는 `ocr_review` MySQL(같은 MySQL
서버 안의 별도 데이터베이스)이 모두 같이 떠 있습니다. `mielin`이 이제 원격이
아니라 **같은 인스턴스 안의 localhost 접속**으로 바뀌는 것이 이전 버전 가이드와
가장 큰 차이입니다 — 그래서 아웃바운드로 나갈 곳은 S3(`cmaps-hub`)뿐입니다.

---

## 0. 시작 전 확인할 것

- [ ] EC2 인스턴스 퍼블릭/프라이빗 IP, SSH 접속용 키(.pem) 확보
- [ ] 보안그룹에 최소 아래 두 인바운드 규칙이 있는지 (없으면 8단계에서 설정)
  - 22(SSH) — 관리자 IP만
  - 8011(HTTP) — 내부망/VPN 대역만 (전체 공개 금지)
- [ ] **기존 MySQL의 root 비밀번호를 시스템 관리자에게 받아둔다** — 이 서버는
      처음 켜는 빈 인스턴스가 아니라 이미 `mielin`이 돌고 있는 운영 서버이므로,
      `mysql_secure_installation`을 다시 돌리거나 root 비밀번호를 임의로
      재설정하면 안 된다(아래 2단계 참고).
- [ ] **`mielin`을 채우는 원본 배치/파이프라인이 이 서버의 어떤 계정·스케줄로
      도는지 확인**한다 — 우리가 하는 작업(DB 추가, MySQL 재시작 등)이 그
      파이프라인을 방해하지 않아야 한다.
- [ ] 로컬 PC에서 EC2로 파일을 옮길 수단(scp) 사용 가능한지

---

## 1단계 — EC2 접속 및 기본 패키지 설치

⚠️ **이미 운영 중인 서버입니다.** `sudo apt upgrade -y`는 mielin 파이프라인이
의존하는 패키지 버전을 건드릴 수 있으니, 시스템 관리자와 먼저 확인하고 진행하는
편이 안전합니다. `mysql-server`는 **이미 설치돼 있으므로 다시 설치하지 않습니다**
— 아래 목록에서 뺐습니다.

```bash
ssh -i <key.pem> ubuntu@<EC2 퍼블릭 IP>

sudo apt update
sudo apt install -y build-essential pkg-config git
mysql --version   # 이미 설치돼 있는지 확인만 — 8.0 계열이면 그대로 쓴다
```

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

## 2단계 — MySQL 설정 (`ocr_review` DB 추가)

Ubuntu 기본 저장소의 MySQL은 8.0 계열입니다(로컬 8.4.9와 마이너 버전이 다름). 이
프로젝트의 스키마(`01~13*.sql`)는 8.0에서도 지원하는 문법만 쓰므로 버전 차이는
문제 되지 않습니다.

⚠️ **`mysql_secure_installation`은 실행하지 않습니다.** 이미 `mielin`이 돌고 있는
운영 서버라, 이 명령이 건드리는 root 비밀번호·익명 계정·원격 root 로그인 허용
여부 등이 이미 mielin 쪽 운영 방식에 맞춰 설정돼 있을 수 있습니다. 대신 0단계에서
받아둔 **기존 root 비밀번호로 그대로 로그인**해서 우리 몫만 추가합니다.

`bind-address`가 어떻게 설정돼 있는지 **확인만** 합니다(바꾸지 않습니다 — 이미
mielin 쪽 접속 방식에 맞춰져 있을 것이므로, 바꾸려면 반드시 관리자와 먼저
상의하세요):

```bash
grep bind-address /etc/mysql/mysql.conf.d/mysqld.cnf
```

DB와 앱 전용 계정을 새로 추가합니다 — **`mielin` 데이터베이스나 기존 계정은
전혀 건드리지 않습니다**, 아래 SQL은 전부 새로 추가하는 것뿐입니다:

```sql
sudo mysql -u root -p
```
```sql
CREATE DATABASE ocr_review CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'sct_app'@'localhost' IDENTIFIED BY '새로운_비밀번호_여기에';
GRANT SELECT, INSERT, UPDATE ON ocr_review.* TO 'sct_app'@'localhost';
-- DELETE는 주지 않는다 — 검수 기록은 append-only 원칙 (README 참고)
-- mielin.*에는 아무 권한도 주지 않는다 — sct_app은 ocr_review만 본다
FLUSH PRIVILEGES;
```

> 로컬 `.mysql/credentials.txt`에 있던 비밀번호를 그대로 재사용하지 마세요 — EC2용
> 새 비밀번호를 발급하고, 안전한 곳(사내 비밀번호 관리자 등)에 별도 보관하세요.
> **이 EC2용 `sct_app` 비밀번호는 이 프로젝트 전용으로만 쓰세요** — 로컬에서
> 같은 계정 정보를 다른 프로젝트(자동화 스크립트 등)에 그대로 재사용했다가
> 그쪽이 실수로 이 DB에 직접 데이터를 써넣은 사고가 있었습니다.

**앱이 mielin을 읽는 계정도 다시 확인하세요.** 지금까지는 원격(로컬 PC → EC2)으로
`admin` 계정을 썼는데, 이제 앱이 같은 서버(localhost)에서 접속합니다. MySQL
계정은 접속 호스트별로 별도 등록이라(`'admin'@'%'`와 `'admin'@'localhost'`는
다른 계정), 기존 계정이 `localhost`에서의 접속을 허용하는지 확인이 필요합니다.
안 되면 관리자에게 **`localhost`용 SELECT 전용 계정**을 새로 하나 요청하세요 —
mielin 쪽 계정 관리는 여전히 관리자 소관입니다.

---

## 3단계 — 로컬 DB → EC2로 이관 (덤프/복원)

**로컬(Windows) PC에서** 덤프를 뜹니다 (한글 깨짐 방지를 위해 `utf8mb4` 옵션 필수):

```bash
"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqldump.exe" \
  --default-character-set=utf8mb4 -h 127.0.0.1 -u root -p \
  ocr_review > ocr_review_dump.sql
```

로컬 → EC2로 전송:

```bash
scp -i <key.pem> ocr_review_dump.sql ubuntu@<EC2 퍼블릭 IP>:~/
```

**EC2에서** 복원:

```bash
mysql --default-character-set=utf8mb4 -u root -p ocr_review < ~/ocr_review_dump.sql
```

검증:

```bash
mysql -u sct_app -p ocr_review -e "
  SELECT 'ocr_reviewers' t, COUNT(*) c FROM ocr_reviewers
  UNION ALL SELECT 'ocr_review_comments', COUNT(*) FROM ocr_review_comments
  UNION ALL SELECT 'ocr_admin_comments', COUNT(*) FROM ocr_admin_comments;
"
```
**하루 약 520건씩 계속 쌓이는 데이터라 특정 숫자를 이 문서에 고정해두면 금방
틀린 값이 됩니다** — 덤프 뜨기 직전에 **로컬에서 같은 쿼리를 먼저 돌려서** 그
값과 위 EC2 결과가 같은지 비교하세요:

```powershell
"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe" -h 127.0.0.1 -u sct_app -p ocr_review -e "
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

```powershell
# Windows(로컬)에서 압축 예시 — 위 항목만 제외
```

또는 PowerShell/Git Bash에서 `tar`/`zip`으로 위 목록만 빼고 압축한 뒤 scp로
전송하고, EC2에서 압축을 풉니다:

```bash
scp -i <key.pem> mielin_SCT.tar.gz ubuntu@<EC2 퍼블릭 IP>:~/
ssh -i <key.pem> ubuntu@<EC2 퍼블릭 IP>
mkdir -p ~/mielin_SCT && tar -xzf mielin_SCT.tar.gz -C ~/mielin_SCT
cd ~/mielin_SCT
ls  # SCT Questions.xlsx가 포함되어 있는지 꼭 확인 — question_master.py가 이 파일을 읽음
```

---

## 5단계 — `app/.env` 새로 작성

`app/.env.template`을 복사해 `app/.env`로 만들고 값을 채웁니다:

```bash
cp app/.env.template app/.env
nano app/.env   # 또는 vim
```

| 항목 | 값 |
|---|---|
| `MYSQL_HOST` (`mielin` 접속) | **`127.0.0.1`로 변경** — 로컬 PC에서는 원격(`43.201.172.24`)이었지만, 이제 앱과 mielin이 같은 인스턴스에 있으므로 localhost 접속입니다 |
| `MYSQL_USER` / `PASSWORD` | 위 2단계에서 확인한, `localhost`에서 접속 가능한 mielin 읽기 전용 계정 |
| `REVIEW_MYSQL_HOST` | `127.0.0.1` (같은 인스턴스의 MySQL, `ocr_review` DB) |
| `REVIEW_MYSQL_USER` / `PASSWORD` | `sct_app` / 2단계에서 만든 **새 비밀번호** |
| `REVIEW_MYSQL_DATABASE` | `ocr_review` |
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

```bash
sudo systemctl daemon-reload
sudo systemctl enable sct-review
sudo systemctl start sct-review
sudo systemctl status sct-review   # active (running) 확인
```

---

## 8단계 — 보안그룹/방화벽 재확인

- **8011 인바운드**: 내부망/VPN CIDR만 허용 (0.0.0.0/0 금지)
- **22(SSH) 인바운드**: 관리자 IP만 허용
- **3306(MySQL)**: 인바운드 규칙 자체를 만들지 않습니다 — mielin·ocr_review 둘 다
  같은 인스턴스 안에서 localhost로만 접속하므로, 3306을 외부에 열 이유가
  없습니다(기존에 3306 인바운드가 열려 있었다면, 왜 열려 있었는지 관리자와
  확인 후 필요 없으면 닫는 것도 검토하세요).
- **아웃바운드**: 이제 mielin은 같은 서버(localhost)라 별도 아웃바운드가
  필요 없습니다 — S3(HTTPS/443)로 나가는 트래픽만 막혀 있지 않은지 확인하면
  됩니다.

---

## 9단계 — 동작 확인

EC2 내부에서:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8011/
# 307(로그인 화면 리다이렉트)이면 정상
sudo journalctl -u sct-review -n 50 --no-pager   # 에러 없는지 확인
```

내부망/VPN에 연결된 PC에서 브라우저로 `http://<EC2 프라이빗 또는 퍼블릭 IP>:8011`
접속 → 로그인 화면이 뜨는지, 기존 계정(`technonia01`/`technonia02`/`admin`)으로
로그인이 되는지, `/review`·`/admin` 화면에서 목록·이미지가 정상적으로 보이는지
확인합니다. 이미지가 안 보이면 대개 `.env`의 S3 관련 값 또는 IAM 키 권한 문제입니다.

---

## 10단계 — 운영 체크리스트 (RDS가 아니므로 직접 챙겨야 하는 것들)

⚠️ **이 서버엔 이미 mielin용 백업/운영 정책이 있을 수 있습니다.** 아래 항목을
새로 만들기 전에 관리자에게 기존 백업 방식(있다면)을 먼저 물어보세요 — 중복
cron이 겹치거나, 기존 백업 대상에 `ocr_review`만 빠지는 일이 없게 하기 위함입니다.

- **정기 백업**: `cron`으로 매일 `mysqldump` 실행 후 별도 저장소(S3 등)에 보관
  (`ocr_review`만 대상 — `mielin`은 관리자의 기존 백업 정책을 따릅니다)
  ```bash
  crontab -e
  # 예: 매일 새벽 3시
  0 3 * * * mysqldump --default-character-set=utf8mb4 -u root -p'비밀번호' ocr_review | gzip > /home/ubuntu/backups/ocr_review_$(date +\%F).sql.gz
  ```
- **로그 확인**: `sudo journalctl -u sct-review -f`
- **재부팅 후 자동 기동 확인**: `systemctl is-enabled mysql sct-review` 둘 다
  `enabled`인지
- **보안 패치**: `sudo apt update && sudo apt upgrade` 주기적으로 적용
- **디스크 용량**: `/var/lib/mysql`에 이제 `mielin`과 `ocr_review`가 같이
  쌓입니다 — 우리 앱 때문에 mielin 쪽 여유 공간이 부족해지지 않도록 모니터링
  주기를 관리자와 맞춰두세요

---

## 나중에 도메인/HTTPS를 붙이게 되면

지금은 내부망 HTTP 전용이라 그대로 두면 되지만, 나중에 외부 도메인 + HTTPS로
바꾸게 되면 아래 두 가지를 같이 챙기세요.

1. nginx를 리버스 프록시로 앞에 두고 Let's Encrypt(`certbot`)로 TLS 인증서 발급.
2. `app/main.py`의 `SessionMiddleware`에 `https_only=True`를 추가해 세션 쿠키가
   HTTPS로만 전송되게 해야 합니다(지금은 HTTP 전용 환경이라 꺼져 있는 게 맞습니다).

---

## 부록 — 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 브라우저에서 접속 자체가 안 됨 | 보안그룹 8011 인바운드, `systemctl status sct-review` |
| 로그인 화면은 뜨는데 로그인 실패 | `.env`의 `REVIEW_MYSQL_*` 값, `mysql -u sct_app -p ocr_review`로 직접 접속 테스트 |
| 목록 조회 시 500/빈 화면 | `.env`의 `MYSQL_HOST`가 `127.0.0.1`인지, mielin 계정이 특정 IP로만 접속을 제한해둔 계정(`'user'@'옛_원격_IP'`처럼)은 아닌지 — `localhost`/`127.0.0.1`을 허용하는 계정이어야 함 |
| 이미지가 안 보임 | `.env`의 `AWS_*`/`S3_BUCKET` 값, IAM 키 권한(cmaps-hub 버킷 GetObject) |
| 롤백이 필요할 때 | 3단계에서 만든 덤프로 `ocr_review` DB를 다시 복원 |
