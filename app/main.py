from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from . import question_master, text_diff
from .auth import AuthError, authenticate, safe_next_path
from .client import SctClient
from .config import load_settings
from .review_client import (
    AdminCommentNotFoundError,
    DuplicateAdminCommentError,
    DuplicateReviewError,
    InvalidReferenceError,
    NotOwnerError,
    ReviewDbClient,
    ReviewNotFoundError,
)
from .s3_client import S3ImageClient

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
REVIEW_HTML_PATH = APP_DIR / "review.html"
ADMIN_HTML_PATH = APP_DIR / "admin.html"
LOGIN_HTML_PATH = APP_DIR / "login.html"

# §4.1 "내가 아직 처리하지 않은 것" 필터가 제외 목록으로 끌어올 수 있는 최대 키 수.
# 넘어가면 잘렸다는 사실을 응답에 실어 보낸다 (조용히 틀리게 두지 않는다).
MY_REVIEWED_KEYS_LIMIT = 20000

# 일괄 패스 1회 상한 (§4.2). 그리드 한 페이지(24건)보다 넉넉하게 두되,
# "화면에서 확인 가능한 규모"를 넘지 않도록 제한한다.
BULK_PASS_MAX = 50

# 난이도 필터는 여러 개를 동시에 받는다(2026-08-24). ge/le는 **리스트가 아니라
# 각 항목**에 걸어야 한다 — 리스트에 직접 걸면 pydantic이 비교를 못 해 500이 난다.
DifficultyLevel = Annotated[int, Field(ge=1, le=5)]

# admin 열람(§4.5)이 한 번에 훑는 검수 기록 키의 상한.
#
# 예전에는 1,000건이었는데, 검수가 쌓이면 그 이상은 **아무 표시 없이** 목록에서
# 사라지는 구조였다(2026-08-24 발견). 전수 검사 대상이 44,823건이라 반드시
# 도달하는 값이었고, 그때는 admin이 "전체"를 골라도 오래된 기록을 못 보게 된다.
# 전체 데이터셋을 덮고도 남는 값으로 올리고, 그래도 걸리면 응답에 경고를 실어
# 보낸다 — 조용히 틀린 목록을 보여주지 않는 것이 이 프로젝트의 원칙이다.
#
# 상한을 아예 없애지 않은 이유: 이 키들로 검수 DB와 원격 mielin에 각각
# IN (...) 배치 조회를 날리는데, 키가 수만 개가 되면 쿼리 문자열 자체가
# 수 MB가 되어 max_allowed_packet에 걸리거나 매우 느려진다. 근본 해결은
# 필터링·페이지네이션을 SQL로 내리는 것이고, 이는 §7의 성능 과제로 남아 있다.
ADMIN_SCAN_LIMIT = 50000

# "판독 불가" 난이도 (§5.1). 패스로는 저장할 수 없고(모순), 텍스트와 동시에
# 성립할 수도 없으므로 이 값은 곧 "판독 불가 판정"과 등가다.
UNREADABLE_DIFFICULTY_LEVEL = 5

settings = load_settings()
client = SctClient(settings)
s3_client = S3ImageClient(settings.s3)
review_client = ReviewDbClient(settings)

app = FastAPI(title="SCT 데이터 조회", version="0.1.0")

if not settings.auth.session_secret:
    raise RuntimeError("SESSION_SECRET_KEY가 설정되어 있지 않습니다 (app/.env 확인)")

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.auth.session_secret,
    session_cookie="sct_session",
    max_age=settings.auth.session_max_age_seconds,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ============================================================
# 요청 모델
#
# reviewer_id / admin_id는 클라이언트가 보내지 않는다 — 세션에서 가져온다.
# 예전에는 로그인이 없어 클라이언트가 직접 지정했는데, 그러면 로그인한
# 누구나 남의 이름으로 검수 의견을 남길 수 있다.
# ============================================================
class LoginRequest(BaseModel):
    username: str
    password: str


class ReviewSubmission(BaseModel):
    """OCR 검수 시나리오.md §4.2/§4.3 — 패스 또는 타이핑 처리 1건.

    ocr_difficulty_level(1~5, §5)은 타이핑 처리에서 분류를 대체한 필드다
    (08_add_review_difficulty_level.sql). contains_negative_expression(§5)은
    패스/타이핑 공통으로, OCR 텍스트에 부정 표현이 있으면 화면이 기본값을
    미리 채워 보내지만 검수자가 직접 켜고 끌 수도 있다.
    """

    assessment_id: int
    drawing_id: int
    answer_index: int
    review_type: Literal["normal_check", "transcription"]
    vlm_model: str | None = None
    typed_text: str | None = None
    # 화면에 보였던 OCR 텍스트. **참고용이며 서버는 이 값을 신뢰하지 않는다**
    # (2026-08-24) — 비교 기준이자 스냅샷으로 저장되는 값은 서버가 원본
    # DB(mielin)에서 직접 다시 읽는다(_authoritative_ocr_text). mielin에 닿지
    # 못할 때만 이 값으로 물러난다. 스냅샷을 남기는 이유 자체는 그대로다:
    # 원본은 다른 DB에 있어 나중에 JOIN으로 되찾을 수 없고, OCR이 갱신되면
    # "그때 무엇과 비교했는가"도 사라진다.
    ocr_text: str | None = None
    ocr_difficulty_level: int | None = Field(default=None, ge=1, le=5)
    contains_negative_expression: bool = False
    comment: str | None = Field(default=None, max_length=1000)


class AdminCommentSubmission(BaseModel):
    """OCR 검수 시나리오.md §4.5 — admin이 완료된 레코드에 남기는 참고용 코멘트.

    난이도(difficulty_level)는 두 검수자 의견이 갈릴 때의 **비블라인드 중재값**
    이다 — 검수자 원본과 별개 컬럼에 담기며 완료 판정에 영향을 주지 않는다.
    """

    assessment_id: int
    drawing_id: int
    answer_index: int
    # 난이도만 남기고 코멘트를 비워도 되게 했다 (2026-08-24) — 중재값만 찍고
    # 넘어가는 경우가 많아 억지로 텍스트를 쓰게 만들 이유가 없다. 대신
    # "난이도 또는 코멘트 중 하나 이상"을 엔드포인트에서 검증한다.
    comment: str = Field(default="", max_length=1000)
    difficulty_level: int | None = Field(default=None, ge=1, le=5)


class AdminCommentUpdate(BaseModel):
    """admin 본인이 남긴 난이도/코멘트 수정 (§4.5) — 검수자의 §4.3과 같은 방식."""

    comment: str = Field(default="", max_length=1000)
    difficulty_level: int | None = Field(default=None, ge=1, le=5)


class BulkPassItem(BaseModel):
    """§5.1(2026-08-21) — 패스도 난이도가 필수라, 일괄 패스 항목도 건별로
    난이도를 실어 보낸다. 판독 불가(5)는 패스와 논리적으로 성립하지 않는
    조합이라 1~4로 제한한다(§4.2 구현 메모 참고)."""

    assessment_id: int
    drawing_id: int
    answer_index: int
    vlm_model: str | None = None
    ocr_text: str | None = None
    ocr_difficulty_level: int = Field(ge=1, le=4)
    contains_negative_expression: bool = False


class BulkPassSubmission(BaseModel):
    """§4.2 일괄 패스 — 그리드에서 체크한 여러 건을 한 번에 패스 처리한다.

    상한을 두는 이유: 화면에 보이지도 않는 수천 건을 한 번에 밀어버리는 것을
    막기 위함이다. 검수는 "이미지를 실제로 봤다"는 전제가 성립해야 의미가
    있으므로, 한 화면에서 눈으로 확인할 수 있는 규모로 제한한다.
    """

    items: list[BulkPassItem] = Field(min_length=1, max_length=BULK_PASS_MAX)


