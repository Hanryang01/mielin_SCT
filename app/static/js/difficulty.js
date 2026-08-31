// OCR 난이도(1~5) 단일 정의 — 검수자/admin/필터 위젯이 모두 여기서 가져간다.
//
// 예전에는 review.js·admin.js·difficulty-filter.js가 같은 목록을 각자 들고
// 있어서 라벨 하나 고치려면 세 곳을 손봐야 했다(실제로 admin 쪽만 title이 빠져
// 있었다). 값이 어긋나면 같은 난이도가 화면마다 다르게 보이므로 한 곳으로 모은다.
//
// §5 — 검수자 화면의 분류(9종)를 대체한 값이다(08_add_review_difficulty_level.sql).
// 5(판독 불가)는 "읽었는데 얼마나 어려웠나"(1~4)와는 다른 종류의 판정이라
// 입력 버튼 그룹에서는 빼고(PICKABLE_DIFFICULTY_LEVELS) 별도 버튼으로 둔다.
// 다만 라벨 목록에는 남겨둔다 — 저장된 값을 표시할 때 필요하기 때문이다.
export const DIFFICULTY_LEVELS = [
  { level: 1, short: "매우 쉬움", title: "매우 쉬움 (정자체 및 완벽한 인식 수준)" },
  { level: 2, short: "쉬움", title: "쉬움 (일반적인 필기체)" },
  { level: 3, short: "보통", title: "보통 (주의 및 문맥 파악 필요)" },
  { level: 4, short: "어려움", title: "어려움 (심한 악필 및 복잡한 구조)" },
  { level: 5, short: "판독 불가", title: "판독 불가 (필기를 알아볼 수 없음)" },
];

/** 판독 불가 난이도. 서버(main.py의 UNREADABLE_DIFFICULTY_LEVEL)와 같은 값이어야 한다. */
export const UNREADABLE_LEVEL = 5;

/** 검수자가 그레이드로 고를 수 있는 범위 (판독 불가는 별도 버튼). */
export const PICKABLE_DIFFICULTY_LEVELS = DIFFICULTY_LEVELS.filter(
  (d) => d.level !== UNREADABLE_LEVEL
);

/** 저장된 난이도를 **표시용 문구**로 바꾼다 (2026-08-24).
 *
 *  예전에는 `3 (보통)`처럼 라벨을 함께 붙였는데 두 가지 이유로 숫자만 남겼다:
 *  ① 검수자는 매번 `[1 매우쉬움]…[4 어려움]` 버튼으로 입력하므로 숫자와 의미의
 *  매핑이 이미 몸에 익는다. ② "보통"은 3에서 파생된 이름일 뿐 추가 정보가 없어,
 *  목록 한 행에 여러 항목이 `·`로 이어질 때 길이만 두 배가 됐다.
 *
 *  **5는 예외로 라벨만 쓴다.** 5는 "가장 어려움"이 아니라 "읽지 못했다"는 종류가
 *  다른 판정이라(§5.1), `난이도 5`로 적으면 최상급 난이도로 오해된다 — 실제로
 *  그 혼동이 반복돼 난이도 필터에서 5를 빼낸 적이 있다. 그래서 "난이도" 접두어
 *  자체를 붙이지 않는다.
 *
 *  라벨이 필요한 곳은 **고르는 UI**(입력 버튼·필터 팝오버·관리자 모달)이고,
 *  거기서는 DIFFICULTY_LEVELS의 short를 직접 쓴다 — 그 노출이 ①의 학습 효과를
 *  계속 만들어주므로 표시에서 빼도 안전하다.
 */
export function describeDifficulty(level) {
  if (level === UNREADABLE_LEVEL) return "판독 불가";
  return level ? `난이도 ${level}` : "";
}
