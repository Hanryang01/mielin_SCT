import { bindDateRanges } from "./date-range.js?v=2";
import {
  escapeHtml,
  showMessage,
  markedToHtml,
  setupImageModal,
  createPager,
} from "./ui-utils.js?v=2";
import { renderDiffHtml } from "./text-diff.js?v=1";
import { bindDifficultyFilter } from "./difficulty-filter.js?v=5";
import {
  PICKABLE_DIFFICULTY_LEVELS,
  UNREADABLE_LEVEL,
  describeDifficulty,
} from "./difficulty.js?v=2";

// OCR 검수 시나리오.md §3~§4.4 화면 구현.
//
// 그리드 카드 격자 하나로만 본다 (§4.2) — 짧은 답변을 한 화면에 많이 놓고,
// 체크박스로 여러 건을 골라 한 번에 패스한다. 전수 검사(44,000여 건)에서
// 건당 오버헤드를 없애는 것이 목적이다. 카드 하나만 처리하고 싶으면 그 카드만
// 체크하고 [제출]을 누른다 — 별도의 [패스] 버튼은 두지 않는다(체크 상태를
// 무시하는 두 번째 진입점이 되어 혼란스럽다). 이미지는 카드 폭에 맞춰
// 축소되지만 클릭하면 원본 크기 모달이 열린다 — 판독이 어려운 건도 이걸로
// 충분해서 별도 리스트 보기 모드는 2026-08-21에 없앴다(§8 변경이력).
//
// 검수자 신원은 로그인 세션에서 결정된다 — 화면에서 고를 수 없고, 제출할 때도
// reviewer_id를 보내지 않는다 (서버가 세션에서 채운다).
//
// 블라인드 원칙(§3: "본인 제출 전까지 다른 검수자 내용 비공개")은 서버가
// 강제한다 (main.py의 _blind_state). 내가 제출하기 전에는 응답에 다른 검수자의
// 내용은 물론 처리 여부(review_count/status)조차 담기지 않는다.
//
// 일괄 패스에 "전체 선택" 버튼은 의도적으로 두지 않았다. 이미지를 보지 않고
// 한꺼번에 넘기면 검수 데이터 자체가 무의미해지기 때문이다. 대신 로드 시
// 각 카드의 패스 체크박스를 기본으로 켜둔다(2026-08-31) — 난이도는 여전히
// 카드마다 직접 골라야만 제출되므로(§5.1), "전부 다 봤다고 치고 그냥 넘김"이
// 여전히 불가능하다는 점은 같다(loadRecords 주석 참고).
const tableWrap = document.getElementById("recordTableWrap");

const keywordInput = document.getElementById("keyword");
const mineFilter = document.getElementById("mineFilter");
// 난이도 + 판독 불가는 팝오버 위젯이 함께 담당한다 (difficulty-filter.js) —
// init에서 연결한다. 연령대/VLM 모델 필터는 검수자에게 필요 없어 없앴다
// (검수자는 이미지를 보고 판정만 하므로 모델·연령대로 나눠 작업할 이유가 없다,
// 2026-08-24). admin 화면에는 §6 분석용으로 그대로 있다.
let difficultyFilter = { levels: () => [], unreadable: () => "exclude", reset: () => {} };
const negativeOnlyFilter = document.getElementById("negativeOnly");

const bulkBar = document.getElementById("bulkBar");
const bulkCountEl = document.getElementById("bulkCount");
const bulkClearBtn = document.getElementById("bulkClear");
const bulkPassBtn = document.getElementById("bulkPass");

// status/review_count는 2026-08-21부터 서버가 내려주지 않는다 — 검수자
// 화면은 상대가 몇 명 처리했는지 몰라도 되는 독립 운영 화면이기 때문이다.
const EMPTY_STATE = { reviews: [], mine_submitted: false };
// 일괄 패스 1회 상한 — 서버(main.py BULK_PASS_MAX)와 같은 값으로 맞춘다.
const BULK_PASS_MAX = 50;
// 그리드는 한 화면에 많이 담아야 의미가 있어 페이지 크기를 크게 쓴다
// (2026-08-21 — 리스트 모드 제거 이후 그리드가 유일한 보기 방식이다).
const PAGE_SIZE = 24;

let currentPage = 1;
let currentItems = [];
let currentUser = null;
const selected = new Set();


function recordKey(item) {
  return `${item.assessment_id}:${item.drawing_id}:${item.answer_index}`;
}

function negativeBadge(flagged) {
  return flagged ? ` · <span class="negative-tag">⚠ 부정 표현</span>` : "";
}

function imageSrc(item) {
  return `/api/sct/records/${item.id}/image`;
}

const IMG_FALLBACK =
  `onerror="this.replaceWith(Object.assign(document.createElement('div'), ` +
  `{className:'thumb-placeholder', textContent:'이미지 준비 중'}))"`;

/** 패스 한 줄 요약. 타이핑/판독 불가는 myReviewPanel이 직접 그리므로
 *  (텍스트·diff·난이도를 여러 줄로 보여줘야 한다) 여기로 오지 않는다 —
 *  호출부가 `review_type !== "transcription"`일 때만 부른다. */
function describePassReview(review) {
  // 패스도 난이도가 필수다(§5.1) — 다만 과거(2026-08-21 이전) 패스는
  // 난이도 없이 저장됐을 수 있어 값이 없으면 라벨을 생략한다.
  const level = review.ocr_difficulty_level
    ? ` · ${escapeHtml(describeDifficulty(review.ocr_difficulty_level))}`
    : "";
  return `패스${level}${negativeBadge(review.contains_negative_expression)}`;
}

/** 배지에는 숫자를 넣지 않는다(2026-08-21) — "완료 (1/2)"처럼 아직 상대가
 *  안 왔는데 "완료"라고 읽혀서 혼란스러웠고, 애초에 검수자 화면은 상대가
 *  몇 명 처리했는지 알 필요가 없는 독립 운영 화면이다. 이 배지는 오직
 *  "나는 이미 처리했다"만 뜻한다. */
function doneBadge() {
  return `<span class="badge green">완료</span>`;
}

/** 내가 남긴 처리 내용. 완료 전이라도 본인 것은 보여준다 — 블라인드는
 *  "남의 의견"을 가리는 규칙이고, 내 입력을 내가 확인하는 건 무관하다.
 *  (서버도 이 경우 reviews에 내 행만 담아서 내려준다.) */