class ReviewUpdate(BaseModel):
    """§4.3 — 본인이 남긴 의견 수정 (오타 교정, 패스↔타이핑 전환 등).

    수정 직전 값은 ocr_review_edits에 보존된다 — §1의 "원래 독립 판단을 수정
    없이 보존" 원칙을 지키기 위함이다.

    2026-08-21 — 패스도 수정 대상이다(원래는 타이핑만 가능했다). 난이도는
    항상 필수이고, review_type은 저장된 값이 아니라 **매번 이 두 필드에서
    다시 계산**한다:
      - typed_text가 있으면(패스였어도 텍스트를 채우면) → transcription
      - 없고 ocr_difficulty_level == 5(판독 불가)면 → transcription
      - 없고 1~4면 → normal_check (패스)
    즉 "패스 → 텍스트를 채워 저장 → 패스 표시가 사라지고 타이핑으로 분류"가
    이 규칙 그대로 동작한다. 반대로 타이핑에서 텍스트를 지우고 1~4를 남기면
    패스로 전환된다(기존 동작 유지).
    """

    typed_text: str = ""
    ocr_difficulty_level: int = Field(ge=1, le=5)
    contains_negative_expression: bool = False
    comment: str | None = Field(default=None, max_length=1000)
    # diff는 저장된 ocr_text_snapshot과 다시 비교해 서버가 계산한다 —
    # 수정 요청은 OCR 텍스트를 다시 보내지 않는다 (비교 기준이 최초 검수
    # 시점의 스냅샷으로 고정되어야 하기 때문).


class ReviewStateQuery(BaseModel):
    """검수 화면이 목록 한 페이지분의 검수 상태를 한 번에 가져올 때 쓴다.

    행마다 GET을 날리면 페이지당 20회 왕복인데, 검수 DB 커넥션이 하나뿐이라
    (ReviewDbClient의 RLock) 전부 직렬화되어 목록이 눈에 띄게 느려진다.
    """

    keys: list[tuple[int, int, int]] = Field(default_factory=list, max_length=200)


# ============================================================
# 인증 / 인가
# ============================================================
def require_login(request: Request) -> dict[str, Any]:
    reviewer = request.session.get("reviewer")
    if not reviewer:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    return reviewer


def require_admin(reviewer: dict[str, Any] = Depends(require_login)) -> dict[str, Any]:
    """§2/§4.5 — Admin 화면은 role='admin' 계정만 볼 수 있다."""
    if reviewer.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin 권한이 필요합니다")
    return reviewer


def _login_redirect(request: Request) -> RedirectResponse | None:
    if request.session.get("reviewer"):
        return None
    return RedirectResponse(f"/login?next={quote(request.url.path)}")


def _require_review_db() -> None:
    if not review_client.enabled:
        raise HTTPException(
            status_code=503,
            detail="검수 DB가 아직 설정되지 않았습니다 (REVIEW_MYSQL_*)",
        )


# ============================================================
# 공통 헬퍼
# ============================================================
# 09_add_review_negative_expression.sql — 부정 표현 자동 감지 키워드.
# 매 요청마다 DB를 다시 조회하지 않도록 캐시해두고, 필요하면 재시작으로
# 갱신한다 (관리 화면 없이 시딩 스크립트/직접 SQL로만 바뀌는 값이라 §7의
# "계정 관리 화면" 항목과 같은 종류의 미해결 과제).
_negative_keywords_cache: list[str] | None = None


def _negative_keywords() -> list[str]:
    global _negative_keywords_cache
    if _negative_keywords_cache is None:
        _negative_keywords_cache = (
            review_client.fetch_negative_keywords() if review_client.enabled else []
        )
    return _negative_keywords_cache


def _auto_negative_flag(text: str | None) -> bool:
    if not text:
        return False
    return any(keyword in text for keyword in _negative_keywords())


def _is_typed(review: dict[str, Any]) -> bool:
    """"타이핑한 것"에 해당하는 의견인가 — **판독 불가를 포함한다**.

    검수자 화면의 "내 처리 상태 > 타이핑한 것"과 같은 기준(review_type)이다
    (2026-08-24). 한동안 admin만 `typed_text` 유무로 좁혀 봤는데, 그러면 같은
    이름의 필터가 두 화면에서 다른 답을 내고 진행 현황 카드와도 어긋났다.

    판독 불가도 "패스가 아닌 판정"이라는 점에서 타이핑 경로로 제출되며(§5.1),
    검수자가 이미지를 보고 판단을 내렸다는 점은 같다. 판독 불가만 따로 보고
    싶으면 난이도 5로 조회한다 — 판독 불가는 난이도 5로만 저장되므로 등가다.
    """
    return review["review_type"] == "transcription"


def _is_unreadable(review: dict[str, Any]) -> bool:
    """판독 불가 판정인가 — 패스가 아니면서 옮겨 적은 내용이 없는 의견.

    난이도 5를 직접 보지 않고 "transcription인데 텍스트가 없다"로 판정하는
    이유는 이 조건이 **구조적 불변식**이기 때문이다: 텍스트 없이 난이도
    1~4를 낸 제출은 서버가 패스로 재분류하므로(submit_review/update_review의
    같은 규칙), transcription으로 남은 채 텍스트가 없으면 난이도는 5이거나
    난이도 도입(2026-08-21) 이전의 레거시(NULL)뿐이다.

    난이도 5만 보면 레거시 데이터가 세 필터(타이핑/판독 불가/패스) 어디에도
    걸리지 않고 "전체"에서만 보이는 사각지대가 생긴다 — 실제로 그런 행이
    있었다(난이도 도입 이전에 남긴 건들).
    이 정의는 그것까지 판독 불가로 흡수해서, 세 필터가 레코드 전체를 빠짐없이
    나누도록(partition) 만든다.
    """
    return review["review_type"] == "transcription" and not review["typed_text"]


def _authoritative_ocr_text(
    keys: list[tuple[int, int, int]], sent: dict[tuple[int, int, int], str | None]
) -> dict[tuple[int, int, int], str | None]:
    """검수 시점 OCR 텍스트를 **원본 DB(mielin)에서 직접** 읽어온다 (§5.3).

    예전에는 화면이 보낸 payload.ocr_text를 그대로 ocr_text_snapshot에
    저장했다. 그런데 이 값은 diff 계산의 유일한 비교 기준이고, 수정(PATCH)은
    ocr_text를 받지 않으므로 **한번 잘못 들어가면 화면에서는 영원히 고칠 수
    없다.** 실제로 잘못된 값이 들어간 레코드에서 "OCR과 똑같이 입력했는데
    전부 다르다고 표시되는" 문제가 발생했다(2026-08-24). 화면 버그나 오래된
    카드 데이터로도 같은 일이 생길 수 있고, 그러면 그 레코드의 diff는 틀린
    채로 §6 분석에 들어간다 — 블라인드/난이도 검증과 마찬가지로 서버가
    스스로 확인해야 하는 값이다.

    mielin에 닿지 못하면 화면이 보낸 값으로 물러난다 — 검수 자체를 막는 것보다
    낫고, 정상 경로에서는 화면도 서버가 내려준 같은 값을 그대로 돌려보낸다.
    """
    try:
        found = client.fetch_records_by_keys(keys)
    except Exception:
        return sent
    return {
        key: (found[key].get("ocr_text") if key in found else sent.get(key))
        for key in keys
    }


def _with_negative_origin(
    review: dict[str, Any], fallback_ocr_text: str | None
) -> dict[str, Any]:
    """§5.2 — 부정 표현 표시가 자동 감지에서 온 것인지 검수자 판단인지 구분한다.

    admin이 "자동감지"와 "검수자 판단"을 나눠 볼 수 있게 하려는 값이다.
    저장된 것은 검수자가 최종 확정한 contains_negative_expression 하나뿐이라,
    자동 감지가 그때 무엇이라고 했는지는 여기서 다시 계산한다.

    판정 기준 텍스트는 **검수 당시 화면에 보였던 ocr_text_snapshot**이다 —
    지금의 OCR 텍스트로 다시 감지하면 그 사이 OCR이 갱신된 경우 당시와 다른
    답이 나온다. 스냅샷이 없는 과거 데이터만 현재 OCR 텍스트로 대신한다.

    검수자가 자동 감지를 껐다면 contains_negative_expression이 False이므로
    화면은 아무것도 표시하지 않는다 — "부정 표현 단어가 들어 있다고 해서 모두
    부정 표현인 것은 아니다"라는 사람의 판단을 그대로 따른다(2026-08-24).
    """
    basis = review.get("ocr_text_snapshot") or fallback_ocr_text
    review["auto_negative_flag"] = _auto_negative_flag(basis)
    return review


