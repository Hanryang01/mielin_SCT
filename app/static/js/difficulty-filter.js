// 난이도 + 판독 불가 필터 (2026-08-24) — 검수자/admin 화면이 함께 쓴다.
//
// 버튼 하나로 접어두고 클릭하면 팝오버로 펼친다. 기간 필터의 `📅 직접 입력 ▾`
// (range-popover)와 검수 카드의 난이도 알약(difficulty-btn)이 이미 쓰던 두 패턴을
// 조합한 것이라 새로운 조작법을 배울 필요가 없다.
//
// **난이도(1~4)와 판독 불가를 한 팝오버 안에서 함께 다룬다.** 두 값은 함께
// 조정하는 경우가 많고("판독 불가 빼고 어려운 것만" 등), 검수 카드의 입력부도
// `[1][2][3][4] [판독 불가]`로 나란히 놓여 있어 구조가 일관된다.
//
// 판독 불가를 난이도 등급(5번 버튼)으로 두지 않는 이유:
//   - "읽었는데 얼마나 어려웠나"(1~4)와 "읽지 못했다"는 서로 다른 종류의 판정이다.
//   - 한동안 별도 체크박스로 뺐는데 난이도에 5번 버튼이 남아 있어서, 같은 대상을
//     두 컨트롤이 각각 조작했다 — "판독 불가 제외 + 난이도 5"처럼 서로 반대되는
//     지시가 가능해져 결과가 항상 0건이 되는 조합이 있었다. 이제 판독 불가는
//     아래 3단 선택 하나만 담당한다.
//
// 접힌 라벨은 **현재 설정을 그대로 말해준다**(`판독 불가 제외` 등). 기본값이
// "제외"라 데이터가 빠진 상태인데 라벨이 중립적이면("전체 난이도") 무엇이
// 빠졌는지 모른 채 쓰게 되기 때문이다.

import { PICKABLE_DIFFICULTY_LEVELS as LEVELS } from "./difficulty.js?v=2";

const DEFAULT_LEVELS = LEVELS.map((d) => d.level);
const ALL = DEFAULT_LEVELS.length;

// 판독 불가 처리 방식. 서버 파라미터와의 대응은 호출부(buildQuery)가 맡는다.
const UNREADABLE_MODES = [
  { value: "exclude", label: "제외", desc: "판독 불가 제외" },
  { value: "include", label: "포함", desc: "전체 난이도" },
  { value: "only", label: "만 보기", desc: "판독 불가만" },
];
const DEFAULT_UNREADABLE = "exclude";

/**
 * @param {object} opts
 * @param {string} opts.mountId  이 id를 가진 빈 컨테이너 안에 위젯을 그린다
 * @param {() => void} opts.onChange  선택이 바뀔 때마다 호출 (목록 재조회용)
 * @returns {{ levels: () => number[], unreadable: () => string, reset: () => void }}
 */