function myReview(state) {
  if (!state.mine_submitted || !state.reviews.length) return null;
  return state.reviews.find((r) => r.reviewer_id === currentUser?.id) || state.reviews[0];
}

/** 수정 횟수 표시 (2026-08-24).
 *
 *  예전에는 이전 입력 내용을 펼쳐 볼 수 있었지만, 최종 검수 결과를 얻는 것이
 *  목표이고 빠르게 입력하다 생긴 오타를 고친 기록까지 남길 이유가 없다고
 *  판단해 내용은 저장·표시 모두 그만뒀다(§8). 대신 **횟수**만 보여준다 —
 *  여러 번 고쳤다는 사실 자체가 "검수 기준이 안 잡혔다"는 신호이기 때문이다.
 *
 *  "완료 후 수정" 여부는 표시에서 뺐다(2026-08-27) — 검수자 화면은 상대가
 *  몇 명 처리했는지조차 모르는 독립 운영 화면이라(doneBadge 주석 참고),
 *  "완료 후"라는 말 자체가 이 화면에서 설명 없이는 이해하기 어려웠다.
 *  값은 서버가 계속 내려준다(review.edited_after_completed) — admin 등
 *  다른 화면에서 필요해지면 쓸 수 있다.
 *
 *  독립된 줄이 아니라 괄호로 붙인다(2026-08-27) — 난이도 한 줄, 수정 횟수 한
 *  줄로 나뉘어 있으면 같은 판정에 대한 부가 정보인데도 별개 사실처럼 읽혔다.
 */
function editCountNote(review) {
  if (!review.edit_count) return "";
  return ` (수정 ${review.edit_count}회)`;
}

/** 내가 남긴 처리 내용. [수정] 버튼도 같이 준다 (2026-08-21부터 패스도 대상).
 *  완료 전이든 후든 본인 것은 보여준다 — 블라인드는 "남의 의견"을 가리는
 *  규칙이고, 내 입력을 내가 확인/수정하는 건 무관하다. */
function myReviewPanel(item, state) {
  const mine = myReview(state);
  if (!mine) return "";

  if (mine.review_type !== "transcription") {
    return `
      <div class="my-review">
        ${describePassReview(mine)}${editCountNote(mine)}
        <button type="button" class="edit-btn" data-action="edit" data-id="${item.id}"
          data-review-id="${mine.id}">수정</button>
      </div>
    `;
  }

  // 판독 불가는 "옮겨 적을 내용이 없다"가 정상이므로 typed_text가 비어 있다 —
  // 빈 값을 "-"로 흘려보내지 않고 판정 그대로 쓴다.
  const isUnreadable = !mine.typed_text && mine.ocr_difficulty_level === UNREADABLE_LEVEL;
  const bodyText = isUnreadable ? "판독 불가" : escapeHtml(mine.typed_text || "-");
  // 판독 불가면 본문·diff 줄에 이미 "판독 불가"가 나오므로 메타 줄에서는 뺀다
  // (세 줄에 걸쳐 같은 말이 세 번 나오고 있었다, 2026-08-24).
  const levelText = isUnreadable
    ? ""
    : escapeHtml(describeDifficulty(mine.ocr_difficulty_level)) || "난이도 -";
  const negative = negativeBadge(mine.contains_negative_expression);
  const editNote = editCountNote(mine);
  const metaLine =
    levelText || negative || editNote
      ? `<div class="my-typed-meta">${levelText}${negative}${editNote}</div>`
      : "";

  return `
    <div class="my-review">
      <div class="my-typed-text">${bodyText}</div>
      ${diffLinePanel(mine, isUnreadable)}
      ${metaLine}
      <button type="button" class="edit-btn" data-action="edit" data-id="${item.id}"
        data-review-id="${mine.id}">수정</button>
    </div>
  `;
}

/** 저장된 diff(§5.3)를 그대로 보여준다 — 서버가 계산해 내려준 표기라 화면에서
 *  다시 계산하지 않는다(DB에 든 값과 어긋나지 않도록).
 *
 *  ocr_diff_char_count는 세 값을 가질 수 있지만(main.py의 저장 규칙) 여기서
 *  실제로 나뉘는 경우는 둘뿐이다:
 *    - > 0  : 차이가 있었다 → 아래에 표시
 *    - null : 비교 대상이 없다(판독 불가) 또는 diff 도입(2026-08-21) 이전
 *             데이터 — 판독 불가는 본문 줄(myReviewPanel)에 이미 "판독 불가"가
 *             나오므로 여기서는 아무것도 더 보태지 않는다(2026-08-27, 같은
 *             사실의 중복 표시를 없앴다).
 *  count === 0은 이 분기에 오지 않는다 — 서버가 0이면 review_type을 패스로
 *  재분류하므로(main.py §5.5) 타이핑 건의 diff는 항상 0보다 크거나 null이다.
 *
 *  띄어쓰기 차이는 문장으로 따로 적지 않고(2026-08-27) 글자 수 옆 괄호에
 *  같이 적는다(`5자, 띄어쓰기 차이`) — 색으로 구분해봤지만, 서버가 공백을
 *  아예 빼고 비교하므로(text_diff.py _compact) **어디가** 다른지는 모르고
 *  "다르다"는 사실(spacing_diff)만 남아 있어, 위치도 못 짚어주는 칩에 색만
 *  입히는 건 정보량에 비해 과한 강조였다. 그래서 글자 차이와 나란히 괄호
 *  안에 텍스트로만 적는다.
 */
function diffLinePanel(review, isUnreadable) {
  const count = review.ocr_diff_char_count;
  if (isUnreadable) return "";
  if (count > 0) {
    const spacingNote = review.spacing_diff ? ", 띄어쓰기" : "";
    return `<div class="my-diff-line">${markedToHtml(
      review.ocr_diff_marked
    )} (${count}자${spacingNote} 차이)</div>`;
  }
  return `<div class="my-diff-line muted">차이 미기록 (2026-08-21 이전 데이터)</div>`;
}

