import {
  escapeHtml,
  showMessage,
  markedToHtml,
  setupImageModal,
  createPager,
} from "./ui-utils.js?v=2";
import { bindDateRanges } from "./date-range.js?v=2";
import { bindDifficultyFilter } from "./difficulty-filter.js?v=5";
import {
  PICKABLE_DIFFICULTY_LEVELS,
  UNREADABLE_LEVEL,
  describeDifficulty,
} from "./difficulty.js?v=2";

// OCR 검수 시나리오.md §4.5 — Admin 열람 및 코멘트 화면.
//
// 이 화면과 /api/ocr/admin/* 은 role='admin' 계정만 접근할 수 있다 (서버의
// require_admin이 403으로 막는다). 코멘트 작성자(admin_id)는 세션에서
// 결정되므로 화면에서 고르지 않는다.
//
// 여기서 남기는 코멘트는 참고용일 뿐 검수자 원본을 바꾸거나 완료 상태에
// 영향을 주지 않는다 (ocr_admin_comments는 v_sct_review_status 뷰에서 아예
// 참조하지 않음 — 03_ocr_review_schema.sql §5 참고).
const tableWrap = document.getElementById("recordTableWrap");

const progressBar = document.getElementById("progressBar");
// 난이도는 다중 선택 위젯이 대신한다 (difficulty-filter.js) — init에서 연결한다.
let difficultyFilter = { levels: () => [], unreadable: () => "exclude", reset: () => {} };
const reviewStatusFilter = document.getElementById("reviewStatusFilter");
// 부정 표현은 타이핑/OCR다름/패스 중 무엇이든 함께 걸릴 수 있는 별개의
// 속성이라(상호 배타적인 상태가 아니다), reviewStatusFilter 안에 넣지 않고
// 검수자 화면(review.html의 negativeOnly)과 같은 방식 — 독립 체크박스로
// 자유롭게 조합되게 뒀다.
const negativeOnly = document.getElementById("negativeOnly");
const ageGroupFilter = document.getElementById("ageGroupFilter");
const vlmModelFilter = document.getElementById("vlmModelFilter");
const reviewerFilter = document.getElementById("reviewerFilter");
const completionFilter = document.getElementById("completionFilter");
const keywordInput = document.getElementById("keyword");

// reviewStatusFilter(단일 드롭다운)의 값 하나가 서버 쪽 불리언 파라미터
// 하나에 대응한다 — "전체"는 이 중 아무것도 보내지 않는다는 뜻이라 매핑에
// 없다(main.py get_admin_records 참고).
// "타이핑한 것"은 검수자 화면과 같은 기준(review_type)이라 **판독 불가를
// 포함한다**(2026-08-24). 판독 불가만 보려면 난이도 5로 조회한다 — 같은 개념이
// 드롭다운과 난이도 필터 두 곳에 있으면 서로 다른 숫자를 내서 헷갈렸다.
//
// "OCR 텍스트와 다른 것"(diff_only)도 뺐다 — 타이핑한 것의 부분집합이라
// 드롭다운에서 둘을 오가는 의미가 크지 않다는 판단이다. 서버 파라미터는
// 남아 있어 필요하면 API로 조회할 수 있다.
const REVIEW_STATUS_PARAM = {
  typed: "typed_only",
  pass: "pass_only",
};

const PAGE_SIZE = 20;
let currentPage = 1;
let reviewerNames = new Map();
let currentAdminId = null;
// admin을 뺀 검수자 순서(=A, B 자리) — OCR 텍스트 밑 타이핑 줄을 이름표 없이
// 고정된 줄 위치로 보여주려면(ocrCell), 누가 몇 번째 자리인지 미리 정해둬야 한다.
let annotatorOrder = [];

function formatDate(value) {
  if (!value) return "-";
  return String(value).slice(0, 10);
}

function reviewerName(id) {
  return reviewerNames.get(id) || `#${id}`;
}

/** 부정 표현 표시 — 자동 감지에서 온 것인지 검수자가 직접 켠 것인지 구분한다.
 *
 *  검수자가 최종 확정한 값(contains_negative_expression)이 켜져 있을 때만
 *  보여준다. 자동 감지됐더라도 검수자가 껐으면 아무것도 표시하지 않는다 —
 *  부정 표현 단어가 들어 있다고 해서 모두 부정 표현인 것은 아니고, 그 판단은
 *  사람이 한 것이 맞기 때문이다(2026-08-24).
 *
 *  auto_negative_flag는 서버가 검수 당시 OCR 스냅샷으로 다시 계산해 내려준다
 *  (main.py의 _with_negative_origin).
 */
function negativeBadge(review) {
  if (!review || !review.contains_negative_expression) return "";
  const origin = review.auto_negative_flag ? "자동감지" : "검수자 판단";
  return ` · <span class="negative-tag">⚠ 부정 표현 (${escapeHtml(origin)})</span>`;
}

