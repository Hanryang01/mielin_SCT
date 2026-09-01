// 검수자 화면(review.js)과 admin 화면(admin.js)이 함께 쓰는 UI 조각.
//
// 아래 네 개(showMessage / markedToHtml / setupImageModal / createPager)는
// 2026-08-27까지 두 파일에 **글자 단위로 똑같은 코드**로 각각 있었다. 한쪽만
// 고쳐지면 같은 정보가 화면마다 다르게 보이므로(실제로 그런 사례가 반복됐다)
// 한 곳으로 모았다.

export function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

/** 화면 상단 알림 박스(#message)에 한 줄 띄우거나(text) 지운다(빈 값).
 *
 *  두 화면이 같은 id의 마크업을 쓰므로 호출 시점에 찾아 쓴다 — 모듈 로드
 *  시점에 잡아두면 이 함수를 쓰는 화면마다 그 요소가 반드시 있어야 한다는
 *  숨은 전제가 생긴다. */
export function showMessage(text, elementId = "message") {
  const el = document.getElementById(elementId);
  if (!el) return;
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = text;
}

/** 서버가 준 대괄호 표기(§5.3)를 강조 표시로 바꾼다.
 *  대괄호 안쪽만 <mark>로 감싸고 나머지는 전부 이스케이프한다. */
export function markedToHtml(marked) {
  if (!marked) return "";
  let html = "";
  let rest = marked;
  while (rest.length) {
    const open = rest.indexOf("[");
    const close = open === -1 ? -1 : rest.indexOf("]", open + 1);
    if (open === -1 || close === -1) {
      html += escapeHtml(rest);
      break;
    }
    html += escapeHtml(rest.slice(0, open));
    html += `<mark class="diff-replace">[${escapeHtml(rest.slice(open + 1, close))}]</mark>`;
    rest = rest.slice(close + 1);
  }
  return html;
}

/** 이미지 확대 모달을 window에 한 번만 설치한다.
 *  카드/표의 이미지가 인라인 onclick으로 window.__openImageModal을 부른다. */
export function setupImageModal() {
  if (window.__openImageModal) return;
  window.__openImageModal = (src) => {
    const overlay = document.createElement("div");
    overlay.className = "image-modal";
    overlay.innerHTML = `<img src="${src}" alt="SCT 답변 이미지 확대" />`;
    overlay.addEventListener("click", () => overlay.remove());
    document.body.appendChild(overlay);
  };
}

/** 연결된 pill 버튼 그룹을 상호 배타적인 단일 선택으로 묶는다 (2026-09-01) —
 *  검수 상태 필터(전체/미처리/패스/수정)가 review.js/admin.js 양쪽에서 같은
 *  모양·동작을 쓴다. 날짜 프리셋(date-range.js)과 같은 시각 언어(.range-action류
 *  대신 filter-btn류)를 쓰지만, 날짜 계산 같은 도메인 로직이 없어 훨씬 단순하다.
 *
 *  @param {{mountId: string, defaultValue: string, onChange: (value: string) => void}} opts
 *  @returns {{ value: () => string, set: (value: string) => void }}
 */
export function bindRadioGroup({ mountId, defaultValue, onChange }) {
  const mount = document.getElementById(mountId);
  let value = defaultValue;

  function sync() {
    mount.querySelectorAll("[data-value]").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(btn.dataset.value === value));
    });
  }

  mount.querySelectorAll("[data-value]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.value === value) return;
      value = btn.dataset.value;
      sync();
      onChange(value);
    });
  });

  sync();
  return {
    value: () => value,
    set: (v) => {
      value = v;
      sync();
    },
  };
}

/** 독립적으로 켜고 끄는 토글 버튼 — "부정 표현만"처럼 상태 그룹과 나란히
 *  있지만 동시에 여러 개 켤 수 있는 별개 속성에 쓴다(체크박스를 대체). */
export function bindToggleButton(id, { defaultValue = false, onChange } = {}) {
  const el = document.getElementById(id);
  let value = defaultValue;

  function sync() {
    el.setAttribute("aria-pressed", String(value));
  }

  el.addEventListener("click", () => {
    value = !value;
    sync();
    onChange(value);
  });

  sync();
  return {
    value: () => value,
    set: (v) => {
      value = v;
      sync();
    },
  };
}

/** 페이저를 그리는 함수를 만들어 돌려준다.
 *
 *  페이지 상태 변수와 재조회 방법은 화면마다 다르므로(review.js/admin.js가 각자
 *  currentPage와 loadRecords를 들고 있다) onPage 콜백으로 받는다.
 *
 *  @param {{pagerId?: string, onPage: (page: number) => void}} opts
 *  @returns {(total: number, page: number, totalPages: number) => void}
 */
export function createPager({ pagerId = "pager", onPage }) {
  const pagerEl = document.getElementById(pagerId);
  return function renderPager(total, page, totalPages) {
    if (total === 0) {
      pagerEl.hidden = true;
      return;
    }
    pagerEl.hidden = false;
    pagerEl.innerHTML = `
      <span>총 ${total}건 / ${page} / ${totalPages}페이지</span>
      <button id="prevPageBtn" ${page <= 1 ? "disabled" : ""}>이전</button>
      <button id="nextPageBtn" ${page >= totalPages ? "disabled" : ""}>다음</button>
    `;
    pagerEl
      .querySelector("#prevPageBtn")
      ?.addEventListener("click", () => onPage(Math.max(1, page - 1)));
    pagerEl
      .querySelector("#nextPageBtn")
      ?.addEventListener("click", () => onPage(Math.min(totalPages, page + 1)));
  };
}
