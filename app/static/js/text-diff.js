// OCR 텍스트와 입력 중인 텍스트의 글자 단위 차이 계산 (OCR 검수 시나리오.md §5.3).
//
// 이건 **입력 중 실시간 미리보기 전용**이다. DB에 저장되는 diff는 항상 서버가
// 계산한다(app/text_diff.py) — 화면이 보낸 값을 저장하면 화면 버그나 조작으로
// "차이 없음"이 쌓여도 알아챌 수 없고, 이 값은 §6 분석의 근거 데이터라
// 단일 소스여야 한다.
//
// 서버는 파이썬 difflib(SequenceMatcher)를, 여기서는 표준 LCS를 쓴다. 둘 다
// 최소 편집 거리 기준으로는 올바르지만, 차이 구간을 어디서 끊을지가 드물게
// 다를 수 있다 (예: 반복되는 글자 주변). 미리보기와 저장값이 한두 글자
// 경계에서 달라 보일 수 있다는 뜻인데, 저장되는 쪽이 서버라 데이터
// 정합성에는 영향이 없다.

// 서버(MAX_DIFF_LENGTH)와 같은 상한. 넘으면 통째로 "전체가 다름"으로 처리한다.
const MAX_DIFF_LENGTH = 2000;

// DP 표 크기 상한. 이 미리보기는 입력할 때마다 다시 계산되므로, 길이 상한만
// 두면 최악의 경우(2000×2000) 키 입력마다 16MB를 할당하게 된다. SCT 답변은
// 한두 문장이라 실제로는 걸릴 일이 없고, 걸리면 "전체가 다름"으로 떨어진다.
const MAX_DP_CELLS = 250000;

/**
 * 글자 단위 LCS로 차이 구간을 구한다.
 * @returns {{segments: Array, charCount: number}}
 *   segments의 op는 replace / delete(OCR에만 있음) / insert(OCR이 누락).
 */
export function computeDiff(ocrText, typedText) {
  const ocr = ocrText || "";
  const typed = typedText || "";

  if (ocr === typed) return { segments: [], charCount: 0 };

  if (tooLargeToDiff(ocr, typed)) {
    return {
      segments: [{ op: "replace", ocr, typed, ocrPos: 0, typedPos: 0 }],
      charCount: Math.max(ocr.length, typed.length),
    };
  }

  const ops = lcsOpcodes(ocr, typed);
  const segments = [];
  let charCount = 0;

  for (const [op, i1, i2, j1, j2] of ops) {
    if (op === "equal") continue;
    segments.push({
      op,
      ocr: ocr.slice(i1, i2),
      typed: typed.slice(j1, j2),
      ocrPos: i1,
      typedPos: j1,
    });
    charCount += Math.max(i2 - i1, j2 - j1);
  }

  return { segments, charCount };
}

/**
 * 차이를 표시한 HTML을 만든다. 검수자가 제시한 대괄호 표기(§5.3)를
 * 시각적으로 보여주되, 삭제/삽입/오인식을 색으로 구분한다.
 *
 * 호출부는 이 결과를 innerHTML로 넣으므로, 여기서 모든 텍스트를 이스케이프한다.
 */
export function renderDiffHtml(ocrText, typedText, escapeHtml) {
  const ocr = ocrText || "";
  const typed = typedText || "";
  if (!typed) return "";
  if (ocr === typed) {
    return `<span class="diff-same">${escapeHtml(typed)} · OCR과 동일</span>`;
  }
  if (tooLargeToDiff(ocr, typed)) {
    return `<mark class="diff-replace">[${escapeHtml(typed)}]</mark>`;
  }

  const ops = lcsOpcodes(ocr, typed);
  const parts = [];
  for (const [op, i1, i2, j1, j2] of ops) {
    if (op === "equal") {
      parts.push(escapeHtml(typed.slice(j1, j2)));
      continue;
    }
    // delete는 타이핑 쪽에 글자가 없으므로 OCR에만 있던 글자를 보여준다 —
    // 그래야 "사람[대] 매너가..."처럼 무엇이 빠졌는지 읽을 수 있다.
    const shown = op === "delete" ? ocr.slice(i1, i2) : typed.slice(j1, j2);
    parts.push(`<mark class="diff-${op}">[${escapeHtml(shown)}]</mark>`);
  }
  return parts.join("");
}

function tooLargeToDiff(a, b) {
  return (
    a.length > MAX_DIFF_LENGTH ||
    b.length > MAX_DIFF_LENGTH ||
    (a.length + 1) * (b.length + 1) > MAX_DP_CELLS
  );
}

/**
 * 표준 LCS 기반 opcode 생성 — difflib.get_opcodes()와 같은 모양
 * ([op, i1, i2, j1, j2] 배열)으로 돌려준다.
 *
 * 짧은 SCT 답변만 다루므로 O(n*m) DP로 충분하다 (호출 전에 tooLargeToDiff로
 * 표 크기를 걸러낸다).
 */
function lcsOpcodes(a, b) {
  const n = a.length;
  const m = b.length;

  // dp[i][j] = a[i..], b[j..]의 최장 공통 부분수열 길이
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] =
        a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const raw = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      raw.push(["equal", i, i + 1, j, j + 1]);
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      raw.push(["delete", i, i + 1, j, j]);
      i += 1;
    } else {
      raw.push(["insert", i, i, j, j + 1]);
      j += 1;
    }
  }
  while (i < n) {
    raw.push(["delete", i, i + 1, j, j]);
    i += 1;
  }
  while (j < m) {
    raw.push(["insert", i, i, j, j + 1]);
    j += 1;
  }

  return coalesce(raw);
}

/** 인접한 같은 종류를 합치고, 붙어 있는 delete+insert는 replace로 묶는다.
 *  글자마다 따로 표시하면 "사[람][랑]"처럼 읽기 어려워진다. */
function coalesce(raw) {
  const merged = [];
  for (const cur of raw) {
    const prev = merged[merged.length - 1];
    if (prev && prev[0] === cur[0]) {
      prev[2] = cur[2];
      prev[4] = cur[4];
      continue;
    }
    merged.push([...cur]);
  }

  const out = [];
  for (const cur of merged) {
    const prev = out[out.length - 1];
    if (prev && prev[0] === "delete" && cur[0] === "insert") {
      // i 범위는 delete 쪽, j 범위는 insert 쪽에서 가져온다.
      out[out.length - 1] = ["replace", prev[1], prev[2], cur[3], cur[4]];
      continue;
    }
    if (prev && prev[0] === "insert" && cur[0] === "delete") {
      out[out.length - 1] = ["replace", cur[1], cur[2], prev[3], prev[4]];
      continue;
    }
    out.push(cur);
  }
  return out;
}