def _with_marked_diff(review: dict[str, Any]) -> dict[str, Any]:
    """§5.3 — 저장된 diff 세그먼트로 대괄호 표기를 만들어 함께 내려보낸다.

    화면이 세그먼트에서 직접 그려도 되지만, 표기 규칙(특히 delete를 어느 쪽
    글자로 보여주는가)이 화면과 서버에 따로 있으면 어긋나기 쉽다. 규칙은
    text_diff 한 곳에만 둔다.
    """
    review["ocr_diff_marked"] = text_diff.render_marked(
        review.get("typed_text"), review.get("ocr_diff")
    )
    return review


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (datetime, date)):
            safe[key] = value.isoformat()
        else:
            safe[key] = value
    return safe


def _attach_edit_summary(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """각 의견에 수정 횟수(edit_count)와 완료 후 수정 여부를 붙인다.

    이전 값 자체는 더 이상 저장하지도 보여주지도 않는다 (2026-08-24, §8) —
    최종 검수 결과가 목표이고 입력 중의 오타 수정까지 남길 이유가 없다는
    판단이다. 횟수만 남기는 이유는 fetch_edit_summary의 주석 참고.
    """
    if not reviews:
        return reviews
    summary = review_client.fetch_edit_summary([r["id"] for r in reviews])
    for r in reviews:
        s = summary.get(r["id"], {})
        r["edit_count"] = s.get("edit_count", 0)
        r["edited_after_completed"] = s.get("edited_after_completed", False)
    return reviews


def _blind_state(reviews: list[dict[str, Any]], viewer_id: int) -> dict[str, Any]:
    """§1/§3/§4.3 블라인드 원칙을 서버에서 강제한다.

    시나리오 §3: "본인이 제출하기 전: 다른 검수자가 이미 처리했는지, 무엇으로
    처리했는지 전혀 알 수 없음". 즉 내용뿐 아니라 **처리 여부 자체**도 숨겨야
    하므로, 내가 제출하기 전에는 review_count/status까지 전부 가린다.
    프론트엔드에서만 가리면 개발자도구로 그대로 들여다볼 수 있다.

    2026-08-21 변경 — 완료(2/2) 후에도 상대 검수자의 내용은 절대 공개하지
    않는다. 예전에는 완료되면 비교를 위해 양쪽을 다 내려줬는데(화면은 이미
    "내 것만" 그려서 안 보여줬지만, API 응답 자체에는 담겨 있어 개발자도구로
    보면 새어나갔다), "검수자 간에 내용을 확인할 필요가 없다"는 판단에 따라
    응답 자체에서도 뺐다. admin 화면의 비교 기능(§4.5)은 이 함수를 쓰지 않는
    완전히 별도의 엔드포인트(require_admin)라 이 변경과 무관하다.

    2026-08-21 추가 변경 — review_count/status(=상대방이 몇 명 처리했는지)도
    더는 내려주지 않는다. 검수자 화면은 "완료 2명 중 몇 명"을 몰라도 되는
    독립 운영 화면이라, 이 값이 남아있으면 "상대가 아직 안 왔다"는 사실
    자체가 새어나간다 — 내용은 아니지만 §1의 "독립적 판단"과는 무관한
    정보이므로 함께 없앤다. 이제 이 필드는 오직 **본인 제출 여부**만 알려준다
    (진행 현황 집계는 admin 화면(§4.5)의 역할로, 거기는 여전히 건수를 센다).
    """
    mine = next((r for r in reviews if r["reviewer_id"] == viewer_id), None)
    if mine is None:
        return {"reviews": [], "mine_submitted": False}

    _attach_edit_summary([mine])
    return {
        "reviews": [_with_marked_diff(_json_safe(mine))],
        "mine_submitted": True,
    }


# ============================================================
# 페이지
# ============================================================
@app.get("/login", response_model=None)
def login_page(request: Request) -> FileResponse | RedirectResponse:
    if request.session.get("reviewer"):
        return RedirectResponse(safe_next_path(request.query_params.get("next")))
    return FileResponse(LOGIN_HTML_PATH)


@app.get("/", response_model=None)
def index(request: Request) -> RedirectResponse:
    """예전 'SCT 데이터 조회' 화면이 있던 자리.

    그 화면은 OCR 검수 화면과 역할이 거의 겹쳐서 없애고, 고유 기능(SCT 질문
    컬럼 / VLM 상태 필터)만 /review로 옮겼다. 기존 북마크가 깨지지 않도록
    루트는 유지한다.

    admin 계정은 그리드(검수 입력 화면)를 쓸 일이 없고 admin 열람 화면이
    사실상 홈이므로 곧장 /admin으로 보낸다 — 그래서 review.html의 "Admin
    열람" 링크도 없앴다(가는 곳이 곧 시작점이라 링크가 무의미해짐). 로그인
    전이거나 일반 검수자면 그대로 /review로 보낸다.
    """
    reviewer = request.session.get("reviewer")
    if reviewer and reviewer.get("role") == "admin":
        return RedirectResponse("/admin")
    return RedirectResponse("/review")


@app.get("/review", response_model=None)
def review(request: Request) -> FileResponse | RedirectResponse:
    """검수 입력 그리드 — 검수자 계정 전용.

    admin은 그리드를 쓸 일이 없으므로(index() 주석) /admin으로 돌려보낸다.

    역할 분기를 "/"에만 두면 **로그인 폼을 거치는 경로에서 admin이 이 화면에
    떨어진다**(2026-08-27 발견). 로그아웃 상태의 "/"가 /review로 보내는 탓에
    로그인 URL이 /login?next=/review가 되고, login.js는 role이 아니라 next만
    보고 이동하기 때문이다. 헤더는 /api/auth/me로 따로 그리므로 "관리자
    (admin)"인데 내용은 검수자 화면인 상태가 된다. 세션이 살아 있으면 "/"가
    /admin으로 잘 가서 평소엔 안 보이고, 세션이 만료된 다음 재로그인할 때만
    드러나 재현이 안 되는 것처럼 보였다.

    그래서 목적지 계산이 아니라 화면 자체에 가드를 둔다 — 북마크로 /review를
    직접 열든 next로 오든 진입 경로와 무관하게 한 곳에서 막힌다. 비-admin이
    /admin에서 403을 받는 것과 대칭이다.
    """
    redirect = _login_redirect(request)
    if redirect:
        return redirect
    if request.session["reviewer"].get("role") == "admin":
        return RedirectResponse("/admin")
    return FileResponse(REVIEW_HTML_PATH)


@app.get("/admin", response_model=None)
def admin_page(request: Request) -> FileResponse | RedirectResponse:
    redirect = _login_redirect(request)
    if redirect:
        return redirect
    # §4.5 Admin 열람/코멘트 화면 — admin 계정만.
    if request.session["reviewer"].get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin 권한이 필요합니다")
    return FileResponse(ADMIN_HTML_PATH)


# ============================================================
# 인증 API
# ============================================================
@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request) -> dict[str, Any]:
    _require_review_db()
    try:
        reviewer = authenticate(
            review_client,
            username=payload.username,
            password=payload.password,
            ip=request.client.host if request.client else None,
        )
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    request.session["reviewer"] = reviewer
    return reviewer


@app.post("/api/auth/logout")
def logout(request: Request) -> dict[str, Any]:
    request.session.clear()
    return {"ok": True}


@app.get("/api/auth/me")
def me(reviewer: dict[str, Any] = Depends(require_login)) -> dict[str, Any]:
    return reviewer


# ============================================================
# SCT 원본 데이터 (mielin, 읽기 전용)
# ============================================================
@app.get("/api/sct/filters", dependencies=[Depends(require_login)])
def get_filters() -> dict[str, Any]:
    return client.fetch_filter_options()