/** 글자 차이 개수를 괄호로 덧붙인다 (`3자 차이` / `3자, 띄어쓰기 차이`) —
 *  검수자 화면(review.js diffLinePanel)과 같은 표기다(2026-08-27).
 *
 *  한동안(2026-08-24) 이 칸에서는 뺐었다 — 바로 왼쪽 "OCR / 검수자 입력" 칸이
 *  이미 대괄호 표기로 어디가 달랐는지 보여주므로 숫자로 한 번 더 적는 게
 *  중복이라고 봤다. 다시 넣은 이유는 admin이 여러 건을 훑을 때 대괄호 표기를
 *  일일이 읽지 않고도 "차이가 컸는지"를 이 줄만 보고 가늠할 수 있어야 하기
 *  때문이다 — 왼쪽 칸은 "어디가" 다른지, 이 줄은 "얼마나" 다른지를 맡는다.
 *
 *  패스나 판독 불가는 ocr_diff_char_count가 없으므로(비교 자체를 안 함)
 *  자연히 표시되지 않는다 — 타이핑이고 차이가 있을 때만 붙는다.
 */
function reviewDiffNote(review) {
  const count = review.ocr_diff_char_count;
  if (!(count > 0)) return "";
  const spacingNote = review.spacing_diff ? ", 띄어쓰기" : "";
  return ` (${count}자${spacingNote} 차이)`;
}

/** "검수자 의견" 칸 한 줄.
 *
 *  패스도 `패스 (일치)`에서 `(일치)`를 뺐고, 이어서 `패스`라는 말 자체도 뺐다
 *  (2026-08-27). 왼쪽 칸의 상태 칩이 미처리/패스/판독 불가를 이미 구분해
 *  보여주므로(reviewerLineBody), 같은 사실을 한 행에 두 번 적는 것이었다.
 *  그래서 이 칸은 **판정의 부가 정보**(난이도·글자 차이·부정 표현·코멘트)만
 *  맡는다.
 *
 *  검수자 코멘트 입력은 2026-08-21 요청으로 없어졌지만, 과거 데이터에 남아
 *  있으면 그대로 보여준다 — 패스에 달린 코멘트도 마찬가지다.
 */
function reviewMetaLine(review) {
  // 판독 불가면 describeDifficulty가 "판독 불가"를 돌려준다 — "난이도" 접두어를
  // 따로 붙이지 않는 이유는 difficulty.js의 주석 참고.
  //
  // 난이도는 패스에도 필수지만(§5.1) 2026-08-21 이전 데이터에는 없을 수 있다.
  // 여기서의 "-"는 "값이 없다"는 뜻 그대로다 — 판정 자체는 왼쪽 칩이 말해주므로
  // 이 자리가 비어도 검수 여부가 헷갈리지 않는다.
  const level = escapeHtml(describeDifficulty(review.ocr_difficulty_level)) || "-";
  const base = `${level}${negativeBadge(review)}${reviewDiffNote(review)}`;
  return review.comment ? `${base} (${escapeHtml(review.comment)})` : base;
}

/** "검수자 A  :  난이도 3" 한 줄 — 콜론 앞뒤를 넓게 띄운다(2026-08-27, 요청).
 *  검수자 의견 칸(reviewsCell)과 관리자 판정 모달이 같은 줄 모양을 쓴다.
 *  같은 칸에 나란히 찍히는 관리자 코멘트 줄(otherAdminLines/myAdminLine)도
 *  같은 간격을 쓴다 — 검수자 줄만 넓고 관리자 줄은 좁으면 같은 칸 안에서
 *  표기가 갈렸다(2026-08-27 지적, 콜론 위치가 줄마다 달라 보임). */
function reviewerLine(review) {
  return `<div>${escapeHtml(reviewerName(review.reviewer_id))}  :  ${reviewMetaLine(review)}</div>`;
}

/** 질문 블록 — 라벨 없이 본문만. 이 칸의 첫머리에 문맥으로 얹힌다(아래
 *  ocrCell 주석 참고). 다른 구획과 마찬가지로 `.cell-section`을 써서 그 뒤
 *  구획과의 구분선은 CSS(`.cell-section + .cell-section`)가 자동으로 그린다.
 *
 *  문항 번호는 별도 칼럼 대신 질문 앞에 `[번호]`로 붙인다(2026-08-31,
 *  요청) — 예: `[36] 시간을 되돌릴 수 있다면 _________________`. */
function questionBlock(sct) {
  const numbered = sct.question_number != null ? `[${sct.question_number}] ${sct.sct_question}` : sct.sct_question;
  return `<div class="cell-section"><div class="cell-question" title="${escapeHtml(numbered)}">${escapeHtml(numbered)}</div></div>`;
}

/** "라벨   내용"을 한 줄에 나란히 놓는 구획 (OCR/검수자별, 2026-08-27) — 라벨
 *  칸 너비를 고정해(CSS `.cell-row .cell-label`) OCR과 검수자 A·B의 내용이
 *  좌우로 같은 위치에서 시작한다. */