/** OCR 난이도(1~4) 버튼 + 판독 불가 버튼 — 분류 드롭다운을 대체한 단일
 *  클릭 입력(§5.1). 둘은 하나의 선택 그룹으로 묶여 상호 배타적이다 — "읽었는데
 *  얼마나 어려웠나"(1~4)와 "읽는 것 자체가 불가능했다"(판독 불가)는 동시에
 *  성립할 수 없는 별개의 판정이기 때문이다(bindRowActions에서 서로를 끈다). */
/** 판독 불가 선택 시 텍스트 입력창을 잠근다(§4.3).
 *
 *  텍스트를 지우지 않고 회색으로 남기는 이유: 실수로 판독 불가를 눌러도
 *  난이도를 다시 고르면 입력이 그대로 돌아오기 때문이다. 화면에 남아 있는
 *  값이 저장되지는 않는다는 걸 알 수 있도록 안내 문구를 함께 보여준다
 *  (제출 로직도 판독 불가면 typed_text를 아예 보내지 않는다).
 */
function setUnreadableLock(id, locked) {
  const input = tableWrap.querySelector(`.typing-input[data-id="${id}"]`);
  if (!input) return;
  input.disabled = locked;
  input.classList.toggle("locked-unreadable", locked);
  const hint = tableWrap.querySelector(`.unreadable-hint[data-id="${id}"]`);
  if (hint) hint.hidden = !locked;

  // [OCR 복사]도 같이 잠근다. disabled는 사용자 타이핑만 막고 스크립트의
  // value 대입은 막지 못해서, 잠긴 입력창에 OCR 텍스트가 채워지는 일이 있었다 —
  // 화면에는 글자가 보이는데 제출하면 버려지므로 가장 헷갈리는 상태다.
  // (Ctrl+Enter 단축키는 disabled 입력창이 keydown을 받지 않아 자동으로 막힌다.)
  const copyBtn = tableWrap.querySelector(`.copy-ocr-btn[data-id="${id}"]`);
  if (copyBtn) copyBtn.disabled = locked;

  // 잠긴 동안에는 diff 미리보기도 의미가 없다 — 비교할 대상이 없기 때문.
  // 풀릴 때는 남아 있던 텍스트 기준으로 다시 계산해준다(입력을 한 글자 더
  // 해야 미리보기가 살아나는 어색함을 없앤다).
  const preview = tableWrap.querySelector(`.diff-preview[data-id="${id}"]`);
  if (!preview) return;
  if (locked) {
    preview.hidden = true;
    return;
  }
  const item = currentItems.find((i) => String(i.id) === String(id));
  if (item) updateDiffPreview(item, input);
}

function difficultyPicker(item, selectedLevel) {
  const buttons = PICKABLE_DIFFICULTY_LEVELS.map(
    (d) => `
      <button type="button" class="difficulty-btn" data-id="${item.id}" data-level="${d.level}"
        title="${escapeHtml(d.title)}" aria-pressed="${d.level === selectedLevel ? "true" : "false"}">
        ${d.level}
      </button>`
  ).join("");
  return `
    <div class="difficulty-picker" data-id="${item.id}">
      <span class="difficulty-picker-label">OCR 난이도</span>
      ${buttons}
      <button type="button" class="unreadable-btn" data-id="${item.id}"
        title="필기를 알아볼 수 없음 — 텍스트 입력 없이도 제출 가능"
        aria-pressed="${selectedLevel === UNREADABLE_LEVEL ? "true" : "false"}">판독 불가</button>
    </div>
  `;
}

/** 부정 표현(죽음/자살/우울 등) 체크박스 — OCR 텍스트에 감지되면 서버가
 *  auto_negative_flag로 미리 켜서 내려주고, 검수자가 직접 켜고 끌 수도
 *  있다(§5). 패스/타이핑 공통으로 하나만 두고 두 제출 경로 모두 이 값을
 *  읽어간다(bindRowActions). 카드 우측 상단에서 항상 보여야 하는 항목이라
 *  타이핑 패널(접혔을 수 있음)이 아니라 card-head에 둔다. */
function negativeExpressionCheckbox(item, existing) {
  const checked = existing ? existing.contains_negative_expression : item.auto_negative_flag;
  return `
    <label class="negative-flag-inline" data-id="${item.id}" title="부정 표현 (죽음·자살·우울 등)">
      <input type="checkbox" class="negative-check" data-id="${item.id}" ${checked ? "checked" : ""} />
      ⚠ 부정 표현
    </label>
  `;
}

/** 타이핑 입력 폼의 내용만 돌려준다. 감싸는 요소(카드 div / 테이블 tr)는
 *  모드별로 다르고, `data-typing-for`는 그 감싸는 요소에 붙는다 —
 *  토글 대상이 그 요소이기 때문이다. 패스 제출도 이 패널이 항상 펼쳐져
 *  있는 동안(§4.3) 여기 담긴 난이도/부정표현 체크박스를 함께 읽어간다.
 *
 *  [OCR 복사]는 입력 속도를 위한 것이다(§5.3) — OCR 텍스트를 그대로 가져와
 *  다른 부분만 고치면 되므로 전체를 다시 칠 필요가 없다. 자동으로 미리
 *  채워두지 않는 이유는, 읽지 않고 그대로 제출하면 "OCR이 완벽하다"는
 *  데이터가 쌓이기 때문이다 — §4.2에서 "전체 선택" 버튼을 두지 않은 것과
 *  같은 이유다. 검수자 코멘트 입력은 2026-08-21 요청으로 제거했다.
 */
function typingPanelInner(item, existing) {
  // 기존 판독 불가 건을 [수정]으로 열 때도 잠긴 상태로 그려야 한다 —
  // 여기서 안 맞추면 수정 화면에서만 입력창이 열려 모순 상태가 다시 생긴다.
  const locked = existing?.ocr_difficulty_level === UNREADABLE_LEVEL;
  return `
      <div class="typing-toolbar">
        <button type="button" class="copy-ocr-btn" data-id="${item.id}"
          title="OCR 텍스트를 입력창으로 가져옵니다 (Ctrl+Enter)">⤵ OCR 복사</button>
        <span class="typing-hint">다른 부분만 고치면 됩니다</span>
      </div>
      <textarea class="typing-input${locked ? " locked-unreadable" : ""}" data-id="${item.id}" rows="2"
        ${locked ? "disabled" : ""}
        placeholder="이미지를 보고 텍스트를 입력하세요">${escapeHtml(existing?.typed_text || "")}</textarea>
      <div class="unreadable-hint" data-id="${item.id}" ${locked ? "" : "hidden"}>
        판독 불가로 저장됩니다 — 입력한 텍스트는 저장되지 않습니다. 난이도를 다시 고르면 입력창이 열립니다.
      </div>
      <div class="diff-preview" data-id="${item.id}" hidden></div>
      <div class="typing-classify" data-id="${item.id}">
        ${difficultyPicker(item, existing?.ocr_difficulty_level)}
      </div>
      <button type="button" class="primary typing-submit" data-id="${item.id}"
        ${existing ? `data-review-id="${existing.id}"` : ""}>${existing ? "수정 저장" : "제출"}</button>
  `;
}