@app.get("/api/sct/records")
def get_records(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    hospital_id: int | None = Query(default=None),
    age_group: str | None = Query(default=None),
    ocr_failed: bool | None = Query(default=None),
    vlm_model: str | None = Query(default=None),
    date_start: date | None = Query(default=None),
    date_end: date | None = Query(default=None),
    keyword: str | None = Query(default=None),
    has_image: bool | None = Query(
        default=None, description="이미지가 있는 레코드만/없는 레코드만"
    ),
    mine: Literal["all", "unreviewed", "pass", "typing"] = Query(
        default="all", description="§4.1 — 내 처리 상태 기준 필터"
    ),
    difficulty_level: list[DifficultyLevel] | None = Query(
        default=None,
        description=(
            "내가 남긴 난이도 기준 필터 (내 것만 — 블라인드와 무관). "
            "여러 번 넘기면 그중 하나라도 해당하면 통과한다(예: 1,2만 보기). "
            "안 넘기면 전체 — 1~5를 모두 넘긴 것과 다르다(난이도 없는 과거 데이터 포함 여부)"
        ),
    ),
    negative_only: bool | None = Query(
        default=None, description="§5.2 — OCR 텍스트에 부정 표현이 감지된 레코드만"
    ),
    exclude_unreadable: bool | None = Query(
        default=None,
        description=(
            "내가 판독 불가로 판정한 건을 뺀다 (§5.1). 검수한 내용 위주로 보기 위한"
            " 기본 동작이라 화면 위젯이 이 값으로 시작한다"
        ),
    ),
    unreadable_only: bool | None = Query(
        default=None, description="내가 판독 불가로 판정한 건만 (§5.1)"
    ),
    reviewer: dict[str, Any] = Depends(require_login),
) -> dict[str, Any]:
    page_size = min(page_size, settings.max_page_size)

    # §4.1 "내 처리 상태" 필터. 검수 DB와 mielin은 서로 다른 서버일 수 있어
    # JOIN이 안 되므로, 내 처리 키를 먼저 뽑아 제외(unreviewed)하거나
    # 그 키만 남긴다(pass/typing).
    exclude_keys: list[tuple[int, int, int]] | None = None
    include_keys: list[tuple[int, int, int]] | None = None
    keys_truncated = False

    if mine != "all":
        _require_review_db()
        review_type = {"pass": "normal_check", "typing": "transcription"}.get(mine)
        keys, keys_truncated = review_client.fetch_my_reviewed_keys(
            reviewer_id=reviewer["id"],
            review_type=review_type,
            limit=MY_REVIEWED_KEYS_LIMIT,
        )
        if mine == "unreviewed":
            exclude_keys = keys
        else:
            include_keys = keys

    # 난이도는 **내가 남긴 값**이라 아직 처리하지 않은 건에는 존재하지 않는다.
    # 그래서 "아직 처리하지 않은 것"에서는 아예 무시한다 — 어떤 값으로 걸러도
    # 0건이 되어 검수자의 기본 작업 화면이 통째로 비어버리기 때문이다.
    #
    # 나머지 상태에서는 그대로 좁힌다. 난이도 필터의 기본값이 "전체(1~5)"라
    # 사용자가 직접 고르기 전에는 아무것도 걸리지 않으므로, 좁혔을 때 처리한
    # 건만 남는 것이 오히려 기대에 맞는다("판독 불가 제외"는 이제 별도
    # 체크박스가 담당한다 — 2026-08-24).
    if difficulty_level and mine != "unreviewed":
        _require_review_db()
        level_keys, level_truncated = review_client.fetch_my_reviewed_keys(
            reviewer_id=reviewer["id"],
            difficulty_levels=difficulty_level,
            limit=MY_REVIEWED_KEYS_LIMIT,
        )
        keys_truncated = keys_truncated or level_truncated
        if include_keys is not None:
            include_set = set(include_keys)
            include_keys = [k for k in level_keys if k in include_set]
        else:
            include_keys = level_keys

    # §5.1 판독 불가 3단 선택(제외 / 포함 / 만 보기). 난이도와 마찬가지로 **내가
    # 남긴 판정**이라 미처리 건에는 존재하지 않으므로, "아직 처리하지 않은 것"에
    # 서는 적용하지 않는다 — 적용하면 "제외"는 무해하지만 "만 보기"가 0건이 되어
    # 기본 작업 화면이 비어버린다(난이도와 같은 이유).
    if (exclude_unreadable or unreadable_only) and mine != "unreviewed":
        _require_review_db()
        unreadable_keys, unread_truncated = review_client.fetch_my_reviewed_keys(
            reviewer_id=reviewer["id"],
            unreadable_only=True,
            limit=MY_REVIEWED_KEYS_LIMIT,
        )
        keys_truncated = keys_truncated or unread_truncated
        if unreadable_only:
            keep = set(unreadable_keys)
            include_keys = (
                [k for k in include_keys if k in keep]
                if include_keys is not None
                else list(keep)
            )
        elif unreadable_keys:
            drop = set(unreadable_keys)
            if include_keys is not None:
                include_keys = [k for k in include_keys if k not in drop]
            else:
                exclude_keys = list(set(exclude_keys or []) | drop)

    # §5.2 "부정 표현 포함" — 내가 확정한 판단이 자동 감지보다 우선한다
    # (2026-08-24). 자세한 조합 규칙은 client.fetch_records의 해당 주석 참고.
    negative_keywords = None
    negative_flagged_keys = None
    negative_reviewed_keys = None
    if negative_only:
        _require_review_db()
        negative_keywords = review_client.fetch_negative_keywords()
        negative_flagged_keys, flagged_truncated = review_client.fetch_my_reviewed_keys(
            reviewer_id=reviewer["id"],
            negative_flagged=True,
            limit=MY_REVIEWED_KEYS_LIMIT,
        )
        negative_reviewed_keys, reviewed_truncated = review_client.fetch_my_reviewed_keys(
            reviewer_id=reviewer["id"],
            limit=MY_REVIEWED_KEYS_LIMIT,
        )
        keys_truncated = keys_truncated or flagged_truncated or reviewed_truncated

    result = client.fetch_records(
        page=page,
        page_size=page_size,
        hospital_id=hospital_id,
        age_group=age_group,
        ocr_failed=ocr_failed,
        vlm_model=vlm_model,
        date_start=date_start,
        date_end=date_end,
        keyword=keyword,
        has_image=has_image,
        negative_keywords=negative_keywords,
        negative_flagged_keys=negative_flagged_keys,
        negative_reviewed_keys=negative_reviewed_keys,
        exclude_keys=exclude_keys,
        include_keys=include_keys,
    )
    items = [_json_safe(item) for item in result["items"]]
    for item in items:
        item["sct_question"] = question_master.get_question_text(
            item.get("sct_age_group"), item.get("question_number")
        )
        # §5 부정 표현 자동 감지 — OCR 텍스트 기준으로 미리 계산해 내려주면,
        # 화면은 이 값으로 체크박스를 미리 켠 채 보여줄 수 있다 (검수자가
        # 패스하는 경우까지 포함해서 놓치지 않기 위함).
        item["auto_negative_flag"] = _auto_negative_flag(item.get("ocr_text"))
    result["items"] = items
    result["image_enabled"] = s3_client.enabled
    if keys_truncated:
        result["warning"] = (
            f"처리 이력이 {MY_REVIEWED_KEYS_LIMIT}건을 넘어 '내 처리 상태' 필터 결과가 "
            "일부 부정확할 수 있습니다."
        )
    return result


@app.get("/api/sct/records/{record_id}/image", dependencies=[Depends(require_login)])
def get_record_image(record_id: int) -> RedirectResponse:
    if not s3_client.enabled:
        raise HTTPException(status_code=404, detail="S3 이미지 연동이 아직 구성되지 않았습니다")

    s3_key = client.fetch_record_image_key(record_id)
    if not s3_key:
        raise HTTPException(status_code=404, detail="이미지가 없습니다")

    url = s3_client.presign(s3_key)
    if not url:
        raise HTTPException(status_code=404, detail="이미지 URL을 생성할 수 없습니다")

    return RedirectResponse(url)