function cellRow(label, bodyHtml) {
  return `<div class="cell-section cell-row"><div class="cell-label">${escapeHtml(label)}</div>${bodyHtml}</div>`;
}

function ocrCell(entry, sct) {
  const sections = [];

  // SCT 질문을 OCR 원문 바로 위에 둔다 (2026-08-26) — 검수자 카드(.card-question)와
  // 같은 위치·같은 이유다. "내가 늘 원하기는" 같은 문두를 알면 뒤에 이어질 필기가
  // 무엇인지 짐작되므로, 관리자가 두 검수자의 판독이 갈린 이유를 볼 때 필요하다.
  // 문항 번호는 별도 칸 없이 questionBlock에서 질문 앞에 `[번호]`로 붙인다.
  //
  // 라벨을 안 붙인다(2026-08-27) — 질문은 이 칸의 맨 위에서 다른 줄들의
  // "맥락"으로 얹히는 것이라 "질문:"이라고 이름표를 달지 않아도 헷갈리지
  // 않는다. OCR/검수자 줄은 서로 비슷한 모양이라 구분에 라벨이 꼭 필요하지만,
  // 질문은 형태 자체(3줄 clamp, 옅은 글자색)가 이미 다르다.
  //
  // 질문이 없는 레코드는 없다 — DB의 (연령대, 문항번호) 조합 133개가 모두
  // SCT Questions.xlsx에 있어(question_master), sct가 붙기만 하면 항상
  // sct_question이 채워진다(2026-08-27 확인, 전체 107건 중 0건 누락). 다만
  // mielin 연결이 끊겨 sct 자체를 못 붙인 순간에는(main.py의 sct_lookup
  // 예외 처리) sct가 빈 객체가 되므로, 그 드문 경우까지 대비해 조건은
  // 남겨둔다 — 없으면 이 블록만 조용히 빠진다.
  if (sct.sct_question) {
    sections.push(questionBlock(sct));
  }

  sections.push(
    cellRow("OCR", `<div class="ocr-original">${escapeHtml(sct.ocr_text || sct.sct_na_reason || "-")}</div>`)
  );

  // 이름표 대신 고정된 줄 자리(검수자 순서, annotatorOrder)로 보여준다 —
  // 그 검수자가 타이핑했으면 내용을, 아니면 무엇을 했는지(미처리/패스/판독 불가)
  // 상태 칩을 그 자리에 남긴다(reviewerLineBody). 관리자가 드물게 검수에 참여해
  // 타이핑해도 이 칸에는 나오지 않는다 — "검수자 의견" 칸에는 이름과 함께
  // 그대로 표시된다.
  //
  // 타이핑 줄은 §5.3의 대괄호 표기로 보여준다 — OCR 원문이 바로 위에 있으니
  // 어디가 달랐는지 눈으로 바로 비교할 수 있다.
  for (const id of annotatorOrder) {
    const review = entry.reviews.find((r) => r.reviewer_id === id);
    sections.push(cellRow(reviewerName(id), `<div class="reviewer-typed">${reviewerLineBody(review)}</div>`));
  }

  return sections.join("");
}

/** 상태 칩 — 옮겨 적은 내용이 아니라 "검수자가 무엇을 했는가"를 나타낸다. */
function stateChip(kind, label, title) {
  return `<span class="state-chip ${kind}" title="${escapeHtml(title)}">${escapeHtml(label)}</span>`;
}

/** 검수자 한 줄의 본문 — 타이핑이면 옮겨 적은 내용, 아니면 상태 칩.
 *
 *  네 상태(미처리 / 패스 / 판독 불가 / 타이핑)를 모두 구분해서 적는다
 *  (2026-08-27). 예전에는 **패스와 미처리가 똑같이 빈 줄**이라 패스한 건이
 *  "아직 검수를 안 한 것"으로 오인됐고, 판독 불가는 `-`였는데 이 표의 다른
 *  칸들이 "값 없음"에 쓰는 기호와 같아 구분되지 않았다. 판독 불가는 값의
 *  부재가 아니라 **검수자가 이미지를 보고 내린 판정**이므로 이름을 적는다.
 *
 *  글씨가 아니라 칩(배경 있는 라벨)으로 두는 이유: 이 칸은 바로 위 OCR 원문과
 *  글자를 맞대어 보는 자리라, 상태를 맨 글씨로 적으면 검수자가 옮겨 적은
 *  내용으로 읽힌다("동일"이라고 적으면 그 두 글자를 필기 내용으로 오해한다).
 *
 *  판독 불가 판정은 서버의 _is_unreadable(main.py)과 같은 기준으로 본다 —
 *  transcription인데 옮겨 적은 내용이 없으면 판독 불가다. 난이도 5를 직접 보지
 *  않는 이유는 그쪽 주석 참고(난이도 도입 이전 레거시 데이터).
 */
