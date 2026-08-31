// 로그인 후 되돌아갈 경로는 사이트 내부 절대경로만 허용한다 (오픈 리다이렉트 방지).
// "//evil.com"은 브라우저가 프로토콜 상대 URL로 읽어 외부로 나가므로 같이 막는다.
// 서버(app/auth.py safe_next_path)에도 같은 검증이 있다 — 여기 통과해도 서버가 다시 막는다.
function safeNext(raw) {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

const form = document.getElementById("loginForm");
const errorEl = document.getElementById("loginError");
const submitBtn = form.querySelector(".login-submit");

function showError(message) {
  errorEl.textContent = message;
  errorEl.hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorEl.hidden = true;
  submitBtn.disabled = true;

  const username = form.username.value.trim();
  const password = form.password.value;

  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      showError(data.detail || "로그인에 실패했습니다");
      return;
    }
    location.href = safeNext(new URLSearchParams(location.search).get("next"));
  } catch {
    showError("서버에 연결할 수 없습니다");
  } finally {
    submitBtn.disabled = false;
  }
});