/** 타이핑 패널을 만들 필요가 있으면 만든다.
 *  - 아직 처리 안 함 -> 빈 입력 폼, 항상 펼쳐져 있음 (타이핑 버튼 없음)
 *  - 이미 처리함(패스든 타이핑이든) -> 기존 값이 채워진 폼, [수정]을 눌러야
 *    펼쳐짐 (2026-08-21 — 패스도 수정 대상이 됐다: 난이도를 잘못 골랐으면
 *    고칠 수 있어야 하고, 텍스트를 채워 저장하면 그 순간 타이핑으로 분류가
 *    바뀐다 — review_type은 서버가 typed_text/난이도로 다시 계산한다)
 *  완료된 레코드도 수정할 수 있어야 하므로 status로 막지 않는다. */
function typingPanelFor(item, state, wrapperClass) {
  const mine = myReview(state);
  if (state.mine_submitted && !mine) return "";
  const existing = state.mine_submitted ? mine : null;
  const hiddenAttr = existing ? "hidden" : "";
  return `<div class="${wrapperClass}" data-typing-for="${item.id}" ${hiddenAttr}>${typingPanelInner(item, existing)}</div>`;
}

// ============================================================
// 그리드 모드
// ============================================================
/** 카드 한 장의 HTML. 그리드 전체를 그릴 때(renderGrid)와 카드 한 장만 다시
 *  그릴 때(updateCard) 모두 이 함수를 쓴다 — 두 경로가 갈라지면 한쪽만
 *  고쳐질 위험이 생긴다. */
function cardHtml(item) {
  const state = item.__review;
  const key = recordKey(item);
  const done = state.mine_submitted;
  const isSelected = selected.has(key);

  const check = done
    ? doneBadge()
    : `<input type="checkbox" class="card-check" data-key="${key}" data-id="${item.id}"
         ${isSelected ? "checked" : ""} aria-label="이 건 선택"
         title="체크 후 [제출]을 누르면 이 건만 패스 처리됩니다. 여러 건을 체크하면 하단에 일괄 패스 버튼이 나타납니다." />`;

  const ocr = item.ocr_text
    ? escapeHtml(item.ocr_text)
    : `<span class="ocr-empty">(OCR 텍스트 없음)</span>`;

  const mine = myReview(state);
  const existing = state.mine_submitted ? mine : null;

  return `
    <div class="review-card ${isSelected ? "selected" : ""} ${done ? "done" : ""}" data-card-key="${key}">
      <div class="card-head">
        ${check}
        <span class="card-meta">#${item.assessment_id} · ${escapeHtml(item.sct_age_group || "-")} · ${item.question_number ?? "-"}번</span>
        ${negativeExpressionCheckbox(item, existing)}
      </div>
      <div class="card-question" title="${escapeHtml(item.sct_question || "")}">${escapeHtml(item.sct_question || "-")}</div>
      <div class="card-image-wrap">
        <img src="${imageSrc(item)}" loading="lazy" alt="SCT 답변 이미지" ${IMG_FALLBACK}
          onclick="window.__openImageModal && window.__openImageModal('${imageSrc(item)}')" />
      </div>
      <div class="card-ocr">${ocr}</div>
      ${myReviewPanel(item, state)}
      ${typingPanelFor(item, state, "card-typing")}
    </div>
  `;
}

function renderGrid(items) {
  tableWrap.innerHTML = `<div class="review-grid">${items.map(cardHtml).join("")}</div>`;
}

/** 지금 걸려 있는 필터를 사람이 읽을 수 있는 목록으로 만든다.
 *
 *  결과가 0건일 때 "조회된 데이터가 없습니다"만 보이면, 정상적인 필터 조합
 *  결과인데도 고장으로 오해하기 쉽다. 실제로 기간 필터를 켜둔 채 "타이핑한
 *  것"을 골라 0건이 나오자 버그로 신고된 적이 있다(2026-08-24). 무엇 때문에
 *  걸러졌는지 보여주고 한 번에 풀 수 있게 한다.
 *
 *  기간 필터가 보는 날짜는 **원본 SCT 데이터가 수집된 날**이지 내가 검수한
 *  날이 아니다 — "내가 처리한 것"과 조합하면 특히 헷갈리므로 그 사실을 함께 적는다.
 */
function activeFilterLabels() {
  const labels = [];
  const mineText = mineFilter.options[mineFilter.selectedIndex]?.text;
  if (mineFilter.value !== "all" && mineText) labels.push(`내 처리 상태: ${mineText}`);
  const levels = difficultyFilter.levels();
  if (levels.length) labels.push(`난이도: ${levels.join(", ")}`);
  if (negativeOnlyFilter.checked) labels.push("부정 표현만");
  const unread = difficultyFilter.unreadable();
  if (unread === "exclude") labels.push("판독 불가 제외");
  else if (unread === "only") labels.push("판독 불가만");
  const start = document.getElementById("dateStart").value;
  const end = document.getElementById("dateEnd").value;
  if (start || end) {
    labels.push(`기간(원본 수집일 기준): ${start || "처음"} ~ ${end || "끝"}`);
  }
  if (keywordInput.value.trim()) labels.push(`검색어: ${keywordInput.value.trim()}`);
  return labels;
}

function render(items) {
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
    updateBulkBar();
    return;
  }
  renderGrid(items);
  bindRowActions(items);
  updateBulkBar();
}