# ============================================================
# OCR 검수 (§4.2~§4.4)
# ============================================================
@app.post("/api/ocr/reviews", status_code=201)
def submit_review(
    payload: ReviewSubmission, reviewer: dict[str, Any] = Depends(require_login)
) -> dict[str, Any]:
    _require_review_db()

    # §5.1(2026-08-21) — 난이도는 패스/타이핑 구분 없이 항상 필요하다. 텍스트
    # 자체는 "판독 불가"처럼 옮겨 적을 내용이 없을 수 있어 선택 사항이지만,
    # "얼마나 어려운 필기였나"는 모든 처리에서 답해야 하는 질문이기 때문이다.
    if payload.ocr_difficulty_level is None:
        raise HTTPException(status_code=400, detail="OCR 난이도(1~5) 선택이 필요합니다")
    if payload.review_type == "normal_check":
        if payload.typed_text:
            raise HTTPException(status_code=400, detail="패스 처리는 typed_text를 가질 수 없습니다")
        if payload.ocr_difficulty_level == UNREADABLE_DIFFICULTY_LEVEL:
            # "일치(패스)"와 "판독 불가"는 동시에 성립할 수 없는 판정이다 —
            # 읽을 수 없는데 OCR이 맞다고 확인할 수는 없기 때문이다.
            raise HTTPException(
                status_code=400,
                detail="판독 불가(5)는 패스로 처리할 수 없습니다 — 타이핑 처리를 이용해주세요",
            )
    # 판독 불가(5)는 "읽을 수 없다"는 뜻이라 텍스트가 있으면 모순이다.
    # update_review에는 같은 검증이 있었는데 여기에는 빠져 있어서, 화면이
    # 막아주는 동안에만 안전했다(review.js의 같은 검사). API를 직접 호출하면
    # "판독 불가인데 텍스트가 있는" 행이 그대로 저장됐다 — 블라인드와 마찬가지로
    # 이 규칙도 서버가 강제해야 한다. 이 불변식이 깨지면 난이도 5로 판독 불가를
    # 골라내는 필터(§4.5)와 화면 표시(§5.3)가 함께 어긋난다.
    if payload.typed_text and payload.ocr_difficulty_level == UNREADABLE_DIFFICULTY_LEVEL:
        raise HTTPException(
            status_code=400, detail="텍스트가 있으면 판독 불가(5)로 저장할 수 없습니다"
        )

    # 비교 기준이 되는 OCR 텍스트는 화면이 보낸 값이 아니라 원본 DB에서 다시
    # 읽는다 — 이유는 _authoritative_ocr_text 참고.
    key = (payload.assessment_id, payload.drawing_id, payload.answer_index)
    ocr_text = _authoritative_ocr_text([key], {key: payload.ocr_text})[key]

    # §5.3 — 차이는 서버가 계산한다. 클라이언트가 보낸 diff를 그대로 믿으면
    # 화면 버그나 조작으로 "차이 없음"이 쌓여도 알아챌 수 없고, 이 값은 §6
    # 분석의 근거 데이터라 단일 소스여야 한다.
    #
    # 텍스트 없이 난이도만 제출한 경우(판독 불가 등, §4.3)는 비교할 대상이
    # 없다 — "전부 다르다"가 아니라 "비교 자체를 안 했다"는 뜻이어야 한다.
    # 빈 문자열을 OCR 원문과 비교하면 SequenceMatcher가 원문 전체를 delete로
    # 처리해버려서, "판독 불가"가 "OCR이 이 답변을 통째로 잘못 읽었다"는
    # 정반대의 강한 주장으로 저장되는 문제가 있었다.
    final_review_type = payload.review_type
    if payload.review_type == "transcription" and payload.typed_text:
        diff = text_diff.compute_diff(ocr_text, payload.typed_text)
        diff_segments = diff["segments"] or None
        diff_char_count = diff["char_count"]
        spacing_diff = diff["spacing_diff"]
        # §5.5(2026-08-21) — 직접 타이핑했더라도 OCR과 글자 하나도 다르지
        # 않으면 결과적으로 "OCR이 맞다"는 패스와 같은 판단이다. 패스 버튼을
        # 눌렀는지 직접 다시 쳐서 확인했는지는 검수 과정의 차이일 뿐이라,
        # review_type이 다르면 §6 분석에서 이 케이스가 "OCR이 틀렸다"로
        # 잘못 집계된다. 다시 쳐서 확인했다는 사실 자체는 typed_text에 그대로
        # 남는다 — 지우지 않는다.
        if diff_char_count == 0:
            final_review_type = "normal_check"
    else:
        # 패스, 그리고 텍스트 없는 타이핑(판독 불가 등) 둘 다 비교 대상이 없다.
        diff_segments = None
        diff_char_count = None
        spacing_diff = False

    # §5.5 추가 — 체크박스 없이 [제출]만 눌러 텍스트를 아예 입력하지 않은
    # 경우도(판독 불가가 아니면) 결과적으로 패스와 같다 — "비교할 게 없다"는
    # "OCR이 틀렸다"는 뜻이 아니다. update_review의 재계산 규칙과 반드시
    # 같아야 한다 — 그렇지 않으면 새로 제출할 때와 기존 걸 수정할 때
    # review_type이 서로 다른 기준으로 갈려버린다.
    if (
        final_review_type == "transcription"
        and not payload.typed_text
        and payload.ocr_difficulty_level != UNREADABLE_DIFFICULTY_LEVEL
    ):
        final_review_type = "normal_check"

    try:
        review_id = review_client.submit_review(
            assessment_id=payload.assessment_id,
            drawing_id=payload.drawing_id,
            answer_index=payload.answer_index,
            reviewer_id=reviewer["id"],
            review_type=final_review_type,
            vlm_model=payload.vlm_model,
            typed_text=payload.typed_text,
            ocr_text_snapshot=ocr_text,
            ocr_diff=diff_segments,
            ocr_diff_char_count=diff_char_count,
            spacing_diff=spacing_diff,
            ocr_difficulty_level=payload.ocr_difficulty_level,
            contains_negative_expression=payload.contains_negative_expression,
            comment=payload.comment,
        )
    except DuplicateReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    reviews = review_client.fetch_reviews(
        assessment_id=payload.assessment_id,
        drawing_id=payload.drawing_id,
        answer_index=payload.answer_index,
    )
    return {"id": review_id, "state": _blind_state(reviews, reviewer["id"])}


@app.post("/api/ocr/reviews/bulk", status_code=201)
def submit_reviews_bulk(
    payload: BulkPassSubmission, reviewer: dict[str, Any] = Depends(require_login)
) -> dict[str, Any]:
    """§4.2 일괄 패스 — 그리드에서 체크한 건들을 한 번에 패스 처리.

    패스만 대상이다. 타이핑은 건별로 내용/난이도가 달라 묶을 수 없다.
    """
    _require_review_db()

    # 단건 제출과 같은 이유로 스냅샷은 원본 DB에서 다시 읽는다
    # (_authoritative_ocr_text 참고). 여러 건이라 한 번에 배치 조회한다.
    bulk_keys = [
        (item.assessment_id, item.drawing_id, item.answer_index) for item in payload.items
    ]
    ocr_texts = _authoritative_ocr_text(
        bulk_keys,
        {
            (item.assessment_id, item.drawing_id, item.answer_index): item.ocr_text
            for item in payload.items
        },
    )

    outcomes = review_client.submit_passes_bulk(
        items=[
            {
                "assessment_id": item.assessment_id,
                "drawing_id": item.drawing_id,
                "answer_index": item.answer_index,
                "vlm_model": item.vlm_model,
                # 패스에는 diff가 없지만, "무엇을 보고 맞다고 판단했는가"는
                # 남긴다 (§5.3의 스냅샷과 같은 목적).
                "ocr_text_snapshot": ocr_texts[
                    (item.assessment_id, item.drawing_id, item.answer_index)
                ],
                "ocr_difficulty_level": item.ocr_difficulty_level,
                "contains_negative_expression": item.contains_negative_expression,
            }
            for item in payload.items
        ],
        reviewer_id=reviewer["id"],
    )

    # 제출 후 상태를 한 번에 다시 읽어 화면이 바로 반영할 수 있게 한다
    keys = [(o["assessment_id"], o["drawing_id"], o["answer_index"]) for o in outcomes]
    all_reviews = review_client.fetch_reviews_for_keys(keys)
    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for row in all_reviews:
        grouped.setdefault(
            (row["assessment_id"], row["drawing_id"], row["answer_index"]), []
        ).append(row)

    states = {
        f"{a}:{d}:{i}": _blind_state(grouped.get((a, d, i), []), reviewer["id"])
        for a, d, i in keys
    }
    counts = {
        "created": sum(1 for o in outcomes if o["result"] == "created"),
        "duplicate": sum(1 for o in outcomes if o["result"] == "duplicate"),
        "invalid_reference": sum(1 for o in outcomes if o["result"] == "invalid_reference"),
    }
    return {"outcomes": outcomes, "counts": counts, "states": states}