function reviewerLineBody(review) {
  if (!review) {
    return stateChip("pending", "미처리", "이 검수자는 아직 이 건을 처리하지 않았습니다");
  }
  if (review.review_type === "normal_check") {
    return stateChip("pass", "패스", "OCR 원문이 이미지와 같다고 판정했습니다");
  }
  if (!review.typed_text) {
    return stateChip("unreadable", "판독 불가", "필기를 알아볼 수 없다고 판정했습니다");
  }
  return review.ocr_diff_char_count
    ? markedToHtml(review.ocr_diff_marked)
    : escapeHtml(review.typed_text);
}

function imageCell(entry) {
  if (!entry.sct || !entry.sct.id) {
    return `<div class="thumb-placeholder">SCT 원본 조회 실패<br />(mielin 연결 확인 필요)</div>`;
  }
  const src = `/api/sct/records/${entry.sct.id}/image`;
  return (
    `<img class="review-image" src="${src}" loading="lazy" alt="SCT 답변 이미지" ` +
    `onerror="this.replaceWith(Object.assign(document.createElement('div'), ` +
    `{className:'thumb-placeholder', textContent:'이미지 준비 중'}))" ` +
    `onclick="window.__openImageModal && window.__openImageModal('${src}')" />`
  );
}

/** 관리자가 남긴 판정 한 줄.
 *
 *  `확정` 꼬리표는 뺐다 (2026-08-24) — 줄 앞에 이미 `관리자:` 이름이 붙어서
 *  누가 남긴 값인지 드러나므로, 비블라인드 판정임을 따로 적을 필요가 없었다.
 *  (판정의 성격은 §4.5 문서에 남아 있다.)
 */
function adminCommentMetaLine(c) {
  const parts = [];
  if (c.difficulty_level) {
    parts.push(escapeHtml(describeDifficulty(c.difficulty_level)));
  }
  if (c.comment) parts.push(escapeHtml(c.comment));
  return parts.join(" · ") || "-";
}

function reviewsCell(entry) {
  const badge =
    entry.status === "completed"
      ? `<span class="badge green">완료 (${entry.review_count}/2)</span>`
      : // 첫 의견만 있어도 내용을 공개한다 (§4.5) — admin이 진행 상황을 빠르게
        // 파악할 수 있도록, 두 번째 의견을 기다리지 않는다.
        `<span class="badge yellow">진행중 (${entry.review_count}/2)</span>`;

  // 검수자 A/B, admin 코멘트를 각자 배경 없이 한 줄씩 나열하고, 전체를
  // 하나의 파란 배경(.my-review)으로 감싼다 — 사람별로 구분하지 않는다.
  const reviewLines = entry.reviews
    .map(reviewerLine)
    .join("");

  const mine = entry.admin_comments.find((c) => c.admin_id === currentAdminId) || null;
  const otherAdminLines = entry.admin_comments
    .filter((c) => c.admin_id !== currentAdminId)
    .map((c) => `<div>${escapeHtml(reviewerName(c.admin_id))}  :  ${adminCommentMetaLine(c)}</div>`)
    .join("");

  // admin 본인 판정: 없으면 [판정] 버튼만, 있으면 내용 + [수정] 버튼.
  // 두 경우 모두 같은 모달을 연다(openAdminCommentModal) — 예전에는 표 아래
  // 행에 폼을 펼쳤는데, 난이도 버튼까지 들어가면서 행 높이가 들쭉날쭉해지고
  // 어느 레코드의 폼인지 헷갈렸다(2026-08-24).
  const myAdminLine = mine
    ? `<div>${escapeHtml(reviewerName(mine.admin_id))}  :  ${adminCommentMetaLine(mine)}
         <button type="button" class="edit-btn" data-action="open-admin-comment"
           data-key="${entryKey(entry)}">수정</button></div>`
    : `<button type="button" class="admin-comment-toggle" data-action="open-admin-comment"
         data-key="${entryKey(entry)}">관리자 의견</button>`;

  return `${badge}<div class="my-review reviews-summary">${reviewLines}${otherAdminLines}${myAdminLine}</div>`;
}

/** 관리자 판정 입력 모달.
 *
 *  난이도(1~4 + 판독 불가)와 코멘트를 함께 받는다. 검수 카드의 입력부와 같은
 *  버튼 구성이라 조작법이 일관된다 — 판독 불가는 등급 스케일 밖의 별도 판정이라
 *  1~4와 나란히 두되 따로 놓는다(§5.1).
 *
 *  둘 중 **하나만** 넣어도 저장된다. 난이도만 찍고 넘어가는 경우가 많은데
 *  코멘트를 필수로 두면 억지로 텍스트를 쓰게 되기 때문이다 — 다만 둘 다 비면
 *  남길 내용이 없으므로 서버가 400으로 막는다.
 *
 *  난이도를 고른 뒤 "선택 해제"로 도로 지우는 버튼은 없다(2026-08-27) —
 *  이미 남긴 판정을 없던 일로 되돌리는 대신, 잘못 골랐으면 다른 값으로
 *  다시 고르면 된다는 게 이 화면의 원칙이다(검수자 쪽 수정 이력도 같은
 *  원칙 — editCountNote 주석 참고: 지우지 않고 고쳐서 최종값에 수렴시킨다).
 *  새 코멘트는 어차피 버튼을 안 누르면 난이도가 null로 남으니, 코멘트만
 *  남기는 경로는 이 버튼 없이도 그대로 열려 있다.
 */
