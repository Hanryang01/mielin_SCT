// 로그인한 계정 표시 + 로그아웃. 모든 화면(app.js/review.js/admin.js)에서 같이 로드한다.
//
// admin 계정은 "/"에서 곧장 /admin으로 리다이렉트되므로(main.py의 index())
// 화면 간 이동 링크가 필요 없다 — 실제 접근 차단은 서버가 한다
// (main.py의 require_admin — 403).
async function initNavAuth() {
  const nav = document.querySelector(".app-nav");
  if (!nav) return;

  const res = await fetch("/api/auth/me");
  if (!res.ok) {
    location.href = "/login";
    return;
  }
  const me = await res.json();

  const userEl = document.createElement("span");
  userEl.className = "nav-user";
  userEl.textContent = me.role === "admin" ? `${me.name} (admin)` : me.name;

  // 화면(admin.js/review.js)이 각자의 init()에서 window.__refreshCurrentView를
  // 채워 넣는다 — 필터/검색창의 옛 "조회" 버튼을 대체해 상단에서 한 번에 누르게
  // 한다(2026-08-31, 중복 방지를 위해 "조회" 버튼은 없앴다). 아직 안 채워졌으면
  // (초기 로딩 중) 조용히 무시한다.
  const refreshBtn = document.createElement("button");
  refreshBtn.type = "button";
  refreshBtn.className = "nav-refresh";
  refreshBtn.textContent = "↻ 새로고침";
  refreshBtn.addEventListener("click", () => {
    window.__refreshCurrentView && window.__refreshCurrentView();
  });

  const logoutBtn = document.createElement("button");
  logoutBtn.type = "button";
  logoutBtn.className = "nav-logout";
  logoutBtn.textContent = "로그아웃";
  logoutBtn.addEventListener("click", async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    location.href = "/login";
  });

  nav.append(userEl, refreshBtn, logoutBtn);
}

initNavAuth();
