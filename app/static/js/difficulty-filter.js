// 난이도 + 판독 불가 필터 (2026-09-01 개편) — 검수자/admin 화면이 함께 쓴다.
//
// 예전에는 팝오버 뒤에 접어뒀지만, 필터 줄을 버튼 두 줄(검수/난이도)로 항상
// 펼쳐두는 배치로 바뀌면서 이 위젯도 상시 노출되는 한 줄짜리 다중선택
// 버튼으로 다시 짰다.
//
// **판독 불가(5)를 1~4와 같은 자리의 값으로 취급한다.** 예전에는 별도의
// "제외/포함/만 보기" 3단 선택으로 뺐었는데(난이도 도입 이전 레거시 데이터가
// 난이도=NULL로 남아 있어 "난이도 5"만으로는 못 잡는다는 이유였다), 실제
// 데이터를 확인해보니 그런 레거시 행이 없고(2026-09-01 DB 조회, transcription
// 67건 중 NULL 난이도 0건) 서버도 제출 시 난이도를 항상 필수로 강제해
// (main.py submit_review) 앞으로도 생길 수 없다. 즉 "판독 불가"는 지금
// 데이터에서 `ocr_difficulty_level == 5`와 완전히 동치라, 별도 파라미터
// (exclude_unreadable/unreadable_only) 없이 기존 difficulty_level 다중선택
// 하나로 흡수해도 결과가 같다 — 오히려 "판독불가 + 4만" 같은, 3단 선택으로는
// 못 하던 조합까지 자연스럽게 가능해진다.

import { DIFFICULTY_LEVELS as LEVELS } from "./difficulty.js?v=2";

// 판독 불가를 제외한 기본 선택값 — 예전 3단 선택의 기본값("제외")과 같은
// 결과를 내도록, 시작할 때는 1~4만 켜둔다.
const PICKABLE = LEVELS.filter((d) => d.level !== 5);
const DEFAULT_LEVELS = PICKABLE.map((d) => d.level);
const ALL_LEVELS = LEVELS.map((d) => d.level);

/**
 * @param {object} opts
 * @param {string} opts.mountId  이 id를 가진 빈 컨테이너 안에 위젯을 그린다
 * @param {() => void} opts.onChange  선택이 바뀔 때마다 호출 (목록 재조회용)
 * @returns {{ levels: () => number[], reset: () => void, setEnabled: (enabled: boolean) => void }}
 */
export function bindDifficultyFilter({ mountId, onChange }) {
  const mount = document.getElementById(mountId);
  if (!mount) {
    return { levels: () => [], reset: () => {}, setEnabled: () => {} };
  }

  const selected = new Set(DEFAULT_LEVELS);

  // "전체~4"는 검수 상태 줄(전체/패스/수정)과 같은 연결된 pill 그룹으로 묶는다
  // — 한 자리 안에서 갈라지는 값들이라는 걸 모양으로도 보여준다. 판독 불가는
  // 값 자체는 이 다중선택 집합의 일부지만(선택 로직은 동일), "얼마나
  // 어려웠나"와는 다른 종류의 판정이라(difficulty.js 주석 참고) 그룹 밖에
  // 따로 띄워 부정표현 토글과 같은 스타일을 쓴다.
  mount.classList.add("filter-row");
  mount.innerHTML = `
    <div class="filter-btn-group">
      <button type="button" class="filter-btn" data-all="true">전체</button>
      ${PICKABLE.map(
        (d) => `
        <button type="button" class="filter-btn" data-level="${d.level}" title="${d.level} ${d.short}">${d.level} ${d.short}</button>`
      ).join("")}
    </div>
    <button type="button" class="filter-btn-toggle" data-level="5" title="판독 불가">판독불가</button>
  `;

  const allButton = mount.querySelector("[data-all]");

  function syncButtons() {
    mount.querySelectorAll("[data-level]").forEach((b) => {
      b.setAttribute("aria-pressed", String(selected.has(Number(b.dataset.level))));
    });
    allButton.setAttribute("aria-pressed", String(selected.size === ALL_LEVELS.length));
  }

  mount.querySelectorAll("[data-level]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const level = Number(btn.dataset.level);
      if (selected.has(level)) selected.delete(level);
      else selected.add(level);
      // 전부 꺼진 상태는 만들지 않는다 — 결과는 "전체"인데 화면은 아무것도
      // 안 골라진 것처럼 보여 가장 헷갈리는 조합이기 때문이다.
      if (!selected.size) DEFAULT_LEVELS.forEach((l) => selected.add(l));
      syncButtons();
      onChange();
    });
  });

  allButton.addEventListener("click", () => {
    if (selected.size === ALL_LEVELS.length) return;
    ALL_LEVELS.forEach((l) => selected.add(l));
    syncButtons();
    onChange();
  });

  function reset(notify = true) {
    selected.clear();
    DEFAULT_LEVELS.forEach((l) => selected.add(l));
    syncButtons();
    if (notify) onChange();
  }

  // "아직 처리하지 않은 것"을 보는 동안에는 난이도가 존재하지 않는 건뿐이라
  // 이 필터를 걸어도 항상 0건이 된다(main.py의 mine != "unreviewed" 가드와
  // 같은 경계). 버튼을 눌러도 아무 일도 안 일어나는 것처럼 보이지 않도록
  // 아예 잠가서 눈에 보이게 한다.
  function setEnabled(enabled) {
    mount.querySelectorAll("button").forEach((b) => {
      b.disabled = !enabled;
    });
  }

  syncButtons();
  return {
    // 1~5가 전부 켜져 있으면 빈 배열 = 난이도 필터 미적용. 나열해 보내면
    // 난이도 값이 없는 건(미처리 포함)이 조용히 빠지기 때문이다.
    levels: () => (selected.size === ALL_LEVELS.length ? [] : [...selected].sort()),
    reset: () => reset(false),
    setEnabled,
  };
}
