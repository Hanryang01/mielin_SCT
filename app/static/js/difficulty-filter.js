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

import {
  DIFFICULTY_LEVELS as LEVELS,
  PICKABLE_DIFFICULTY_LEVELS as PICKABLE,
  UNREADABLE_LEVEL,
} from "./difficulty.js?v=2";

// admin/review 둘 다 검수 상태·난이도 전부 "전체"로 시작한다(2026-09-01) —
// 필터를 직관적으로 쉽게 바꿀 수 있으니, 특정 값을 기본으로 숨겨두기보다는
// 열자마자 전체를 보여주고 필요할 때 좁히는 쪽으로 통일했다.
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

  const selected = new Set(ALL_LEVELS);

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
    <button type="button" class="filter-btn-toggle" data-level="${UNREADABLE_LEVEL}" title="판독 불가">판독불가</button>
  `;

  const allButton = mount.querySelector("[data-all]");

  const isAllSelected = () => selected.size === ALL_LEVELS.length;
  function selectAll() {
    selected.clear();
    ALL_LEVELS.forEach((l) => selected.add(l));
  }

  // "전체"가 켜진 상태(=5개 다 선택)는 실제 값이 아니라 "다 선택돼 있다"는
  // 파생 상태라서, 그동안은 개별 버튼도 함께 눌린 것처럼 보였다. 그런데 그
  // 상태에서 개별 버튼 하나를 누르면 안 건드린 나머지가 화면에 갑자기
  // 나타나는 것처럼 보여 혼란스러웠다(2026-09-01 지적). 그래서 "전체" 상태를
  // 벗어나는 첫 클릭은 토글이 아니라 **그 값 하나로 교체**한다 — 결과가
  // 클릭한 버튼 하나뿐이라 다른 버튼이 따라 바뀌는 일이 없다.
  function syncButtons() {
    const allSelected = isAllSelected();
    mount.querySelectorAll("[data-level]").forEach((b) => {
      b.setAttribute(
        "aria-pressed",
        String(!allSelected && selected.has(Number(b.dataset.level)))
      );
    });
    allButton.setAttribute("aria-pressed", String(allSelected));
  }

  mount.querySelectorAll("[data-level]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const level = Number(btn.dataset.level);
      if (isAllSelected()) {
        selected.clear();
        selected.add(level);
      } else if (selected.has(level)) {
        selected.delete(level);
        // 마지막 하나까지 꺼서 전부 빈 상태가 되면 "전체"로 되돌린다 — 아무
        // 것도 안 고른 상태를 별도로 두지 않고, 좁혔다가 완전히 놓으면 다시
        // 넓어지는 흐름이 자연스럽기 때문이다.
        if (!selected.size) selectAll();
      } else {
        selected.add(level);
      }
      syncButtons();
      onChange();
    });
  });

  allButton.addEventListener("click", () => {
    if (isAllSelected()) return;
    selectAll();
    syncButtons();
    onChange();
  });

  function reset(notify = true) {
    selectAll();
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
    levels: () => (isAllSelected() ? [] : [...selected].sort()),
    reset: () => reset(false),
    setEnabled,
  };
}