function openAdminCommentModal(entry) {
  const mine = entry.admin_comments.find((c) => c.admin_id === currentAdminId) || null;
  const sct = entry.sct || {};
  let level = mine ? mine.difficulty_level : null;

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-card" role="dialog" aria-modal="true" aria-label="관리자 판정 입력">
      <div class="modal-title">관리자 판정</div>
      <div class="modal-meta">
        #${entry.assessment_id} · ${escapeHtml(sct.sct_age_group || "-")} · ${sct.question_number ?? "-"}번
      </div>
      <div class="modal-ocr">OCR: ${escapeHtml(sct.ocr_text || "-")}</div>
      <div class="modal-reviews">${entry.reviews
        .map(reviewerLine)
        .join("")}</div>

      <div class="modal-field">
        <div class="modal-label">난이도 <span class="modal-hint">(의견이 갈릴 때의 중재값)</span></div>
        <div class="level-buttons modal-levels">
          ${PICKABLE_DIFFICULTY_LEVELS.map(
            (d) => `
            <button type="button" class="level-btn" data-level="${d.level}"
              aria-pressed="${d.level === level ? "true" : "false"}" title="${d.level} ${d.short}">
              ${d.level}<span class="level-btn-short">${d.short}</span>
            </button>`
          ).join("")}
          <button type="button" class="unreadable-btn" data-level="${UNREADABLE_LEVEL}"
            aria-pressed="${level === UNREADABLE_LEVEL ? "true" : "false"}">판독 불가</button>
        </div>
      </div>

      <div class="modal-field">
        <div class="modal-label">코멘트 <span class="modal-hint">(선택)</span></div>
        <textarea class="modal-comment" rows="3"
          placeholder="참고 메모 (선택)">${mine ? escapeHtml(mine.comment) : ""}</textarea>
      </div>

      <div class="modal-actions">
        <button type="button" class="modal-cancel">취소</button>
        <button type="button" class="primary modal-save">${mine ? "수정 저장" : "저장"}</button>
      </div>
      <div class="modal-error" hidden></div>
    </div>
  `;

  // 바깥 클릭/ESC/취소로 닫는다 — 이미지 확대 모달과 같은 조작이다.
  //
  // document에 붙인 keydown은 **close 안에서** 떼야 한다(2026-08-27). 예전에는
  // ESC로 닫는 경로에서만 떼어냈는데, [취소]나 바깥 클릭으로 닫으면 리스너가
  // 남아 모달을 여닫을 때마다 하나씩 쌓였다.
  const onKey = (e) => {
    if (e.key === "Escape") close();
  };
  const close = () => {
    document.removeEventListener("keydown", onKey);
    overlay.remove();
  };
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  overlay.querySelector(".modal-cancel").addEventListener("click", close);

  const err = overlay.querySelector(".modal-error");
  const syncLevels = () => {
    overlay.querySelectorAll(".modal-levels [data-level]").forEach((b) => {
      b.setAttribute("aria-pressed", String(Number(b.dataset.level) === level));
    });
  };
  overlay.querySelectorAll(".modal-levels [data-level]").forEach((btn) => {
    btn.addEventListener("click", () => {
      level = Number(btn.dataset.level);
      syncLevels();
    });
  });
  overlay.querySelector(".modal-save").addEventListener("click", async () => {
    const comment = overlay.querySelector(".modal-comment").value.trim();
    if (level === null && !comment) {
      err.hidden = false;
      err.textContent = "난이도 또는 코멘트 중 하나는 입력해야 합니다.";
      return;
    }
    const body = { comment, difficulty_level: level };
    try {
      const res = await fetch(
        mine ? `/api/ocr/admin/comments/${mine.id}` : "/api/ocr/admin/comments",
        {
          method: mine ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            mine
              ? body
              : {
                  assessment_id: entry.assessment_id,
                  drawing_id: entry.drawing_id,
                  answer_index: entry.answer_index,
                  ...body,
                }
          ),
        }
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `요청 실패 (${res.status})`);
      }
      close();
      showMessage("");
      loadRecords();
      loadProgress();
    } catch (e2) {
      err.hidden = false;
      err.textContent = `저장 실패: ${e2.message}`;
    }
  });

  document.body.appendChild(overlay);
  overlay.querySelector(".modal-comment").focus();
}

function bindCommentForms(byKey) {
  tableWrap.querySelectorAll('[data-action="open-admin-comment"]').forEach((btn) => {
    btn.addEventListener("click", () => {
      const entry = byKey.get(btn.dataset.key);
      if (entry) openAdminCommentModal(entry);
    });
  });
}

/** 레코드를 가리키는 자연 키 문자열 — DOM data-key와 조회용 Map의 키로 쓴다.
 *  검수 DB와 mielin이 별도 서버라 id 하나로 묶을 수 없어(§4.5) 세 값을 이어 쓴다. */
function entryKey(entry) {
  return `${entry.assessment_id}:${entry.drawing_id}:${entry.answer_index}`;
}

/** 지금 걸려 있는 필터를 사람이 읽을 수 있는 목록으로 만든다 — 검수자 화면
 *  (review.js activeFilterLabels)과 같은 이유다(2026-08-31). 필터가 8개 가까이
 *  늘면서 "0건인 게 필터 탓인지 고장인지" 구분이 안 된다는 지적이 있었다.
 *  결과가 0건일 때만 쓰이므로, 값 하나하나가 "무엇 때문에 걸러졌는지"를
 *  바로 알려줘야 한다. */
function activeFilterLabels() {
  const labels = [];
  if (reviewStatusFilter.value !== "all") {
    labels.push(
      `검수 상태: ${reviewStatusFilter.options[reviewStatusFilter.selectedIndex]?.text}`
    );
  }
  const levels = difficultyFilter.levels();
  if (levels.length) labels.push(`난이도: ${levels.join(", ")}`);
  if (negativeOnly.checked) labels.push("부정 표현만");
  const unread = difficultyFilter.unreadable();
  if (unread === "exclude") labels.push("판독 불가 제외");
  else if (unread === "only") labels.push("판독 불가만");
  if (ageGroupFilter.value) {
    labels.push(`연령대: ${ageGroupFilter.options[ageGroupFilter.selectedIndex]?.text}`);
  }
  if (vlmModelFilter.value) {
    labels.push(`VLM 모델: ${vlmModelFilter.options[vlmModelFilter.selectedIndex]?.text}`);
  }
  if (reviewerFilter.value) {
    labels.push(`검수자: ${reviewerFilter.options[reviewerFilter.selectedIndex]?.text}`);
  }
  if (completionFilter.value) {
    labels.push(`완료 여부: ${completionFilter.options[completionFilter.selectedIndex]?.text}`);
  }
  const start = document.getElementById("dateStart").value;
  const end = document.getElementById("dateEnd").value;
  if (start || end) {
    labels.push(`기간: ${start || "처음"} ~ ${end || "끝"}`);
  }
  if (keywordInput.value.trim()) labels.push(`검색어: ${keywordInput.value.trim()}`);
  return labels;
}

/** 모든 필터를 기본값으로 되돌리고 다시 조회한다 (review.js resetFilters와 같다). */
function resetFilters() {
  reviewStatusFilter.value = "typed";
  negativeOnly.checked = false;
  ageGroupFilter.value = "";
  vlmModelFilter.value = "";
  reviewerFilter.value = "";
  completionFilter.value = "";
  difficultyFilter.reset?.();
  // 기간은 date-range.js가 관리하므로 "전체 기간" 프리셋 버튼을 눌러 되돌린다
  // (입력값만 지우면 pill 버튼의 선택 표시가 남아 상태가 어긋난다).
  document.querySelector('.range-action[data-range-value="all"]')?.click();
  keywordInput.value = "";
  currentPage = 1;
  loadRecords();
}

function renderTable(items) {
  if (!items.length) {
    const labels = activeFilterLabels();
    const detail = labels.length
      ? `<div class="empty-filters">적용 중인 필터<ul>${labels
          .map((l) => `<li>${escapeHtml(l)}</li>`)
          .join("")}</ul></div>
         <button type="button" id="resetFilters" class="primary">필터 초기화</button>`
      : "";
    tableWrap.innerHTML = `<div class="empty">조회된 데이터가 없습니다.${detail}</div>`;
    document.getElementById("resetFilters")?.addEventListener("click", resetFilters);
    return;
  }

  const byKey = new Map(items.map((entry) => [entryKey(entry), entry]));

  const rows = items
    .map((entry) => {
      // mielin 연결이 끊겨 SCT 원본을 못 붙였을 수도 있다 — 그때도 검수
      // 내용은 보여줘야 하므로 빈 객체로 두고 각 칸이 "-"를 쓰게 한다.
      const sct = entry.sct || {};
      return `<tr data-key="${entryKey(entry)}">
        <td>${formatDate(sct.source_created_at || sct.imported_at)}</td>
        <td>${entry.assessment_id}</td>
        <td>${escapeHtml(sct.sct_age_group || "-")}</td>
        <td class="cell-image">${imageCell(entry)}</td>
        <td class="cell-answer">${ocrCell(entry, sct)}</td>
        <td class="cell-answer">${reviewsCell(entry)}</td>
      </tr>`;
    })
    .join("");

  tableWrap.innerHTML = `<table>
    <thead>
      <tr>
        <th>검사일</th>
        <th>검사 ID</th>
        <th>연령대</th>
        <th>이미지</th>
        <th>OCR / 검수자 입력</th>
        <th>검수자 의견</th>
      </tr>
    </thead>
    <tbody>${rows}</tbody>
  </table>`;

  bindCommentForms(byKey);
}

// 페이저는 검수자 화면과 공용이다 (ui-utils.js createPager) — 페이지 상태와
// 재조회 방법만 여기서 넘긴다.
const renderPager = createPager({
  onPage: (page) => {
    currentPage = page;
    loadRecords();
  },
});

function buildQuery() {
  const params = new URLSearchParams();
  params.set("page", String(currentPage));
  params.set("page_size", String(PAGE_SIZE));
  // 난이도는 여러 개를 반복 파라미터로 보낸다 (?difficulty_level=1&difficulty_level=2)
  for (const level of difficultyFilter.levels()) params.append("difficulty_level", String(level));
  // 판독 불가 3단 선택 — "포함"은 아무 파라미터도 보내지 않는다는 뜻이다.
  const unreadable = difficultyFilter.unreadable();
  if (unreadable === "exclude") params.set("exclude_unreadable", "true");
  else if (unreadable === "only") params.set("unreadable_only", "true");
  const statusParam = REVIEW_STATUS_PARAM[reviewStatusFilter.value];
  if (statusParam) params.set(statusParam, "true");
  if (negativeOnly.checked) params.set("negative_only", "true");
  if (ageGroupFilter.value) params.set("age_group", ageGroupFilter.value);
  if (vlmModelFilter.value) params.set("vlm_model", vlmModelFilter.value);
  if (reviewerFilter.value) params.set("reviewer_id", reviewerFilter.value);
  if (completionFilter.value) params.set("status", completionFilter.value);
  const start = document.getElementById("dateStart").value;
  const end = document.getElementById("dateEnd").value;
  if (start) params.set("date_start", start);
  if (end) params.set("date_end", end);
  if (keywordInput.value.trim()) params.set("keyword", keywordInput.value.trim());
  return params;
}

async function loadRecords() {
  showMessage("");
  tableWrap.innerHTML = `<div class="empty">불러오는 중...</div>`;
  try {
    const res = await fetch(`/api/ocr/admin/records?${buildQuery().toString()}`);
    if (!res.ok) throw new Error(`요청 실패 (${res.status})`);
    const data = await res.json();
    // 조회 상한에 걸려 일부가 빠졌으면 알린다 — 검수자 화면과 같은 방식이다.
    if (data.warning) showMessage(data.warning);
    renderTable(data.items);
    renderPager(data.total, data.page, data.total_pages);
  } catch (err) {
    tableWrap.innerHTML = "";
    // 이전 조회의 페이저가 남아 있으면 실패한 화면에 엉뚱한 페이지 수가 보인다.
    renderPager(0, 1, 1);
    showMessage(`데이터를 불러오지 못했습니다: ${err.message}`);
  }
}

async function loadProgress() {
  try {
    const res = await fetch("/api/ocr/admin/progress");
    if (!res.ok) throw new Error(`요청 실패 (${res.status})`);
    const p = await res.json();
    const fmt = (n) => (n === null || n === undefined ? "-" : n.toLocaleString("en-US"));

    // 진행중을 검수자별로 나눠 카드 하나씩 보여준다(2026-08-31, 요청) — 누가
    // 밀리고 있는지 한눈에 보려는 목적이라, "1명만 처리"를 합산한 숫자 하나보다
    // 검수자마다 나눈 게 더 쓸모 있다. annotatorOrder(현역 검수자, admin 제외)를
    // 그대로 써서 이름표/순서를 다른 곳(ocrCell 등)과 맞춘다.
    const wipByReviewer = new Map(
      (p.in_progress_by_reviewer || []).map((r) => [r.reviewer_id, r.records])
    );
    const wipCards = annotatorOrder
      .map(
        (id) => `
      <div class="progress-stat wip">
        <div class="stat-label">진행중 · ${escapeHtml(reviewerName(id))}</div>
        <div class="stat-value">${fmt(wipByReviewer.get(id) || 0)}</div>
      </div>`
      )
      .join("");

    progressBar.innerHTML = `
      <div class="progress-stat">
        <div class="stat-label">전체 SCT 검사 수</div>
        <div class="stat-value">${fmt(p.total_assessments)}</div>
      </div>
      <div class="progress-stat">
        <div class="stat-label">전체 검사자 수</div>
        <div class="stat-value">${fmt(p.total_clients)}</div>
      </div>
      <div class="progress-stat">
        <div class="stat-label">전체 이미지 수</div>
        <div class="stat-value">${fmt(p.total_images)}</div>
      </div>
      <div class="progress-stat done">
        <div class="stat-label">검수 완료 (2명 이상)</div>
        <div class="stat-value">${fmt(p.completed)}</div>
      </div>
      ${wipCards}
    `;
    progressBar.hidden = false;
  } catch (err) {
    // 공용 메시지 박스(#message)를 쓰지 않는다(2026-08-27) — 목록 조회와 동시에
    // 실행되는데 loadRecords가 시작할 때 그 박스를 비우므로, 이 오류가 조용히
    // 지워지곤 했다. 비어 있는 자리에 직접 적으면 경쟁도 없고 어느 영역이
    // 실패했는지도 분명해진다.
    progressBar.innerHTML =
      `<div class="progress-error">진행 현황을 불러오지 못했습니다: ${escapeHtml(err.message)}</div>`;
    progressBar.hidden = false;
  }
}

async function loadFilters() {
  const res = await fetch("/api/sct/filters");
  if (!res.ok) throw new Error(`요청 실패 (${res.status})`);
  const data = await res.json();

  for (const ageGroup of data.age_groups || []) {
    const option = document.createElement("option");
    option.value = ageGroup;
    option.textContent = ageGroup;
    ageGroupFilter.appendChild(option);
  }
  for (const vlmModel of data.vlm_models || []) {
    const option = document.createElement("option");
    option.value = vlmModel;
    option.textContent = vlmModel;
    vlmModelFilter.appendChild(option);
  }
}

async function loadCurrentAdmin() {
  const res = await fetch("/api/auth/me");
  if (!res.ok) throw new Error(`요청 실패 (${res.status})`);
  const me = await res.json();
  currentAdminId = me.id;
}

async function loadReviewers() {
  const res = await fetch("/api/ocr/reviewers");
  if (!res.ok) throw new Error(`요청 실패 (${res.status})`);
  const data = await res.json();
  const reviewers = data.items || [];
  // 이름 표를 만들 때는 비활성·삭제된 계정까지 담는다 — 그 계정이 과거에 남긴
  // 검수 의견이 목록에 그대로 나오므로, 이름을 모르면 내부 id가 노출된다.
  reviewerNames = new Map(reviewers.map((r) => [r.id, r.name]));
  // 반면 고를 수 있는 대상(필터 드롭다운)과 타이핑 줄 자리는 현역 검수자만이다.
  //
  // reviewer_id 필터는 검수자 의견 테이블(ocr_review_comments)만 본다
  // (get_admin_records의 reviewer_id 조건) — 관리자 판정은 완전히 다른
  // 테이블(ocr_admin_comments, admin_id 컬럼)에 저장되므로, 드롭다운에
  // 관리자가 있어도 골랐을 때 항상 0건이었다(2026-08-27 제거). "관리자가
  // 남긴 판정만 모아 보기"는 지금의 reviewer_id 필터와 다른 조건·다른
  // 동작(다른 필터를 무시할지 등)이 필요한 별도 기능이라, 있는 것처럼
  // 보이는 죽은 선택지를 없애는 쪽을 택했다.
  const activeAnnotators = reviewers.filter(
    (r) => r.is_active && !r.is_deleted && r.role !== "admin"
  );
  annotatorOrder = activeAnnotators.map((r) => r.id);

  for (const r of activeAnnotators) {
    const option = document.createElement("option");
    option.value = String(r.id);
    option.textContent = r.name;
    reviewerFilter.appendChild(option);
  }
}

async function init() {
  setupImageModal();

  // 상단 네비(nav-auth.js)의 새로고침 버튼이 부른다 — 필터/페이지는 그대로 두고
  // 진행률과 목록만 다시 불러온다(2026-08-31, "조회" 버튼을 대체).
  window.__refreshCurrentView = () => {
    loadProgress();
    loadRecords();
  };

  difficultyFilter = bindDifficultyFilter({
    mountId: "difficultyFilter",
    onChange: () => {
      currentPage = 1;
      loadRecords();
    },
  });

  keywordInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      currentPage = 1;
      loadRecords();
    }
  });
  negativeOnly.addEventListener("change", () => {
    currentPage = 1;
    loadRecords();
  });
  [reviewStatusFilter, ageGroupFilter, vlmModelFilter, reviewerFilter, completionFilter].forEach((select) => {
    select.addEventListener("change", () => {
      currentPage = 1;
      loadRecords();
    });
  });

  bindDateRanges({
    configs: [
      {
        selectId: "dateRange",
        startId: "dateStart",
        endId: "dateEnd",
        setPresetInputs: true,
        dispatchChange: false,
        onPreset: () => {
          currentPage = 1;
          loadRecords();
        },
        onApply: () => {
          currentPage = 1;
          loadRecords();
        },
      },
    ],
    closeOnOutside: true,
    closeOnEscape: true,
  });

  try {
    await Promise.all([loadCurrentAdmin(), loadReviewers(), loadFilters()]);
  } catch (err) {
    showMessage(`초기 설정을 불러오지 못했습니다: ${err.message}`);
  }
  await Promise.all([loadProgress(), loadRecords()]);
}

init();