/** 모든 필터를 기본값으로 되돌리고 다시 조회한다. */
function resetFilters() {
  mineFilter.value = "unreviewed";
  negativeOnlyFilter.checked = false;
  keywordInput.value = "";
  difficultyFilter.reset?.();
  // 기간은 date-range.js가 관리하므로 "전체 기간" 프리셋 버튼을 눌러 되돌린다
  // (입력값만 지우면 pill 버튼의 선택 표시가 남아 상태가 어긋난다).
  document.querySelector('.range-action[data-range-value="all"]')?.click();
  currentPage = 1;
  loadRecords();
}

// ============================================================
// 선택 상태 / 일괄 패스
// ============================================================
function updateBulkBar() {
  const n = selected.size;
  if (n === 0) {
    bulkBar.hidden = true;
    return;
  }
  bulkBar.hidden = false;
  const over = n > BULK_PASS_MAX;
  bulkCountEl.textContent = over
    ? `${n}건 선택됨 — 한 번에 최대 ${BULK_PASS_MAX}건까지만 처리할 수 있습니다`
    : `${n}건 선택됨`;
  bulkPassBtn.disabled = over;
  bulkPassBtn.textContent = `선택한 ${n}건 패스 처리`;
}

function clearSelection() {
  selected.clear();
  tableWrap.querySelectorAll(".card-check").forEach((c) => {
    c.checked = false;
    c.closest(".review-card")?.classList.remove("selected");
  });
  updateBulkBar();
}

async function runBulkPass() {
  const items = currentItems.filter((i) => selected.has(recordKey(i)));
  if (!items.length) return;

  // §5.1(2026-08-21) — 패스도 난이도가 필수다. 카드마다 미리 난이도(1~4)를
  // 골라둬야 하고, 판독불가(5)는 패스와 모순이라 여기서 제외한다. 골라두지
  // 않은 건 조용히 빠뜨리지 않고 몇 건이 왜 빠졌는지 알려준다.
  const withLevel = [];
  let missingCount = 0;
  let unreadableCount = 0;
  for (const i of items) {
    const level = readDifficultySelection(i.id);
    if (level === null) missingCount += 1;
    else if (level === UNREADABLE_LEVEL) unreadableCount += 1;
    else withLevel.push({ item: i, level });
  }

  if (!withLevel.length) {
    showMessage("선택한 건에 먼저 OCR 난이도를 골라주세요.");
    return;
  }

  bulkPassBtn.disabled = true;
  try {
    const res = await fetch("/api/ocr/reviews/bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        items: withLevel.map(({ item: i, level }) => {
          const negativeBox = tableWrap.querySelector(`.negative-check[data-id="${i.id}"]`);
          return {
            assessment_id: i.assessment_id,
            drawing_id: i.drawing_id,
            answer_index: i.answer_index,
            vlm_model: i.vlm_model || null,
            ocr_text: i.ocr_text || null,
            ocr_difficulty_level: level,
            contains_negative_expression: negativeBox ? negativeBox.checked : Boolean(i.auto_negative_flag),
          };
        }),
      }),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `요청 실패 (${res.status})`);
    }
    const data = await res.json();

    // 서버가 돌려준 갱신 상태를 그대로 반영한다 (재조회 없음). 영향받은
    // 카드만 새로 그려서, 이번 일괄 패스에 포함되지 않은 다른 카드의 입력
    // 중인 내용(난이도 선택·타이핑 등)을 건드리지 않는다 — updateCard 주석 참고.
    for (const item of currentItems) {
      const st = data.states?.[recordKey(item)];
      if (st) {
        item.__review = st;
        updateCard(item);
      }
    }
    selected.clear();

    const { created = 0, duplicate = 0, invalid_reference: invalid = 0 } = data.counts || {};
    const notes = [];
    if (missingCount) notes.push(`난이도 미선택 ${missingCount}건은 제외`);
    if (unreadableCount) notes.push(`판독불가 ${unreadableCount}건은 제외(타이핑으로 제출해주세요)`);
    if (duplicate) notes.push(`이미 처리한 ${duplicate}건은 건너뜀`);
    if (invalid) notes.push(`잘못된 참조 ${invalid}건 실패`);
    showMessage(notes.length ? `${created}건 패스 처리 — ${notes.join(", ")}` : "");
  } catch (err) {
    showMessage(`일괄 패스 실패: ${err.message}`);
  } finally {
    updateBulkBar();
  }
}