@app.patch("/api/ocr/reviews/{review_id}")
def update_review(
    review_id: int,
    payload: ReviewUpdate,
    reviewer: dict[str, Any] = Depends(require_login),
) -> dict[str, Any]:
    """§4.3 — 본인이 남긴 의견 수정 (패스 포함, 2026-08-21부터).

    남의 의견은 절대 수정할 수 없다 (403). 수정 전 값은 이력으로 보존된다.
    """
    _require_review_db()

    if payload.typed_text and payload.ocr_difficulty_level == UNREADABLE_DIFFICULTY_LEVEL:
        # 판독 불가(5)는 "읽을 수 없다"는 뜻이라 텍스트가 있으면 모순이다
        # (submit_review와 같은 규칙).
        raise HTTPException(
            status_code=400, detail="텍스트가 있으면 판독 불가(5)로 저장할 수 없습니다"
        )

    try:
        info = review_client.update_review(
            review_id=review_id,
            reviewer_id=reviewer["id"],
            typed_text=payload.typed_text,
            ocr_difficulty_level=payload.ocr_difficulty_level,
            contains_negative_expression=payload.contains_negative_expression,
            comment=payload.comment,
            # 스냅샷이 비어 있는 과거 데이터(2026-08-21 이전)일 때만 호출된다 —
            # 비교 대상이 없어 입력 전체가 "다르다"로 기록되던 문제를 막는다.
            # review_client는 mielin을 모르므로 조회를 함수로 넘겨준다.
            resolve_ocr_text=lambda a, d, i: _authoritative_ocr_text(
                [(a, d, i)], {(a, d, i): None}
            )[(a, d, i)],
        )
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotOwnerError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    reviews = review_client.fetch_reviews(
        assessment_id=info["assessment_id"],
        drawing_id=info["drawing_id"],
        answer_index=info["answer_index"],
    )
    return {
        "state": _blind_state(reviews, reviewer["id"]),
        # 완료(2/2) 상태에서의 수정이었는지 — 상대 내용을 봤다는 뜻은 아니다
        # (완료 후에도 공개되지 않는다), 그래도 화면이 경고를 띄울 수 있게 알려준다
        "was_completed": info["was_completed"],
        # 빈 텍스트로 수정해 패스로 전환됐는지 — 화면이 안내 문구를 바꿀 수 있게
        "turned_into_pass": info["turned_into_pass"],
    }


@app.get("/api/ocr/reviews")
def get_reviews(
    assessment_id: int = Query(...),
    drawing_id: int = Query(...),
    answer_index: int = Query(...),
    reviewer: dict[str, Any] = Depends(require_login),
) -> dict[str, Any]:
    _require_review_db()
    reviews = review_client.fetch_reviews(
        assessment_id=assessment_id, drawing_id=drawing_id, answer_index=answer_index
    )
    return _blind_state(reviews, reviewer["id"])


@app.post("/api/ocr/review-states")
def get_review_states(
    payload: ReviewStateQuery, reviewer: dict[str, Any] = Depends(require_login)
) -> dict[str, Any]:
    """목록 한 페이지분의 검수 상태를 쿼리 1번으로 가져온다 (블라인드 규칙 적용)."""
    _require_review_db()
    if not payload.keys:
        return {"states": {}}

    keys = [tuple(key) for key in payload.keys]
    all_reviews = review_client.fetch_reviews_for_keys(keys)

    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for row in all_reviews:
        grouped.setdefault(
            (row["assessment_id"], row["drawing_id"], row["answer_index"]), []
        ).append(row)

    states = {
        f"{a}:{d}:{i}": _blind_state(grouped.get((a, d, i), []), reviewer["id"])
        for a, d, i in keys
    }
    return {"states": states}


# ============================================================
# Admin 열람 (§4.5) — admin 계정 전용
# ============================================================
@app.get("/api/ocr/reviewers", dependencies=[Depends(require_admin)])
def get_reviewers() -> dict[str, Any]:
    _require_review_db()
    return {"items": review_client.fetch_reviewers()}


@app.get("/api/ocr/admin/progress", dependencies=[Depends(require_admin)])
def get_admin_progress() -> dict[str, Any]:
    """§4.5 — 전체 진행 현황 요약.

    개별 의견 내용을 노출하지 않고 건수만 집계하므로 블라인드 원칙과 무관하다.

    completed/in_progress는 **패스를 포함한** 모든 처리 건이다 (2026-08-24).
    예전에는 타이핑이 없는 레코드를 빼서 목록 화면 건수와 어긋났는데, 검수자가
    이미지를 보고 "OCR이 맞다"고 판정한 것도 처리한 것이므로 함께 센다.
    이제 completed + in_progress = 검수 기록이 있는 전체 레코드다.

    total_images는 실제 검수 대상 이미지 수다 — s3_key가 없는 빈 레코드는
    목록에서도 has_image로 걸러지므로 카드도 같은 기준으로 센다.
    """
    _require_review_db()
    summary = review_client.fetch_progress_summary()
    in_progress_by_reviewer = review_client.fetch_in_progress_by_reviewer()

    try:
        stats = client.fetch_stats()
    except Exception:
        # mielin 연결이 잠깐 끊겨도 검수 진행 현황은 계속 보여줄 수 있어야 한다.
        stats = {"total_assessments": None, "total_clients": None, "total_images": None}

    # SUM()은 MySQL이 Decimal로 돌려줘서 그대로 두면 JSON에 문자열("47088")로
    # 나간다 — 화면의 숫자 포맷(toLocaleString)이 먹히지 않으므로 int로 맞춘다.
    total_images = stats.get("total_images")
    return {
        "total_assessments": stats.get("total_assessments"),
        "total_clients": stats.get("total_clients"),
        "total_images": int(total_images) if total_images is not None else None,
        "completed": summary.get("completed", 0),
        "in_progress": summary.get("needs_second_opinion", 0),
        # admin.js가 검수자별 진행중 카드를 그리는 데 쓴다 — 이름은 여기서 붙이지
        # 않는다(reviewer_id만 준다). 화면이 이미 /api/ocr/reviewers로 받아둔
        # 이름표(annotatorOrder/reviewerNames)를 그대로 재사용하면 되므로, 여기서
        # 또 조인해서 중복으로 이름을 내려줄 필요가 없다.
        "in_progress_by_reviewer": [
            {"reviewer_id": reviewer_id, "records": records}
            for reviewer_id, records in in_progress_by_reviewer.items()
        ],
    }