export function bindDifficultyFilter({ mountId, onChange }) {
  const mount = document.getElementById(mountId);
  if (!mount) {
    return { levels: () => [], unreadable: () => DEFAULT_UNREADABLE, reset: () => {} };
  }

  const selected = new Set(DEFAULT_LEVELS);
  let unreadableMode = DEFAULT_UNREADABLE;

  mount.classList.add("level-picker");
  mount.innerHTML = `
    <button type="button" class="level-toggle" aria-expanded="false">
      <span class="level-toggle-label"></span> <span aria-hidden="true">▾</span>
    </button>
    <div class="level-popover" hidden>
      <div class="level-section" data-section="levels">
        <div class="level-section-title">난이도</div>
        <div class="level-buttons">
          ${LEVELS.map(
            (d) => `
            <button type="button" class="level-btn" data-level="${d.level}"
              aria-pressed="true" title="${d.level} ${d.short}">
              ${d.level}<span class="level-btn-short">${d.short}</span>
            </button>`
          ).join("")}
        </div>
      </div>
      <div class="level-section">
        <div class="level-section-title">판독 불가</div>
        <div class="level-modes">
          ${UNREADABLE_MODES.map(
            (m) => `
            <button type="button" class="level-mode" data-mode="${m.value}"
              aria-pressed="${m.value === DEFAULT_UNREADABLE ? "true" : "false"}">
              ${m.label}
            </button>`
          ).join("")}
        </div>
      </div>
      <button type="button" class="level-reset">기본값으로 되돌리기</button>
    </div>
  `;

  const toggle = mount.querySelector(".level-toggle");
  const label = mount.querySelector(".level-toggle-label");
  const popover = mount.querySelector(".level-popover");

  /** 접힌 버튼이 곧 현재 설정 설명이다 — 위 헤더 주석 참고. */
  function refreshLabel() {
    const picked = [...selected].sort();
    const levelPart =
      picked.length === ALL
        ? ""
        : picked.length <= 2
          ? `난이도 ${picked.join(", ")}`
          : `난이도 ${picked.length}개`;
    const mode = UNREADABLE_MODES.find((m) => m.value === unreadableMode);

    if (unreadableMode === "only" || !levelPart) {
      // 판독 불가만 볼 때는 난이도 선택이 의미가 없다(교집합이 비어 있다).
      label.textContent = mode.desc;
    } else if (unreadableMode === "exclude") {
      label.textContent = `${levelPart} · 판독불가 제외`;
    } else {
      label.textContent = levelPart;
    }
    // "전체 난이도"(=아무것도 안 걸린 상태)에서만 강조를 뺀다.
    const neutral = picked.length === ALL && unreadableMode === "include";
    mount.classList.toggle("has-selection", !neutral);
  }

  function syncButtons() {
    const onlyMode = unreadableMode === "only";
    mount.querySelectorAll(".level-btn").forEach((b) => {
      b.setAttribute("aria-pressed", String(selected.has(Number(b.dataset.level))));
      // 판독 불가만 보는 동안에는 난이도로 좁힐 수 없다 — 눌러도 0건이 되므로
      // 잠가서 그 사실을 눈에 보이게 한다(판독 불가 선택 시 입력창을 잠그는
      // 것과 같은 접근이다).
      b.disabled = onlyMode;
    });
    mount.querySelectorAll(".level-mode").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.mode === unreadableMode));
    });
    mount.querySelector('[data-section="levels"]')?.classList.toggle("dimmed", onlyMode);
  }

  function setOpen(open) {
    popover.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
  }

  toggle.addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(popover.hidden);
  });
  // 팝오버 안을 클릭해도 닫히지 않아야 여러 개를 연달아 고를 수 있다.
  popover.addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => setOpen(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setOpen(false);
  });

  mount.querySelectorAll(".level-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const level = Number(btn.dataset.level);
      if (selected.has(level)) selected.delete(level);
      else selected.add(level);
      // 전부 꺼진 상태는 만들지 않는다 — 결과는 "전체"인데 화면은 아무것도
      // 안 골라진 것처럼 보여 가장 헷갈리는 조합이기 때문이다.
      if (!selected.size) DEFAULT_LEVELS.forEach((l) => selected.add(l));
      syncButtons();
      refreshLabel();
      onChange();
    });
  });

  mount.querySelectorAll(".level-mode").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (unreadableMode === btn.dataset.mode) return;
      unreadableMode = btn.dataset.mode;
      syncButtons();
      refreshLabel();
      onChange();
    });
  });

  function reset(notify = true) {
    selected.clear();
    DEFAULT_LEVELS.forEach((l) => selected.add(l));
    unreadableMode = DEFAULT_UNREADABLE;
    syncButtons();
    refreshLabel();
    if (notify) onChange();
  }

  mount.querySelector(".level-reset").addEventListener("click", () => reset());

  syncButtons();
  refreshLabel();
  return {
    // 1~4가 전부 켜져 있으면 빈 배열 = 난이도 필터 미적용. 나열해 보내면
    // 난이도 값이 없는 건(미처리 포함)이 조용히 빠지기 때문이다.
    levels: () => (selected.size === ALL ? [] : [...selected].sort()),
    unreadable: () => unreadableMode,
    reset: () => reset(false),
  };
}