// ============================================================
// 단건 제출
// ============================================================
async function patchReview(reviewId, body) {
  const res = await fetch(`/api/ocr/reviews/${reviewId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `요청 실패 (${res.status})`);
  }
  return res.json();
}

async function postReview(body) {
  const res = await fetch("/api/ocr/reviews", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `요청 실패 (${res.status})`);
  }
  return res.json();
}

/** 카드 한 장만 새로 그려 넣는다.
 *
 *  왜 필요한가: 예전에는 카드 하나를 제출/수정할 때마다 render(currentItems)로
 *  그리드 전체를 다시 그렸다. renderGrid는 모든 카드를 서버가 내려준 상태
 *  (item.__review) 그대로 새로 만들기 때문에, 아직 제출 전인 **다른** 카드에서
 *  이미 눌러둔 난이도 버튼 선택이나 타이핑 중인 텍스트는(둘 다 서버 상태가
 *  아니라 DOM에만 있던 값이라) 그 순간 전부 사라졌다 — 체크박스 선택만
 *  `selected` Set에 저장돼 있어 살아남고 나머지는 초기화되는 식이었다.
 *
 *  실제로 겪은 증상(2026-08-28): 패스 3건을 체크(+난이도 선택)해두고 별도
 *  카드 하나를 타이핑 제출하면, 그 제출 하나 때문에 그리드 전체가 다시 그려져
 *  체크된 3건의 난이도 선택만 날아갔다. "선택한 3건 패스 처리"를 눌러도
 *  난이도가 비어 있으니 서버로 보낼 게 하나도 없어 조용히 아무 일도 안 하는
 *  것처럼 보였다(§4.2 "난이도 미선택" 안내만 뜸).
 *
 *  그래서 방금 바뀐 카드 하나만 DOM에서 교체한다 — 다른 카드의 엘리먼트는
 *  건드리지 않으므로 그 안의 로컬 입력 상태가 그대로 보존된다. */
function updateCard(item) {
  const key = recordKey(item);
  const el = tableWrap.querySelector(`[data-card-key="${key}"]`);
  if (!el) {
    // 화면에 없다면(필터가 바뀌었거나 하는 드문 경우) 전체를 다시 그리는
    // 수밖에 없다.
    render(currentItems);
    return;
  }
  const wrapper = document.createElement("div");
  wrapper.innerHTML = cardHtml(item);
  const newEl = wrapper.firstElementChild;
  el.replaceWith(newEl);
  // scope를 새 카드 하나로 좁혀서, 이미 리스너가 붙어 있는 다른 카드에
  // 리스너가 중복으로 붙는 것을 막는다(bindRowActions 주석 참고).
  bindRowActions([item], newEl);
}

/**
 * §4.2 "처리 즉시 다음 미검토 행으로 포커스 자동 이동".
 *
 * 제출 응답에 갱신된 상태가 들어 있으므로 목록을 다시 불러오지 않는다.
 */
function afterSubmit(item, state) {
  item.__review = state;
  selected.delete(recordKey(item));
  const processedIndex = currentItems.indexOf(item);
  updateCard(item);
  updateBulkBar();

  const candidates = [...tableWrap.querySelectorAll(".card-check")];
  const next =
    candidates.find((el) => {
      const idx = currentItems.findIndex((i) => String(i.id) === el.dataset.id);
      return idx > processedIndex;
    }) || candidates[0];

  next?.focus();
  next?.scrollIntoView({ block: "center", behavior: "smooth" });
}

/** 카드/행의 난이도 선택 상태를 읽는다 — 1~4 버튼 또는 판독불가 버튼 중
 *  눌린 것을 찾아 숫자로 돌려준다(§5.1). 아무것도 안 골랐으면 null.
 *  패스(§4.2)와 타이핑 제출 양쪽에서 공통으로 쓴다. */
function readDifficultySelection(id) {
  const levelBtn = tableWrap.querySelector(`.difficulty-btn[data-id="${id}"][aria-pressed="true"]`);
  const unreadableBtn = tableWrap.querySelector(`.unreadable-btn[data-id="${id}"][aria-pressed="true"]`);
  if (unreadableBtn) return UNREADABLE_LEVEL;
  if (levelBtn) return Number(levelBtn.dataset.level);
  return null;
}

/** 입력창 아래에 "OCR과 다른 부분"을 대괄호로 표시한다 (§5.3 미리보기).
 *  입력이 비어 있으면 보여줄 게 없으므로 숨긴다. */
function updateDiffPreview(item, input) {
  const panel = tableWrap.querySelector(`.diff-preview[data-id="${item.id}"]`);
  if (!panel) return;
  const typed = input.value.trim();
  if (!typed) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  panel.hidden = false;
  panel.innerHTML = renderDiffHtml(item.ocr_text || "", typed, escapeHtml);
}

/** 카드 액션(체크박스/난이도 버튼/타이핑 등)에 이벤트를 붙인다.
 *
 *  `scope`를 받는 이유: updateCard()가 카드 한 장만 새로 그려 넣을 때, 전체
 *  tableWrap을 다시 스캔해 바인딩하면 이미 바인딩돼 있던 **다른** 카드에
 *  똑같은 리스너가 한 번 더 붙어서(중복 제출 등) 문제가 생긴다. scope를
 *  그 카드 엘리먼트 하나로 좁혀서 새로 만든 카드에만 리스너를 붙인다. 전체
 *  그리드를 그릴 때는 기본값(tableWrap)을 그대로 쓴다. */
function bindRowActions(items, scope = tableWrap) {
  const byId = new Map(items.map((item) => [String(item.id), item]));

  // 체크박스 (그리드 전용)
  scope.querySelectorAll(".card-check").forEach((box) => {
    box.addEventListener("change", () => {
      const key = box.dataset.key;
      if (box.checked) selected.add(key);
      else selected.delete(key);
      box.closest(".review-card")?.classList.toggle("selected", box.checked);
      updateBulkBar();
    });
  });

  // 카드 체크박스를 자동으로 끈다 — 타이핑 시작과 판독 불가 선택 둘 다 "이
  // 건은 더 이상 단순 패스가 아니게 된" 순간이라 공통으로 쓴다(2026-08-31,
  // 판독 불가 쪽으로 확장). 체크된 채로 [제출]하면 패스로 처리되므로
  // (typing-submit의 wantsPass 분기), 모순 조합이 생기기 전에 미리 꺼서
  // 애초에 안 만들어지게 한다 — 판독 불가 버튼이 입력창을 잠그는 것과 같은
  // 접근이다. 목록을 불러올 때 체크박스가 기본으로 켜져 있어서(§4.2), 이
  // 처리가 없으면 "판독 불가를 고르고 제출"이 매번 "판독 불가는 패스로
  // 처리할 수 없습니다" 에러로 막혔다.
  const uncheckPassBox = (id) => {
    const box = tableWrap.querySelector(`.card-check[data-id="${id}"]`);
    if (!box || !box.checked) return;
    const item = byId.get(id);
    box.checked = false;
    if (item) selected.delete(recordKey(item));
    box.closest(".review-card")?.classList.remove("selected");
    updateBulkBar();
  };

  // 난이도(1~4) + 판독 불가 버튼 — 같은 data-id 그룹 안에서 단일 선택으로
  // 토글한다. 판독 불가와 난이도 그레이드는 동시에 성립할 수 없는 판정이라
  // 하나의 선택 그룹으로 묶어 서로를 끈다.
  const selectLevelButton = (id, chosenBtn) => {
    tableWrap
      .querySelectorAll(`.difficulty-btn[data-id="${id}"], .unreadable-btn[data-id="${id}"]`)
      .forEach((b) => b.setAttribute("aria-pressed", String(b === chosenBtn)));
    const isUnreadable = chosenBtn.classList.contains("unreadable-btn");
    // 판독 불가는 "옮겨 적을 내용이 없다"는 판정이라 입력창과 동시에 성립하지
    // 않는다. 제출 시점에 에러로 막는 대신 여기서 입력창을 잠가, 모순 상태
    // 자체가 만들어지지 않게 한다. 이미 친 텍스트는 지우지 않고 회색으로
    // 남겨둔다 — 실수로 눌렀을 때 난이도를 다시 고르면 그대로 되살아난다
    // (저장은 되지 않는다, 아래 제출 로직 참고).
    setUnreadableLock(id, isUnreadable);
    // 판독 불가는 패스와도 동시에 성립할 수 없다(§4.2) — 체크돼 있으면 끈다.
    if (isUnreadable) uncheckPassBox(id);
  };
  scope.querySelectorAll(".difficulty-btn, .unreadable-btn").forEach((btn) => {
    btn.addEventListener("click", () => selectLevelButton(btn.dataset.id, btn));
  });

  // §5.3 입력 속도 — [OCR 복사]로 OCR 텍스트를 입력창에 가져온다.
  // 커서를 끝으로 보내 바로 수정에 들어갈 수 있게 한다.
  const copyOcrInto = (item, input) => {
    input.value = item.ocr_text || "";
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    updateDiffPreview(item, input);
  };

  scope.querySelectorAll(".copy-ocr-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const item = byId.get(btn.dataset.id);
      const input = tableWrap.querySelector(`.typing-input[data-id="${btn.dataset.id}"]`);
      if (item && input) copyOcrInto(item, input);
    });
  });

  // 입력창에서 OCR과 다른 부분을 실시간으로 표시한다(§5.3). 저장되는 diff는
  // 서버가 다시 계산하므로 이건 어디까지나 미리보기다.
  scope.querySelectorAll(".typing-input").forEach((input) => {
    const item = byId.get(input.dataset.id);
    if (!item) return;
    updateDiffPreview(item, input);
    input.addEventListener("input", () => {
      updateDiffPreview(item, input);
      // 타이핑을 시작하면 "단독 패스" 체크를 자동으로 끈다(2026-08-28,
      // uncheckPassBox — 판독 불가 선택 시와 같은 헬퍼를 쓴다).
      // 체크박스는 빠른 일괄 처리를 위한 지름길일 뿐이고, 우선순위는 항상
      // 텍스트 내용이어야 한다 — 체크된 채로 [제출]을 누르면 입력창 내용은
      // 버려지고 패스로 저장되므로(위 typing-submit의 wantsPass 분기), 체크된
      // 채로 타이핑을 시작하는 순간 그 모순된 상태 자체가 생기지 않게
      // 미리 체크를 풀어준다.
      if (input.value.trim()) uncheckPassBox(input.dataset.id);
    });
    // 빈 입력창에서 Ctrl+Enter는 [OCR 복사] 단축키로 쓴다 — 마우스로
    // 버튼까지 가지 않고 키보드만으로 이어서 작업할 수 있게 한다.
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        copyOcrInto(item, input);
      }
    });
  });

  // 타이핑 패널 열기 — 신규 입력은 항상 펼쳐져 있어 버튼이 필요 없고,
  // 수정([data-action="edit"])만 토글로 연다.
  scope.querySelectorAll('[data-action="edit"]').forEach((btn) => {
    btn.addEventListener("click", () => {
      const panel = tableWrap.querySelector(`[data-typing-for="${btn.dataset.id}"]`);
      if (!panel) return;
      panel.hidden = !panel.hidden;
      if (!panel.hidden) panel.querySelector(".typing-input")?.focus();
    });
  });

  scope.querySelectorAll(".typing-submit").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const item = byId.get(id);
      const input = tableWrap.querySelector(`.typing-input[data-id="${id}"]`);
      const reviewId = btn.dataset.reviewId;
      const ocrDifficultyLevel = readDifficultySelection(id);
      // 판독 불가면 입력창에 값이 남아 있어도 보내지 않는다. 잠긴 입력창의
      // value는 JS에서 그대로 읽히므로(disabled여도 마찬가지), 여기서 명시적으로
      // 비워야 서버의 "텍스트+판독 불가" 검증(400)에 걸리지 않는다.
      const rawText = input ? input.value.trim() : "";
      const text = ocrDifficultyLevel === UNREADABLE_LEVEL ? "" : rawText;
      const negativeBox = tableWrap.querySelector(`.negative-check[data-id="${id}"]`);
      const containsNegativeExpression = negativeBox ? negativeBox.checked : false;

      // §5.4(2026-08-21) — 신규 제출에서 카드의 선택 체크박스가 켜져 있으면
      // "이 건 하나만 단독 패스"로 처리한다. 여러 건 모아 하단 바에서
      // 처리하는 일괄 패스와 같은 체크박스를 그대로 쓴다 — 별도의 [패스]
      // 버튼을 새로 두면 그 버튼이 체크 상태를 무시하는 두 번째 진입점이
      // 되어 혼란을 준다는 의견을 반영해, 이미 있는 체크박스+제출 조합만
      // 남긴다. 수정([data-review-id] 존재)에는 적용하지 않는다 — 수정은
      // 항상 입력 내용으로만 패스/타이핑을 다시 판단한다(server-side).
      const checkbox = tableWrap.querySelector(`.card-check[data-id="${id}"]`);
      const wantsPass = !reviewId && checkbox && checkbox.checked;

      // 난이도는 이제 패스/타이핑 구분 없이 항상 필요하다(§5.1). 텍스트는
      // "판독 불가"처럼 옮겨 적을 내용이 없을 수 있어 선택 사항이다.
      if (ocrDifficultyLevel === null) {
        showMessage("OCR 난이도(1~5)를 선택해주세요.");
        return;
      }
      if (wantsPass && ocrDifficultyLevel === UNREADABLE_LEVEL) {
        showMessage("판독 불가는 패스로 처리할 수 없습니다 — 체크를 해제하고 타이핑으로 제출해주세요.");
        return;
      }
      // "텍스트 + 판독 불가"는 위에서 text를 비웠으므로 여기까지 오지 않는다
      // (입력창도 잠겨 있다). 서버에도 같은 검증이 있어 API 직접 호출은 400으로
      // 막힌다 — 화면과 서버 양쪽에서 같은 규칙을 지킨다.

      btn.disabled = true;
      try {
        if (reviewId) {
          // 기존 내 의견 수정 — 직전 값은 서버가 이력으로 보존한다
          // 수정 시 ocr_text를 보내지 않는다 — 서버가 최초 검수 때 저장한
          // ocr_text_snapshot과 비교해 diff를 다시 계산한다(§5.3).
          const result = await patchReview(reviewId, {
            typed_text: text,
            ocr_difficulty_level: ocrDifficultyLevel,
            contains_negative_expression: containsNegativeExpression,
          });
          item.__review = result.state;
          updateCard(item);
          showMessage("");
        } else if (wantsPass) {
          // 단독 패스 — 타이핑 입력창 내용은 쓰지 않는다(패스에는 텍스트가
          // 없다, main.py의 서버 쪽 검증과 같은 규칙).
          const result = await postReview({
            assessment_id: item.assessment_id,
            drawing_id: item.drawing_id,
            answer_index: item.answer_index,
            review_type: "normal_check",
            vlm_model: item.vlm_model || null,
            ocr_text: item.ocr_text || null,
            ocr_difficulty_level: ocrDifficultyLevel,
            contains_negative_expression: containsNegativeExpression,
          });
          showMessage("");
          afterSubmit(item, result.state);
        } else {
          const result = await postReview({
            assessment_id: item.assessment_id,
            drawing_id: item.drawing_id,
            answer_index: item.answer_index,
            review_type: "transcription",
            vlm_model: item.vlm_model || null,
            typed_text: text || null,
            // 화면에 보인 OCR 텍스트 — 서버가 이걸로 diff를 계산하고
            // 스냅샷으로 저장한다 (§5.3)
            ocr_text: item.ocr_text || null,
            ocr_difficulty_level: ocrDifficultyLevel,
            contains_negative_expression: containsNegativeExpression,
          });
          showMessage("");
          afterSubmit(item, result.state);
        }
      } catch (err) {
        btn.disabled = false;
        const action = reviewId ? "수정" : wantsPass ? "패스 제출" : "타이핑 제출";
        showMessage(`${action} 실패: ${err.message}`);
      }
    });
  });
}

// 페이저는 admin 화면과 공용이다 (ui-utils.js createPager) — 페이지 상태와
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
  // §4.1 내 처리 상태 필터 (기본: 내가 아직 처리하지 않은 것)
  params.set("mine", mineFilter.value);
  // 이미지가 없는 레코드(= OCR 텍스트도 없는 빈 레코드)는 검수 대상이 아니다
  params.set("has_image", "true");
  // 난이도는 여러 개를 반복 파라미터로 보낸다 (?difficulty_level=1&difficulty_level=2)
  for (const level of difficultyFilter.levels()) params.append("difficulty_level", String(level));
  // 판독 불가 3단 선택 — "포함"은 아무 파라미터도 보내지 않는다는 뜻이다.
  const unreadable = difficultyFilter.unreadable();
  if (unreadable === "exclude") params.set("exclude_unreadable", "true");
  else if (unreadable === "only") params.set("unreadable_only", "true");
  if (negativeOnlyFilter.checked) params.set("negative_only", "true");
  // 기본이 켜짐이라 대부분의 조회에 붙는다 (§5.1)
  const start = document.getElementById("dateStart").value;
  const end = document.getElementById("dateEnd").value;
  if (start) params.set("date_start", start);
  if (end) params.set("date_end", end);
  if (keywordInput.value.trim()) params.set("keyword", keywordInput.value.trim());
  return params;
}

/** 목록 한 페이지분의 검수 상태를 요청 1번으로 가져온다. */
async function fetchReviewStates(items) {
  if (!items.length) return {};
  try {
    const res = await fetch("/api/ocr/review-states", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        keys: items.map((i) => [i.assessment_id, i.drawing_id, i.answer_index]),
      }),
    });
    if (!res.ok) throw new Error(`요청 실패 (${res.status})`);
    const data = await res.json();
    return data.states || {};
  } catch (err) {
    showMessage(`검수 상태를 불러오지 못했습니다: ${err.message}`);
    return {};
  }
}

async function loadRecords() {
  showMessage("");
  // 페이지/필터가 바뀌면 이전 선택은 의미가 없다 (보이지 않는 건을 패스하면 안 됨)
  selected.clear();
  updateBulkBar();
  tableWrap.innerHTML = `<div class="empty">불러오는 중...</div>`;
  try {
    const res = await fetch(`/api/sct/records?${buildQuery().toString()}`);
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `요청 실패 (${res.status})`);
    }
    const data = await res.json();

    const states = await fetchReviewStates(data.items);
    currentItems = data.items.map((item) => {
      item.__review = states[recordKey(item)] || EMPTY_STATE;
      return item;
    });

    // 새로 불러온 건은 패스 체크박스를 기본으로 켜둔다(2026-08-31, 요청) —
    // 대부분은 OCR이 그대로 맞아서 체크만 하고 넘어가던 반복 클릭을 줄인다.
    // 난이도는 여전히 카드마다 직접 골라야 제출되므로(§5.1, runBulkPass 참고)
    // 아래 "전체 선택 버튼은 안 둔다" 원칙의 핵심(이미지를 안 보고 통째로
    // 넘기지 못하게 하는 것)은 그대로 유지된다 — 안 맞는 건은 타이핑을
    // 시작하면 체크가 자동으로 꺼진다(아래 "단독 패스" 체크 해제 참고).
    for (const item of currentItems) {
      if (!item.__review.mine_submitted) selected.add(recordKey(item));
    }

    render(currentItems);
    renderPager(data.total, data.page, data.total_pages);
    if (data.warning) showMessage(data.warning);
  } catch (err) {
    tableWrap.innerHTML = "";
    showMessage(`데이터를 불러오지 못했습니다: ${err.message}`);
  }
}

async function loadCurrentUser() {
  const res = await fetch("/api/auth/me");
  if (!res.ok) {
    location.href = "/login";
    return;
  }
  currentUser = await res.json();
}

async function init() {
  setupImageModal();

  // 상단 네비(nav-auth.js)의 새로고침 버튼이 부른다 — 필터/페이지는 그대로 두고
  // 목록만 다시 불러온다(2026-08-31, "조회" 버튼을 대체).
  window.__refreshCurrentView = () => loadRecords();

  difficultyFilter = bindDifficultyFilter({
    mountId: "difficultyFilter",
    onChange: () => {
      currentPage = 1;
      loadRecords();
    },
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

  bulkClearBtn.addEventListener("click", clearSelection);
  bulkPassBtn.addEventListener("click", runBulkPass);

  keywordInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      currentPage = 1;
      loadRecords();
    }
  });
  [mineFilter, negativeOnlyFilter].forEach((control) => {
    control.addEventListener("change", () => {
      currentPage = 1;
      loadRecords();
    });
  });

  await loadCurrentUser();
  await loadRecords();
}

init();