@app.get("/api/ocr/admin/records", dependencies=[Depends(require_admin)])
def get_admin_records(
    difficulty_level: list[DifficultyLevel] | None = Query(
        default=None,
        description="여러 번 넘기면 그중 하나라도 남긴 의견이 있는 레코드를 통과시킨다",
    ),
    negative_only: bool | None = Query(
        default=None, description="부정 표현 포함(§5)으로 표시된 의견이 있는 레코드만"
    ),
    diff_only: bool | None = Query(
        default=None,
        description="OCR과 실제로 다른 부분이 있는 의견만 (§5.3 — 차이 글자 수 > 0)",
    ),
    diff_min_chars: int | None = Query(
        default=None, ge=1, description="차이 글자 수가 이 값 이상인 의견이 있는 레코드만"
    ),
    reviewer_id: int | None = Query(default=None),
    age_group: str | None = Query(default=None),
    vlm_model: str | None = Query(default=None),
    keyword: str | None = Query(
        default=None,
        description="검수자 화면과 같은 기준(assessment_id/환자명/답변 내용) 부분일치 검색",
    ),
    typed_only: bool | None = Query(
        default=None,
        description="텍스트를 실제로 옮겨 적은 의견이 있는 레코드만 (판독 불가는 제외)",
    ),
    unreadable_only: bool | None = Query(
        default=None,
        description=(
            "판독 불가 의견이 있는 레코드만. 화면 드롭다운에서는 빠졌고"
            "(난이도 5 필터가 같은 역할을 한다) 서버에만 남아 있다 — 난이도 값이"
            " 없는 과거 데이터까지 구조적으로 잡는 유일한 경로이기 때문이다"
        ),
    ),
    pass_only: bool | None = Query(
        default=None, description="패스(일치 판정)한 의견이 있는 레코드만"
    ),
    exclude_unreadable: bool | None = Query(
        default=None,
        description=(
            "판독 불가 의견이 있는 레코드를 뺀다 (§5.1). 검수한 내용 위주로 보기"
            " 위한 기본 동작이라 화면 체크박스는 켜진 채로 시작한다"
        ),
    ),
    date_start: date | None = Query(default=None),
    date_end: date | None = Query(default=None),
    status: Literal["completed", "needs_second_opinion"] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """OCR 검수 시나리오.md §4.5 — admin 열람 화면.

    처리 기록이 있는 레코드를 완료/진행중 모두 스캔한다. mielin과 검수 DB가
    서로 다른 서버일 수 있어 SQL JOIN이 안 되므로, 자연 키로 각각 조회한
    뒤 여기서 합친다. 필터(difficulty_level/negative_only/diff_only/
    diff_min_chars/reviewer_id/age_group/
    vlm_model/typed_only/pass_only/date_start/date_end/status)는 지금은
    스캔 결과를 파이썬에서 거르는 단순한 방식이다 — 데이터가 아주 많아지면
    이 스캔 자체를 최적화해야 한다(§7 성능 항목과 같은 종류의 과제). age_group/
    vlm_model/날짜 필터링을 위해 스캔된 키 전체의 SCT 메타데이터를
    페이지네이션 전에 미리 가져온다.

    typed_only/unreadable_only/pass_only는 reviewer_id 필터와 결합되면 의미가
    달라진다 — 특정 검수자를 선택했다면 "그 검수자 본인이" 그렇게 판정했는지를
    보고, 전체 검수자일 때는 "누구든 한 명이라도" 그랬는지를 본다. pass_only만
    반대 방향이다: "타이핑도 판독 불가도 한 명 없는지"를 본다(둘 다 패스했거나,
    한 명은 패스하고 다른 한 명은 아직 처리 전인 경우까지 포함).

    **판독 불가는 타이핑에서 분리한다** (2026-08-24) — DB에는 둘 다
    `review_type='transcription'`으로 저장되지만, "읽을 수 없어 판정을 못 했다"와
    "OCR이 틀려서 다시 적었다"는 §6 분석에서 정반대 의미다. 그래서 typed_only는
    review_type이 아니라 typed_text 유무를 보고(_is_typed), 판독 불가는
    unreadable_only로 따로 조회한다(_is_unreadable). 검수자가 서로 다르게 판정한
    레코드(A=타이핑, B=판독 불가)는 "한 명이라도" 기준이므로 양쪽 필터에 모두
    나온다 — 의견이 갈렸다는 사실 자체가 정보이므로 의도한 동작이다.

    화면에는 typed_only/diff_only/unreadable_only/pass_only가 상호 배타적인 단일
    드롭다운(타이핑한 것/OCR과 다른 것/판독 불가/패스한 것/전체 — 한 번에 하나만
    켠다)으로, negative_only는 검수자 화면(review.html)과 같은 방식으로
    독립된 체크박스로 나가 있다 — 부정 표현은 타이핑/패스 어느 쪽과도
    같이 걸릴 수 있는 별개의 속성이라 드롭다운의 배타적 선택에 끼워 넣으면
    "타이핑 + 부정 표현" 같은 조합 조회가 막히기 때문이다.

    아무 필터도 지정하지 않으면(화면의 "전체") 전체 레코드를 다 보여준다 —
    admin은 A/B 검수자가 남긴 내용을 모두 확인할 수 있어야 하기 때문이다.

    의견이 하나라도 등록된 레코드는 내용을 공개한다. admin도 검수자를
    겸할 수 있지만(§2), 소수 인원으로 전수 검사를 진행하는 현재 운영
    방식상 admin이 진행 상황을 빠르게 파악하는 것이 더 중요하다고
    판단하여, 두 번째 의견을 기다리지 않고 첫 의견만으로도 연다.
    """
    _require_review_db()

    scanned, scan_truncated = review_client.fetch_review_keys(scan_limit=ADMIN_SCAN_LIMIT)
    keys = [(row["assessment_id"], row["drawing_id"], row["answer_index"]) for row in scanned]

    all_reviews = review_client.fetch_reviews_for_keys(keys)
    all_comments = review_client.fetch_admin_comments_for_keys(keys)

    try:
        # age_group/vlm_model 필터링에 SCT 메타데이터가 필요하므로, 페이지네이션
        # 전에 스캔된 키 전체에 대해 미리 가져온다 (§4.5 표시용 조회도 겸함).
        sct_lookup = client.fetch_records_by_keys(keys)
    except Exception:
        # mielin(원본 SCT 데이터) 연결이 잠깐 끊겨도 검수 데이터 자체는 계속
        # 보여줄 수 있어야 하므로, OCR 텍스트/이미지 병합 실패는 조용히 넘어간다.
        sct_lookup = {}

    # 질문 텍스트는 원본 컬럼(r.sct_question) 대신 엑셀 마스터에서 채운다
    # (2026-08-27). 원본 컬럼은 49,331건 중 35,198건(71%)이 NULL이고 — 아동은
    # 8,469건 중 6,670건 — 값이 있어도 `{textArea}` 플레이스홀더가 섞여 있어,
    # admin 화면에서 질문이 통째로 비어 보였다. 검수자 화면(/api/sct/records)은
    # 이미 같은 마스터로 덮어쓰고 있었고, admin 경로만 원본 컬럼을 그대로 쓰고
    # 있던 것이 원인이다. DB에 실제로 존재하는 (연령대, 문항번호) 조합 133개가
    # 모두 엑셀에 있으므로 이 치환으로 잃는 건 없다. 엑셀에 없는 조합이 생기면
    # 원본 값을 그대로 남겨 최소한 무언가는 보이게 한다.
    for sct_row in sct_lookup.values():
        question = question_master.get_question_text(
            sct_row.get("sct_age_group"), sct_row.get("question_number")
        )
        if question:
            sct_row["sct_question"] = question

    reviews_by_key: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for row in all_reviews:
        key = (row["assessment_id"], row["drawing_id"], row["answer_index"])
        reviews_by_key.setdefault(key, []).append(row)

    comments_by_key: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for row in all_comments:
        key = (row["assessment_id"], row["drawing_id"], row["answer_index"])
        comments_by_key.setdefault(key, []).append(row)

    entries: list[dict[str, Any]] = []
    for key in keys:
        key_reviews = reviews_by_key.get(key)
        if not key_reviews:
            continue

        is_completed = len(key_reviews) >= 2
        entry_status = "completed" if is_completed else "needs_second_opinion"
        if status is not None and entry_status != status:
            continue

        # 의견이 하나라도 있으면 내용을 공개한다(§4.5, 완료 여부 무관) — 그래서
        # 아래 필터들은 공개 여부를 따지지 않는다. 예전에는 `disclosed` 플래그로
        # 가릴지 말지를 매번 확인했는데, 규칙이 "첫 의견부터 공개"로 바뀐 뒤로는
        # 항상 True인 상수였다(2026-08-27 제거).

        # 일치/불일치 판정은 계산하지 않는다(2026-08-27 제거). 기준이 "난이도값
        # 비교 + 패스는 전부 일치"였는데, 그러면 "둘 다 패스인데 난이도가 2 vs 1"인
        # 건을 놓쳐 실제 불일치 3건 중 1건만 잡혔다. 화면 노출은 2026-08-19부터
        # 보류된 상태로 서버 계산만 남아 있었고, 틀린 기준을 코드로 붙들고 있으면
        # 중재 대기열을 만들 때 그대로 물려받게 되므로 지웠다. 기준을 다시 정하는
        # 일은 §7의 미해결 과제로 남아 있다.
        if difficulty_level and not any(
            r["ocr_difficulty_level"] in difficulty_level for r in key_reviews
        ):
            continue
        if negative_only and not any(
            r["contains_negative_expression"] for r in key_reviews
        ):
            continue
        # §5.3 — "OCR과 다른 부분"만 모아 보는 필터. 검수자가 타이핑했지만
        # 결과가 OCR과 완전히 같았던 건(char_count == 0)은 제외된다.
        if diff_only and not any(
            (r["ocr_diff_char_count"] or 0) > 0 for r in key_reviews
        ):
            continue
        if diff_min_chars is not None and not any(
            (r["ocr_diff_char_count"] or 0) >= diff_min_chars for r in key_reviews
        ):
            continue
        if reviewer_id is not None and not any(
            r["reviewer_id"] == reviewer_id for r in key_reviews
        ):
            continue

        # 아래 세 필터는 화면의 단일 드롭다운에서 한 번에 하나만 켜진다.
        # 판정 종류를 review_type이 아니라 _is_typed/_is_unreadable로 보는
        # 이유는 그 두 헬퍼의 주석 참고 — "판독 불가"를 "타이핑"에서 떼어내기
        # 위한 것이다(2026-08-24).
        if typed_only:
            # 검수자를 특정해서 봤다면 "그 검수자가" 타이핑했는지를 본다.
            # 전체 검수자일 때는 누구든 한 명이라도 타이핑했으면 남긴다.
            if reviewer_id is not None:
                mine = next((r for r in key_reviews if r["reviewer_id"] == reviewer_id), None)
                if not mine or not _is_typed(mine):
                    continue
            elif not any(_is_typed(r) for r in key_reviews):
                continue

        if unreadable_only:
            if reviewer_id is not None:
                mine = next((r for r in key_reviews if r["reviewer_id"] == reviewer_id), None)
                if not mine or not _is_unreadable(mine):
                    continue
            elif not any(_is_unreadable(r) for r in key_reviews):
                continue

        # §5.1 "판독 불가 제외" — 한 명이라도 판독 불가로 판정했으면 뺀다.
        # 타이핑/패스 어느 상태와도 조합되는 독립 속성이라 드롭다운이 아니라
        # 체크박스가 담당한다(부정 표현과 같은 구조).
        if exclude_unreadable and any(_is_unreadable(r) for r in key_reviews):
            continue

        if pass_only:
            # "패스한 것" = 타이핑도 판독 불가도 한 명 없는 레코드. 검수자
            # 두 명 다 패스했든, 한 명만 패스하고 다른 한 명이 아직 처리
            # 전이든(key_reviews에 1건만 있음) 모두 여기 해당한다.
            # 판독 불가도 review_type이 transcription이므로 아래 조건 하나로
            # 타이핑과 판독 불가가 함께 걸러진다 — 의도한 동작이다("읽지
            # 못했다"는 "OCR이 맞다고 확인했다"가 아니므로 패스가 아니다).
            if reviewer_id is not None:
                mine = next((r for r in key_reviews if r["reviewer_id"] == reviewer_id), None)
                if not mine or mine["review_type"] != "normal_check":
                    continue
            elif any(r["review_type"] == "transcription" for r in key_reviews):
                continue

        # age_group/vlm_model/날짜는 검수 의견이 아니라 SCT 원본 메타데이터라
        # 블라인드 원칙과 무관하다 — 진행중 레코드에도 그대로 적용한다.
        sct_row = sct_lookup.get(key)
        if age_group and (not sct_row or sct_row.get("sct_age_group") != age_group):
            continue
        if vlm_model and (not sct_row or sct_row.get("vlm_model") != vlm_model):
            continue
        if keyword:
            # 검수자 화면(client.fetch_records)과 같은 대상(환자명/답변 내용/
            # assessment_id)을 본다. 다만 여긴 mielin에 SQL로 못 던지고(§3)
            # 이미 뽑아둔 sct_lookup을 파이썬에서 부분일치로 거른다.
            needle = keyword.lower()
            haystack = " ".join(
                str(v)
                for v in (
                    key[0],
                    sct_row.get("client_name") if sct_row else None,
                    sct_row.get("ocr_text") if sct_row else None,
                )
                if v is not None
            ).lower()
            if needle not in haystack:
                continue
        if date_start is not None or date_end is not None:
            ts = sct_row.get("source_created_at") or sct_row.get("imported_at") if sct_row else None
            ts_date = ts.date() if isinstance(ts, datetime) else ts
            if date_start is not None and (ts_date is None or ts_date < date_start):
                continue
            if date_end is not None and (ts_date is None or ts_date > date_end):
                continue

        entries.append(
            {
                "assessment_id": key[0],
                "drawing_id": key[1],
                "answer_index": key[2],
                "status": entry_status,
                "review_count": len(key_reviews),
                "reviews": key_reviews,
                "admin_comments": comments_by_key.get(key, []),
            }
        )

    total = len(entries)
    start = (page - 1) * page_size
    page_entries = entries[start : start + page_size]

    items = []
    for entry in page_entries:
        key = (entry["assessment_id"], entry["drawing_id"], entry["answer_index"])
        sct_row = sct_lookup.get(key)
        items.append(
            {
                **entry,
                "reviews": [
                    _with_negative_origin(
                        _with_marked_diff(_json_safe(r)),
                        sct_row.get("ocr_text") if sct_row else None,
                    )
                    for r in entry["reviews"]
                ],
                "admin_comments": [_json_safe(c) for c in entry["admin_comments"]],
                "sct": _json_safe(sct_row) if sct_row else None,
            }
        )

    result: dict[str, Any] = {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }
    if scan_truncated:
        # 조용히 잘린 채로 두지 않는다 — 검수자 화면의 keys_truncated와 같은 원칙.
        result["warning"] = (
            f"검수 기록이 조회 상한({ADMIN_SCAN_LIMIT:,}건)을 넘어 오래된 일부가 "
            "목록에서 빠졌습니다. 기간·검수자 필터로 범위를 좁혀서 확인해주세요."
        )
    return result


@app.post("/api/ocr/admin/comments", status_code=201)
def submit_admin_comment(
    payload: AdminCommentSubmission, admin: dict[str, Any] = Depends(require_admin)
) -> dict[str, Any]:
    """admin당 레코드당 코멘트는 1건만 — 이미 남겼으면 409 (PATCH로 수정)."""
    _require_review_db()
    # 난이도도 코멘트도 없으면 남길 내용이 없다 — 빈 코멘트만 쌓이면 "관리자가
    # 확인했다"는 신호로 오해될 수 있어 하나 이상을 요구한다 (2026-08-24).
    if payload.difficulty_level is None and not payload.comment.strip():
        raise HTTPException(
            status_code=400, detail="난이도 또는 코멘트 중 하나는 입력해야 합니다"
        )
    try:
        comment_id = review_client.add_admin_comment(
            assessment_id=payload.assessment_id,
            drawing_id=payload.drawing_id,
            answer_index=payload.answer_index,
            admin_id=admin["id"],
            comment=payload.comment.strip(),
            difficulty_level=payload.difficulty_level,
        )
    except DuplicateAdminCommentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": comment_id}


@app.patch("/api/ocr/admin/comments/{comment_id}")
def update_admin_comment(
    comment_id: int,
    payload: AdminCommentUpdate,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """§4.5 — admin 본인이 남긴 난이도/코멘트 수정. 남의 코멘트는 403."""
    _require_review_db()
    # 난이도도 코멘트도 없으면 남길 내용이 없다 — 빈 코멘트만 쌓이면 "관리자가
    # 확인했다"는 신호로 오해될 수 있어 하나 이상을 요구한다 (2026-08-24).
    if payload.difficulty_level is None and not payload.comment.strip():
        raise HTTPException(
            status_code=400, detail="난이도 또는 코멘트 중 하나는 입력해야 합니다"
        )
    try:
        review_client.update_admin_comment(
            comment_id=comment_id,
            admin_id=admin["id"],
            comment=payload.comment.strip(),
            difficulty_level=payload.difficulty_level,
        )
    except AdminCommentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotOwnerError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": comment_id}
