/* LeetKit Manager 대시보드 로직. window.pywebview.api.* 가 유일한 백엔드 접점이다 —
   여기서 subprocess나 uv를 직접 다루지 않는다(그건 Python orchestrator의 몫). */

const TARGET_LABEL = { "claude-desktop": "Claude Desktop", "claude-code": "Claude Code", "codex": "Codex CLI" };

// 카드는 항상 고정 높이를 유지한다(문제를 펼쳐도 안 늘어남) — 상세 데이터는 모달에서만
// 보여주므로, 마지막으로 받은 각 Lens 데이터를 여기 캐싱해서 모달을 다시 fetch 없이 연다.
const lensDataCache = {};

function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// innerHTML로 들어가는 모든 Lens 유래 문자열용. 실제로 재현되던 표시 버그를 고친다:
// DartLens의 조치 안내 `dartlens-setup --plaintext <YOUR_DART_API_KEY>`가 HTML 파서에
// 알 수 없는 태그로 먹혀서 화면엔 `dartlens-setup --plaintext `까지만 보였다(복사되는
// 값은 멀쩡해서 "보이는 것과 복사되는 것이 다른" 상태였다). doctor가 예외 원문을
// summary/details에 그대로 담는 경로도 있어서, 값 전체를 이스케이프한다.
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function statusClass(readiness) {
  if (readiness === "정상") return "ok";
  if (readiness === "주의") return "warn";
  if (readiness === "조치 필요" || readiness === "호환되지 않는 Lens 버전") return "fail";
  return "neutral";
}

function updateLabel(updateAvailable) {
  if (updateAvailable === true) return "업데이트 가능";
  if (updateAvailable === false) return "최신";
  return "확인 필요";
}

function licenseLabel(status) {
  return { active: "활성", missing: "없음", invalid: "유효하지 않음" }[status] || "확인 필요";
}

function formatCheckedAt(iso) {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch {
    return iso;
  }
}

function focusRingSvg(cls) {
  const colorVar = { ok: "var(--ok)", warn: "var(--warn)", fail: "var(--fail)", neutral: "var(--neutral)" }[cls];
  const dotOpacity = cls === "ok" ? 1 : 0;
  const arcDash = { ok: "88 0", warn: "60 28", fail: "30 58", neutral: "0 88" }[cls];
  return `
    <svg class="focus-ring snap" viewBox="0 0 34 34">
      <circle class="track" cx="17" cy="17" r="14"></circle>
      <circle class="arc" cx="17" cy="17" r="14" stroke="${colorVar}"
        stroke-dasharray="${arcDash}" pathLength="88"></circle>
      <circle class="dot" cx="17" cy="17" r="4" fill="${colorVar}" opacity="${dotOpacity}"></circle>
    </svg>`;
}

function renderReadout(summary) {
  const dots = document.getElementById("readout-dots");
  dots.innerHTML = "";
  const total = summary.total;
  const colors = [];
  for (let i = 0; i < summary.ok; i++) colors.push("ok");
  for (let i = 0; i < summary.action_needed; i++) colors.push("fail");
  while (colors.length < total) colors.push("warn");
  colors.forEach((c) => {
    const dot = document.createElement("span");
    dot.className = `readout-dot ${c}`;
    dots.appendChild(dot);
  });

  let text = `${summary.total}개 중 ${summary.ok}개 정상`;
  if (summary.update_available) text += ` · 업데이트 ${summary.update_available}개`;
  if (summary.action_needed) text += ` · 조치 필요 ${summary.action_needed}개`;
  document.getElementById("readout-text").textContent = text;
}

function renderCard(lens) {
  lensDataCache[lens.name] = lens;

  const cls = statusClass(lens.readiness);
  const targets = lens.targets.length
    ? lens.targets.map((t) => TARGET_LABEL[t] || escapeHtml(t)).join(" · ")
    : "미등록";
  const problems = lens.checks.filter((c) => !["ok", "active", "skip", "info-skip"].includes(c.status));
  const inProgress = lens.checks.filter((c) => c.status === "active");

  const repairBtn = lens.repairable_repair_id
    ? `<button class="action-btn warn" data-action="repair" data-lens="${lens.name}" data-repair-id="${lens.repairable_repair_id}">복구</button>`
    : "";
  // 설치가 깨진 상태(incompatible)에서는 예전엔 설치·업데이트 버튼이 둘 다 안 떴다 —
  // not_installed는 false(명령은 실행됨)인데 installed_version을 못 읽어
  // update_available이 null이라 두 조건 모두 빗나갔다. 그 결과 사용자가 카드에서
  // 할 수 있는 게 삭제뿐인 막다른 길이 됐다("업데이트도 안 된다"의 정체).
  // 이 상태야말로 재설치가 정확한 조치라 버튼을 확실히 띄운다.
  const installOrUpdateBtn = lens.not_installed
    ? `<button class="action-btn primary" data-action="install" data-lens="${lens.name}">설치</button>`
    : lens.incompatible
    ? `<button class="action-btn primary" data-action="install" data-lens="${lens.name}">재설치</button>`
    : lens.update_available
    ? `<button class="action-btn primary" data-action="install" data-lens="${lens.name}">업데이트</button>`
    : "";
  // 텔레그램 로그인은 TelegramLens 고유 흐름(전화번호·SMS 코드 등 여러 단계 대화가
  // 필요) — 다른 Lens에는 해당 개념 자체가 없어서 이 카드에서만 보여준다.
  const telegramLoginBtn =
    lens.name === "telegramlens"
      ? `<button class="action-btn" data-action="telegram-login" data-lens="${lens.name}">텔레그램 로그인</button>`
      : "";
  // 업데이트로도 안 풀리는 "호환되지 않는 버전"(예: PATH에 uv 관리 밖의 낡은 실행 파일이
  // 남아있는 경우) 대응 — 완전히 지우고 새로 설치할 수 있게. 이미 설치된 것만 지울 게
  // 있으므로 미설치 상태에선 안 보여준다.
  const uninstallBtn = lens.not_installed
    ? ""
    : `<button class="action-btn danger" data-action="uninstall" data-lens="${lens.name}">삭제</button>`;

  return `
    <div class="card" data-card="${lens.name}">
      <div class="card-head">
        ${focusRingSvg(cls)}
        <div class="card-title">
          <span class="card-name">${escapeHtml(lens.display_name)}</span>
          <span class="card-version">${lens.installed_version ? "v" + escapeHtml(lens.installed_version) : "미설치"}</span>
        </div>
        <span class="card-readiness ${cls}">${escapeHtml(lens.readiness)}</span>
      </div>
      <div class="field-list">
        <div class="field-row"><span class="field-label">업데이트</span><span class="field-value">${updateLabel(lens.update_available)}</span></div>
        <div class="field-row"><span class="field-label">라이선스</span><span class="field-value emph">${licenseLabel(lens.license_status)}${lens.license_id_masked ? " · " + escapeHtml(lens.license_id_masked) : ""}</span></div>
        <div class="field-row"><span class="field-label">MCP 등록</span><span class="field-value">${targets}</span></div>
        <div class="field-row"><span class="field-label">최근 진단</span><span class="field-value">${formatCheckedAt(lens.checked_at)}</span></div>
      </div>
      ${lens.problem_detail ? `
      <div class="problem-detail">${escapeHtml(lens.problem_detail)}</div>` : ""}
      ${problems.length ? `
      <div class="check-toggle" data-action="open-detail" data-lens="${lens.name}">
        <span class="chevron">▸</span>문제 ${problems.length}건 — 자세히
      </div>` : ""}
      ${inProgress.length ? `
      <div class="check-toggle progress" data-action="open-detail" data-lens="${lens.name}">
        <span class="chevron">▸</span>진행중 ${inProgress.length}건 — 자세히
      </div>` : ""}
      <div class="actions">
        <button class="action-btn" data-action="diagnose" data-lens="${lens.name}">진단</button>
        <button class="action-btn" data-action="register" data-lens="${lens.name}">MCP 등록</button>
        <button class="action-btn" data-action="activate" data-lens="${lens.name}">활성화</button>
        ${telegramLoginBtn}
        ${repairBtn}
        ${installOrUpdateBtn}
        ${uninstallBtn}
      </div>
    </div>`;
}

function render(data) {
  renderReadout(data.summary);
  document.getElementById("grid").innerHTML = data.lenses.map(renderCard).join("");
}

/* ---------- 상세 모달 — 카드는 절대 안 늘어나고, 자세한 내용은 여기서만 스크롤 ---------- */

let detailModalLens = null;

// 가이드 데모용 예시 데이터 — 실제 시스템 상태와 무관하게 항상 "문제 1건 + 조치 명령"을
// 보여줘서, 지금 마침 문제가 없는 Lens라도 조치 클릭-복사 기능을 확실히 가르쳐준다.
const EXAMPLE_LENS_DATA = {
  name: "__example__",
  display_name: "StockLens (예시)",
  installed_version: "0.5.3",
  update_available: false,
  license_status: "active",
  license_id_masked: "****A91F",
  targets: ["claude-desktop"],
  checked_at: new Date().toISOString(),
  checks: [
    {
      id: "MCP_CONFIG_VALID",
      status: "fail",
      summary: "Claude Code에 아직 등록돼 있지 않습니다.",
      details: { lines: ["Claude Desktop: 등록됨", "Claude Code: 미등록"] },
      action: "stocklens-setup --target claude-code",
    },
  ],
};

// 세 Lens의 doctor.py가 쓰는 내부 진단 식별자(전부 합집합) → 고객이 읽을 라벨.
// 원문 메시지(c.summary/details/action)는 이미 각 Lens가 한국어로 주는 그대로 두고,
// 이 식별자 하나만 고객 언어로 바꾼다 — "결과 복사"(진단 텍스트, 나에게 문의로 오는
// 용도)는 원래 식별자를 그대로 유지한다(내가 원인 찾을 때는 이게 더 유용함).
const CHECK_ID_LABEL = {
  UV_AVAILABLE: "실행 환경(uv)",
  PACKAGE_IMPORTABLE: "패키지 설치 상태",
  COMMAND_AVAILABLE: "실행 명령어",
  PYTHON_SUPPORTED: "Python 버전",
  CACHE_WRITABLE: "캐시 폴더",
  MCP_CONFIG_DESKTOP: "Claude Desktop 등록",
  MCP_CONFIG_CODE: "Claude Code 등록",
  MCP_CONFIG_VALID: "MCP 등록 상태",
  LICENSE_ACTIVE: "라이선스",
  DART_API_KEY: "DART API 키",
  CORP_CODE_CACHE: "기업코드 캐시",
  TELEGRAM_LOGIN: "텔레그램 로그인",
  DAEMON_COLLECTOR: "수집 데몬",
  DATA_SQLITE: "데이터베이스",
  BACKFILL: "과거 데이터 백필",
  KR_DATA_REACHABLE: "한국 시세 연결",
  US_DATA_REACHABLE: "미국 시세 연결",
  UPDATE_CHECK_REACHABLE: "업데이트 확인",
};

function renderCheckItem(c) {
  const detailLines = (c.details && c.details.lines) || [];
  const linesHtml = detailLines.length
    ? `<ul class="check-detail-lines">${detailLines.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ul>`
    : "";
  const actionHtml = c.action
    ? `<div class="check-action"><span class="check-action-label">조치</span><span class="check-action-cmd" data-action="copy-cmd" data-cmd="${escapeAttr(c.action)}" title="눌러서 복사">${escapeHtml(c.action)}</span></div>`
    : "";
  const cls = c.status === "active" ? "check-item active" : "check-item";
  const label = CHECK_ID_LABEL[c.id] || escapeHtml(c.id);
  return `<div class="${cls}"><span class="check-id">${label}</span>${escapeHtml(c.summary)}${linesHtml}${actionHtml}</div>`;
}

function renderDetailModal(lens) {
  const targets = lens.targets.length
    ? lens.targets.map((t) => TARGET_LABEL[t] || escapeHtml(t)).join(" · ")
    : "미등록";
  const problems = lens.checks.filter((c) => !["ok", "active", "skip", "info-skip"].includes(c.status));
  const inProgress = lens.checks.filter((c) => c.status === "active");

  // "진행중"은 문제가 아니므로 별도 섹션으로 — 둘 다 있을 때만 "문제" 소제목을 붙여
  // 구분하고, 하나만 있으면 굳이 소제목으로 나누지 않는다.
  const progressHtml = inProgress.length
    ? `<div class="detail-progress"><div class="detail-section-label">진행중</div>${inProgress.map(renderCheckItem).join("")}</div>`
    : "";
  const problemsHtml = problems.length
    ? `${inProgress.length ? `<div class="detail-section-label">문제</div>` : ""}${problems.map(renderCheckItem).join("")}`
    : `<div class="check-item">문제 없음</div>`;

  document.getElementById("detail-title").textContent = `${lens.display_name} 상세`;
  document.getElementById("detail-body").innerHTML = `
    <div class="field-list">
      <div class="field-row"><span class="field-label">버전</span><span class="field-value emph">${lens.installed_version ? "v" + lens.installed_version : "미설치"}</span></div>
      <div class="field-row"><span class="field-label">업데이트</span><span class="field-value emph">${updateLabel(lens.update_available)}</span></div>
      <div class="field-row"><span class="field-label">라이선스</span><span class="field-value emph">${licenseLabel(lens.license_status)}${lens.license_id_masked ? " · " + lens.license_id_masked : ""}</span></div>
      <div class="field-row"><span class="field-label">MCP 등록</span><span class="field-value emph">${targets}</span></div>
      <div class="field-row"><span class="field-label">최근 진단</span><span class="field-value emph">${formatCheckedAt(lens.checked_at)}</span></div>
    </div>
    ${progressHtml}
    <div class="detail-problems">${problemsHtml}</div>
  `;
  document.getElementById("detail-backdrop").hidden = false;
}

function openDetailModal(lensName) {
  const lens = lensDataCache[lensName];
  if (!lens) return;
  detailModalLens = lensName;
  renderDetailModal(lens);
}

function openDetailModalExample() {
  detailModalLens = null; // 예시 모달의 "결과 복사"는 실제 Lens가 아니므로 동작 안 함
  renderDetailModal(EXAMPLE_LENS_DATA);
  // 가이드 데모 중엔 모달을 좌상단으로 — 가이드 설명(우하단)과 자리를 나눠 쓴다.
  document.getElementById("detail-backdrop").classList.add("demo-position");
}

function closeDetailModal() {
  document.getElementById("detail-backdrop").hidden = true;
  document.getElementById("detail-backdrop").classList.remove("demo-position");
}

document.getElementById("detail-close").addEventListener("click", closeDetailModal);
document.getElementById("detail-copy").addEventListener("click", (e) => {
  if (detailModalLens) copyDiagnosticText(detailModalLens, e.currentTarget);
});

async function loadDiagnosis() {
  const btn = document.getElementById("refresh-btn");
  btn.disabled = true;
  document.getElementById("readout-text").textContent = "진단 중…";
  try {
    const data = await window.pywebview.api.diagnose(false);
    render(data);
  } finally {
    btn.disabled = false;
  }
}

// 같은 Lens에 대해 이미 실행 중인 작업 — 설치/등록/삭제는 전부 같은 설정 파일과
// 같은 uv 도구 디렉터리를 건드리므로, 버튼을 두 번 눌러 subprocess 두 개가 동시에
// 돌면 설정 JSON이 깨질 수 있다. 카드를 흐리게만 하고 버튼은 계속 눌리는 상태였다.
const runningActions = new Set();

// 성공 후 다음 화면으로 넘어가기까지의 짧은 대기 동안 입력칸·버튼이 그대로 살아 있어서
// 그 틈에 조작이 됐다(입력칸은 비었는데 확인이 또 눌리는 식). 대기 구간에는 모달 안을
// 통째로 잠근다.
function setModalInteractive(backdropId, enabled) {
  const backdrop = document.getElementById(backdropId);
  if (!backdrop) return;
  backdrop.querySelectorAll("input, button, select, textarea").forEach((el) => {
    el.disabled = !enabled;
  });
}

// Claude Desktop이 Lens를 MCP 서버로 띄워두면 그 프로세스가 uv 도구 폴더의 파일을
// 잡아서 설치·삭제가 "액세스 거부"로 실패한다(실사용에서 확인). 사용자에게 "Claude를
// 닫고 다시 해보세요"라고 떠넘기는 대신, 닫고 → 재시도 → 다시 켜기까지 대신 해준다.
async function offerCloseClaudeAndRetry(lensName, action) {
  const displayName = (lensDataCache[lensName] || {}).display_name || lensName;
  const label = action === "uninstall" ? "삭제" : "설치";
  const ok = confirm(
    `Claude Desktop이 ${displayName} 파일을 사용 중이라 ${label}할 수 없습니다.\n\n` +
      `Claude Desktop을 잠시 껐다가 ${label}를 진행하고, 끝나면 다시 켤까요?`
  );
  if (!ok) {
    showToast(`Claude Desktop을 완전히 종료한 뒤 다시 ${label}해주세요.`);
    return;
  }

  showBusyOverlay("Claude Desktop을 종료하는 중…");
  try {
    const quit = await window.pywebview.api.quit_claude_desktop();
    if (!quit.ok) {
      showToast(quit.error || "Claude Desktop을 종료하지 못했습니다.");
      return;
    }
    updateBusyOverlay(`${displayName}를 ${label}하는 중…`);
    const retry =
      action === "uninstall"
        ? await window.pywebview.api.uninstall(lensName)
        : await window.pywebview.api.install_or_update(lensName);
    const lens = await window.pywebview.api.diagnose_one(lensName, false);
    replaceCard(lensName, lens);

    updateBusyOverlay("Claude Desktop을 다시 켜는 중…");
    await window.pywebview.api.launch_claude_desktop();
    showToast(
      retry.ok
        ? `${label} 완료 — Claude Desktop도 다시 켰습니다.`
        : retry.error || `${label}에 실패했습니다.`
    );
  } catch {
    showToast("처리 중 오류가 발생했습니다. 다시 시도해주세요.");
  } finally {
    hideBusyOverlay();
  }
}

// 오래 걸리는 작업 중에는 화면 전체를 덮어 뒤쪽 버튼을 아예 못 누르게 한다 —
// 기다리는 동안 "되는 건가?" 하며 다른 걸 눌러 상태가 꼬이는 걸 막는다.
function showBusyOverlay(text) {
  document.getElementById("busy-text").textContent = text;
  document.getElementById("busy-overlay").hidden = false;
}

function updateBusyOverlay(text) {
  const el = document.getElementById("busy-text");
  if (!document.getElementById("busy-overlay").hidden) el.textContent = text;
}

function hideBusyOverlay() {
  document.getElementById("busy-overlay").hidden = true;
}

// 설치는 pandas·numpy까지 받느라 실제로 수십 초 걸린다. 그동안 화면이 멈춘 것처럼
// 보이면 사용자는 불안해하고 창을 닫아버린다 — 백엔드가 담아두는 현재 단계를 짧은
// 주기로 읽어와 계속 갱신해준다. 반환값은 이 폴링을 멈추는 함수.
function startInstallProgressPolling(render) {
  let stopped = false;
  const tick = async () => {
    if (stopped) return;
    try {
      const text = await window.pywebview.api.install_progress();
      if (!stopped && text) render(text);
    } catch {
      /* 진행 표시 실패가 설치를 방해하면 안 된다 */
    }
    if (!stopped) setTimeout(tick, 500);
  };
  setTimeout(tick, 300);
  return () => {
    stopped = true;
  };
}

async function runAction(action, lensName, extra) {
  if (runningActions.has(lensName)) {
    showToast("이미 처리 중입니다 — 잠시만 기다려주세요.");
    return;
  }
  runningActions.add(lensName);

  const grid = document.getElementById("grid");
  const card = grid.querySelector(`[data-card="${lensName}"]`);
  if (card) {
    card.style.opacity = "0.55";
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
  }
  try {
    if (action === "diagnose") {
      const lens = await window.pywebview.api.diagnose_one(lensName, false);
      replaceCard(lensName, lens);
    } else if (action === "repair") {
      const result = await window.pywebview.api.repair(lensName, extra);
      const lens = await window.pywebview.api.diagnose_one(lensName, false);
      replaceCard(lensName, lens);
      if (!result.ok) {
        showToast(result.error || "복구에 실패했습니다.");
      } else {
        const actions = (result.result && result.result.actions) || [];
        const failed = actions.find((a) => a.status === "failed");
        if (failed) showToast(failed.detail || "일부 복구 작업이 실패했습니다.");
      }
    } else if (action === "install") {
      const displayName = (lensDataCache[lensName] || {}).display_name || lensName;
      showBusyOverlay(`${displayName}를 설치하는 중…`);
      const stopPolling = startInstallProgressPolling((text) => updateBusyOverlay(`${displayName} · ${text}`));
      let result, lens;
      try {
        result = await window.pywebview.api.install_or_update(lensName);
        lens = await window.pywebview.api.diagnose_one(lensName, false);
      } finally {
        stopPolling();
        hideBusyOverlay();
      }
      replaceCard(lensName, lens);
      if (!result.ok) {
        if (result.claude_blocking) {
          await offerCloseClaudeAndRetry(lensName, "install");
        } else {
          showToast(
            result.rollback_command
              ? `설치/업데이트에 실패했습니다 — 이전 버전 복구 명령: ${result.rollback_command}`
              : "설치/업데이트에 실패했습니다."
          );
        }
      }
    } else if (action === "uninstall") {
      const displayName = (lensDataCache[lensName] || {}).display_name || lensName;
      showBusyOverlay(`${displayName}를 삭제하는 중…`);
      let result, lens;
      try {
        result = await window.pywebview.api.uninstall(lensName);
        lens = await window.pywebview.api.diagnose_one(lensName, false);
      } finally {
        hideBusyOverlay();
      }
      replaceCard(lensName, lens);
      if (result.ok) {
        showToast("삭제되었습니다 — 다시 설치할 수 있습니다.");
      } else if (result.claude_blocking) {
        await offerCloseClaudeAndRetry(lensName, "uninstall");
      } else {
        showToast(result.error || "삭제에 실패했습니다.");
      }
    }
  } finally {
    hideBusyOverlay(); // 어떤 경로로 빠져나가도 화면이 덮인 채 남지 않게
    runningActions.delete(lensName);
    // 카드가 replaceCard로 갈렸으면 위에서 잡아둔 참조는 이미 DOM 밖이다 — 지금 붙어
    // 있는 카드를 다시 찾아서 되돌린다(안 그러면 새 카드가 흐린 채로 남는다).
    const current = grid.querySelector(`[data-card="${lensName}"]`);
    if (current) {
      current.style.opacity = "1";
      current.querySelectorAll("button").forEach((b) => (b.disabled = false));
    }
  }
}

function replaceCard(lensName, lensData) {
  const grid = document.getElementById("grid");
  const card = grid.querySelector(`[data-card="${lensName}"]`);
  if (card) card.outerHTML = renderCard(lensData);
  recomputeSummaryFromCache();
}

// 복구/설치/개별 진단 후에는 지금까지 card 하나만 새로 그리고 상단 "N개 중 M개 정상"
// 요약 바는 "진단 재실행"을 눌러야만 갱신되던 문제 — renderCard가 이미 lensDataCache를
// 최신으로 갱신해두므로(정확히는 replaceCard 호출 시점에 최신), 서버의
// orchestrator.summarize()와 동일한 계산을 클라이언트에서 캐시로 다시 해서 매번
// 새로고침 버튼을 누르지 않아도 즉시 반영되게 한다.
// orchestrator.has_actionable_problem()과 같은 규칙 — 각 Lens의 `overall` 계산 방식이
// 서로 달라서(StockLens만 critical 개념을 써서 일부 실패를 degraded로 낮춘다)
// overall만 보면 실제 실패가 "조치 필요"에서 빠진다.
function lensHasActionableProblem(lens) {
  if (lens.not_installed || lens.incompatible) return true;
  if (lens.overall === "fail") return true;
  return (lens.checks || []).some((c) => c.status === "fail");
}

function recomputeSummaryFromCache() {
  const lenses = Object.values(lensDataCache);
  if (!lenses.length) return;
  renderReadout({
    total: lenses.length,
    ok: lenses.filter((l) => l.overall === "ok" && !l.incompatible).length,
    update_available: lenses.filter((l) => l.update_available).length,
    action_needed: lenses.filter(lensHasActionableProblem).length,
  });
}

/* ---------- 활성화 모달 ---------- */

let activateTargetLens = null;

function openActivateModal(lensName) {
  activateTargetLens = lensName;
  const lens = lensDataCache[lensName];
  const displayName = lens ? lens.display_name : lensName;
  document.getElementById("modal-title").textContent = `${displayName} 라이선스 활성화`;
  document.getElementById("modal-key-input").value = "";
  document.getElementById("modal-apikey-input").value = "";
  document.getElementById("modal-backdrop").hidden = false;
  // 직전 성공 대기 중 잠갔던 걸 반드시 풀고 시작한다(안 풀면 다음에 열었을 때 먹통).
  setModalInteractive("modal-backdrop", true);
  renderActivateModalState(lens, displayName);

  // DartLens는 API 키 발급 페이지를 열어준다(텔레그램 로그인의 need_credentials와
  // 같은 정신) — 모달을 열 때 한 번만, 재렌더(설치 완료 등)마다 다시 열지 않는다.
  const needsApiKey = !!(lens && lens.extra_credentials && lens.extra_credentials.includes("dart_api"));
  if (needsApiKey && lens && !lens.not_installed) {
    window.pywebview.api.open_dart_api_signup();
  }
}

// 활성화 모달 안에서 설치/업데이트까지 한 번에 — 처음 구매한 사람이 "먼저 설치하고,
// 창 찾아서 활성화 누르고" 순서를 몰라도 헤매지 않게 이 모달 하나로 끝나게 한다.
function renderActivateModalState(lens, displayName) {
  const msgEl = document.getElementById("modal-msg");
  const keyInput = document.getElementById("modal-key-input");
  const confirmBtn = document.getElementById("modal-confirm");
  const installBtn = document.getElementById("modal-install-btn");
  const apiKeyField = document.getElementById("modal-apikey-field");
  const apiKeyInput = document.getElementById("modal-apikey-input");

  // DartLens처럼 라이선스 키 말고 추가 자격증명(DART API 키)이 필요한 Lens에서만
  // 두 번째 입력칸을 보여준다 — StockLens/TelegramLens는 지금처럼 라이선스 키 하나뿐.
  const needsApiKey = !!(lens && lens.extra_credentials && lens.extra_credentials.includes("dart_api"));
  apiKeyField.hidden = !needsApiKey;

  if (lens && lens.not_installed) {
    msgEl.textContent = `${displayName}가 아직 설치되지 않았습니다. 먼저 설치해야 활성화할 수 있어요.`;
    msgEl.className = "modal-msg fail";
    keyInput.disabled = true;
    apiKeyInput.disabled = true;
    confirmBtn.hidden = true;
    installBtn.hidden = false;
    installBtn.disabled = false;
    installBtn.textContent = "설치";
    installBtn.dataset.mode = "install";
  } else if (lens && lens.update_available) {
    msgEl.textContent = "새 버전이 있습니다. 업데이트하거나, 지금 버전으로 바로 활성화해도 됩니다.";
    msgEl.className = "modal-msg";
    keyInput.disabled = false;
    apiKeyInput.disabled = false;
    confirmBtn.hidden = false;
    installBtn.hidden = false;
    installBtn.disabled = false;
    installBtn.textContent = "업데이트";
    installBtn.dataset.mode = "update";
    keyInput.focus();
  } else {
    msgEl.textContent = "";
    msgEl.className = "modal-msg";
    keyInput.disabled = false;
    apiKeyInput.disabled = false;
    confirmBtn.hidden = false;
    installBtn.hidden = true;
    keyInput.focus();
  }
}

document.getElementById("modal-install-btn").addEventListener("click", async () => {
  const lensName = activateTargetLens;
  if (!lensName) return;
  const installBtn = document.getElementById("modal-install-btn");
  const msgEl = document.getElementById("modal-msg");
  const isUpdate = installBtn.dataset.mode === "update";
  installBtn.disabled = true;
  installBtn.textContent = isUpdate ? "업데이트 중…" : "설치 중…";
  msgEl.textContent = isUpdate ? "업데이트하는 중입니다…" : "설치하는 중입니다…";
  msgEl.className = "modal-msg";

  showBusyOverlay(isUpdate ? "업데이트하는 중…" : "설치하는 중…");
  const stopPolling = startInstallProgressPolling((text) => {
    msgEl.textContent = text;
    updateBusyOverlay(text);
  });
  let result, lens;
  try {
    result = await window.pywebview.api.install_or_update(lensName);
    lens = await window.pywebview.api.diagnose_one(lensName, false);
  } catch {
    // 여기서 안 잡으면 버튼이 "설치 중…"인 채로 영구 비활성 — 모달을 닫는 것 말고는
    // 빠져나갈 방법이 없었다.
    stopPolling();
    hideBusyOverlay();
    msgEl.textContent = "설치/업데이트 중 오류가 발생했습니다. 다시 시도해주세요.";
    msgEl.className = "modal-msg fail";
    installBtn.disabled = false;
    installBtn.textContent = isUpdate ? "업데이트" : "설치";
    return;
  }
  stopPolling();
  hideBusyOverlay();
  replaceCard(lensName, lens);

  if (result.ok) {
    renderActivateModalState(lens, lens.display_name);
    msgEl.textContent = isUpdate ? "업데이트 완료." : "설치 완료! 이제 라이선스 키를 입력하세요.";
    msgEl.className = "modal-msg ok";
  } else {
    msgEl.textContent = "설치/업데이트에 실패했습니다. 잠시 후 다시 시도해주세요.";
    msgEl.className = "modal-msg fail";
    installBtn.disabled = false;
    installBtn.textContent = isUpdate ? "업데이트" : "설치";
  }
});

function closeActivateModal(completed = false) {
  document.getElementById("modal-backdrop").hidden = true;
  activateTargetLens = null;
  if (completed) {
    onboardingHandleModalClosed("license");
  } else if (onboardingActive && onboardingSubStep === "license") {
    // 취소·Escape는 "이 단계를 마쳤다"는 신호가 아니다 — 진행률을 몰래 채우고 다음
    // 단계로 넘어가면 안 된다(실제로 신고된 버그). 마법사만 멈추고, 카드에서 언제든
    // 개별적으로 이어서 할 수 있게 둔다.
    skipOnboarding();
  }
}

async function confirmActivate() {
  const key = document.getElementById("modal-key-input").value.trim();
  const apiKeyField = document.getElementById("modal-apikey-field");
  const apiKeyInput = document.getElementById("modal-apikey-input");
  const needsApiKey = !apiKeyField.hidden;
  const apiKey = needsApiKey ? apiKeyInput.value.trim() : "";
  const msgEl = document.getElementById("modal-msg");

  if (!key) {
    msgEl.textContent = "라이선스 키를 입력하세요.";
    msgEl.className = "modal-msg fail";
    return;
  }
  if (needsApiKey && !apiKey) {
    msgEl.textContent = "DART API 키를 입력하세요.";
    msgEl.className = "modal-msg fail";
    return;
  }

  msgEl.textContent = "확인 중…";
  msgEl.className = "modal-msg";

  const lensName = activateTargetLens;
  const activateResult = await window.pywebview.api.activate(lensName, key);
  document.getElementById("modal-key-input").value = "";

  let apiKeyResult = { ok: true };
  if (needsApiKey) {
    apiKeyResult = await window.pywebview.api.register_api_key(lensName, "dart_api", apiKey);
    apiKeyInput.value = "";
  }

  if (activateResult.ok && apiKeyResult.ok) {
    msgEl.textContent = needsApiKey
      ? `활성화됨 (${activateResult.license_id_masked}) · API 키 등록됨`
      : `활성화됨 (${activateResult.license_id_masked})`;
    msgEl.className = "modal-msg ok";
    // 성공 표시를 보여주는 사이(0.7초) 입력칸이 비어 있는데도 계속 조작이 돼서,
    // 그 틈에 다시 확인을 누르면 이미 쓴 키로 한 번 더 활성화를 시도하게 된다.
    // 다음 단계로 넘어갈 때까지 모달 전체를 잠근다.
    setModalInteractive("modal-backdrop", false);
    setTimeout(async () => {
      // 성공 표시를 보여주는 사이 사용자가 이 모달을 닫고 *다른* Lens로 다시 열었을 수
      // 있다 — 그때 이 늦은 타이머가 그대로 실행되면 방금 연 모달을 강제로 닫아버린다.
      // 대상이 그대로일 때만 진행한다.
      if (activateTargetLens !== lensName) return;
      try {
        // 카드/lensDataCache를 먼저 최신 상태로 갱신한 다음에 모달을 닫는다 —
        // closeActivateModal()이 마법사 다음 단계(등록 대상 선택 등)를 그 자리에서
        // 바로 열 수 있는데, 순서가 반대면 그 다음 모달이 낡은(활성화 전) 데이터로
        // 열려서 빈 것처럼 보이는 깜빡임이 있었다(실제로 재현된 버그).
        const lens = await window.pywebview.api.diagnose_one(lensName, false);
        replaceCard(lensName, lens);
      } catch {
        // 재진단이 실패해도 활성화 자체는 이미 끝났다 — 모달이 "활성화됨" 상태로
        // 영영 열려 있는 것보다 닫고 넘어가는 게 낫다.
      }
      if (activateTargetLens === lensName) closeActivateModal(true);
    }, 700);
  } else {
    const parts = [];
    if (!activateResult.ok) parts.push(activateResult.message || "라이선스 활성화 실패");
    if (needsApiKey && !apiKeyResult.ok) parts.push(apiKeyResult.error || "API 키 등록 실패");
    msgEl.textContent = parts.join(" · ");
    msgEl.className = "modal-msg fail";
  }
}

/* ---------- MCP 등록 대상 선택 모달 ---------- */
// claude-desktop/claude-code 둘 다로 바로 등록하던 걸, Codex 같은 다른 로컬 MCP
// 클라이언트도 고를 수 있게 체크박스 모달로 바꿨다 — 설치 안 된 대상(Codex 등)은
// 회색으로 비활성화해서 헛클릭을 막는다.

let registerTargetLens = null;

async function openRegisterModal(lensName) {
  registerTargetLens = lensName;
  const lens = lensDataCache[lensName];
  const displayName = lens ? lens.display_name : lensName;
  document.getElementById("register-title").textContent = `${displayName} — MCP 등록 대상`;
  setModalInteractive("register-backdrop", true); // 직전 대기 중 잠근 걸 푼다
  const msgEl = document.getElementById("register-msg");
  msgEl.textContent = "";
  msgEl.className = "modal-msg";
  const container = document.getElementById("register-targets");
  container.innerHTML = "<div class=\"register-target-row disabled\">불러오는 중…</div>";
  document.getElementById("register-backdrop").hidden = false;

  const targets = await window.pywebview.api.available_targets(lensName);
  const currentTargets = (lens && lens.targets) || [];
  container.innerHTML = targets
    .map((t) => {
      const checked = t.installed && (t.id !== "codex" || currentTargets.includes(t.id));
      // 아직 없는 앱은 등록해봐야 읽어갈 주체가 없다 — 막기만 하지 말고 받는 곳을
      // 바로 열 수 있게 해준다(없는 게 잘못이 아니라 다음 할 일을 알려주는 것).
      const getItHtml = t.installed
        ? ""
        : `<button type="button" class="target-install-link" data-install-url="${escapeAttr(t.install_url)}">받으러 가기</button>`;
      return `
        <label class="register-target-row${t.installed ? "" : " disabled"}">
          <input type="checkbox" value="${t.id}" ${t.installed ? "" : "disabled"} ${checked ? "checked" : ""}>
          <span>${escapeHtml(t.label)}${t.installed ? "" : " — 아직 설치 안 됨"}</span>
          ${getItHtml}
        </label>`;
    })
    .join("");

  container.querySelectorAll(".target-install-link").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault(); // label 안이라 클릭이 체크박스로 새지 않게
      window.pywebview.api.open_url(btn.dataset.installUrl);
    });
  });
}

function closeRegisterModal(completed = false) {
  document.getElementById("register-backdrop").hidden = true;
  registerTargetLens = null;
  if (completed) {
    onboardingHandleModalClosed("register");
  } else if (onboardingActive && onboardingSubStep === "register") {
    // 취소는 진행이 아니다 — 마법사를 멈추고 억지로 다음 단계로 넘기지 않는다.
    skipOnboarding();
  }
}

document.getElementById("register-cancel").addEventListener("click", () => closeRegisterModal());
document.getElementById("register-confirm").addEventListener("click", async () => {
  const lensName = registerTargetLens;
  if (!lensName) return;
  const checked = Array.from(
    document.querySelectorAll("#register-targets input[type=checkbox]:checked")
  ).map((el) => el.value);
  const msgEl = document.getElementById("register-msg");
  if (!checked.length) {
    msgEl.textContent = "하나 이상 선택하세요.";
    msgEl.className = "modal-msg fail";
    return;
  }
  const confirmBtn = document.getElementById("register-confirm");
  confirmBtn.disabled = true; // 더블클릭 시 subprocess 2개가 같은 설정 파일에 동시에 쓴다
  msgEl.textContent = "등록하는 중…";
  msgEl.className = "modal-msg";

  let result, lens;
  try {
    result = await window.pywebview.api.register(lensName, checked);
    lens = await window.pywebview.api.diagnose_one(lensName, false);
  } catch {
    msgEl.textContent = "등록 중 오류가 발생했습니다. 다시 시도해주세요.";
    msgEl.className = "modal-msg fail";
    confirmBtn.disabled = false;
    return;
  }
  confirmBtn.disabled = false;
  replaceCard(lensName, lens);

  if (result.ok) {
    // 마법사 중이면 완료 화면에서 한 번만 안내하므로 여기선 생략 — 매 단계 반복하면
    // 잔소리가 된다. 카드에서 직접 등록한 경우엔 여기가 유일한 안내 지점이다.
    let needsRestartNote = false;
    if (!onboardingActive) {
      try {
        needsRestartNote = await window.pywebview.api.claude_desktop_running();
      } catch {
        /* 확인 실패 시 조용히 생략 */
      }
    }
    msgEl.textContent = needsRestartNote
      ? "등록 완료 — Claude Desktop을 껐다 켜야 도구가 나타납니다."
      : "등록 완료.";
    msgEl.className = "modal-msg ok";
    if (needsRestartNote) {
      // 마법사 밖에서 등록한 경우엔 여기가 유일한 안내 지점이라, 안내만 하지 말고
      // 바로 실행할 수단까지 같이 준다.
      const restartBtn = document.createElement("button");
      restartBtn.className = "action-btn primary";
      restartBtn.textContent = "Claude 다시 시작";
      restartBtn.addEventListener("click", async () => {
        await restartClaudeDesktop(restartBtn);
        closeRegisterModal(true);
      });
      msgEl.appendChild(document.createElement("br"));
      msgEl.appendChild(restartBtn);
      return; // 사용자가 직접 닫거나 재시작을 누를 때까지 모달 유지
    }
    setModalInteractive("register-backdrop", false); // 닫히기까지의 대기 동안 조작 차단
    // 늦게 도착한 타이머가 사용자가 방금 다시 연 모달을 닫지 않도록 대상 확인.
    setTimeout(() => {
      if (registerTargetLens === lensName) closeRegisterModal(true);
    }, 700);
  } else {
    msgEl.textContent = result.error || "등록에 실패했습니다.";
    msgEl.className = "modal-msg fail";
  }
});

/* ---------- 텔레그램 로그인 마법사 ----------
   전화번호 → SMS 인증코드 → (필요하면) 2단계 인증까지, 라이선스 활성화처럼 한 번에
   끝나지 않고 여러 번 대화가 필요하다. 매 단계 window.pywebview.api.telegram_login_step
   호출 결과(status)에 따라 다음에 보여줄 입력칸이 바뀐다 — Python 쪽(orchestrator의
   InteractiveProcess)이 실제 자식 프로세스와의 stdin/stdout 왕복을 담당하고, 여기는
   그 상태를 화면에 반영만 한다. */

let telegramLoginTargetLens = null;
let telegramLoginStep = null; // "credentials" | "phone" | "code" | "2fa"

const TELEGRAM_LOGIN_FIELD_HTML = {
  credentials: `
    <div class="modal-field">
      <div class="field-label">발급 순서</div>
      <ol class="credential-help-steps">
        <li>열린 페이지에서 본인 전화번호로 로그인(텔레그램으로 코드가 옵니다)</li>
        <li>"API development tools" 클릭</li>
        <li>App title/Short name에 아무 이름이나 입력 후 Create</li>
        <li>화면에 나온 api_id(숫자)·api_hash(영숫자)를 아래에 붙여넣기</li>
      </ol>
      <button type="button" class="action-btn" id="tg-login-reopen-signup">my.telegram.org 다시 열기</button>
    </div>
    <div class="modal-field">
      <div class="field-label">API_ID</div>
      <input type="text" id="tg-login-api-id" placeholder="숫자" autocomplete="off">
    </div>
    <div class="modal-field">
      <div class="field-label">API_HASH</div>
      <input type="text" id="tg-login-api-hash" placeholder="영숫자" autocomplete="off">
    </div>`,
  phone: `
    <div class="modal-field">
      <div class="field-label">전화번호</div>
      <input type="text" id="tg-login-phone" placeholder="+821012345678" autocomplete="off">
    </div>`,
  code: `
    <div class="modal-field">
      <div class="field-label">인증 코드</div>
      <input type="text" id="tg-login-code" placeholder="텔레그램 앱으로 받은 코드" autocomplete="off">
    </div>`,
  "2fa": `
    <div class="modal-field">
      <div class="field-label">2단계 인증 비밀번호</div>
      <input type="password" id="tg-login-password" autocomplete="off">
    </div>`,
};

function renderTelegramLoginStep(step) {
  telegramLoginStep = step;
  document.getElementById("telegram-login-fields").innerHTML = TELEGRAM_LOGIN_FIELD_HTML[step] || "";
  if (step === "credentials") {
    document.getElementById("tg-login-reopen-signup").addEventListener("click", () => {
      window.pywebview.api.open_telegram_api_signup();
    });
  }
  const firstInput = document.querySelector("#telegram-login-fields input");
  if (firstInput) firstInput.focus();
}

function telegramLoginPayloadForCurrentStep() {
  if (telegramLoginStep === "credentials") {
    return {
      api_id: document.getElementById("tg-login-api-id").value.trim(),
      api_hash: document.getElementById("tg-login-api-hash").value.trim(),
    };
  }
  if (telegramLoginStep === "phone") {
    return { phone: document.getElementById("tg-login-phone").value.trim() };
  }
  if (telegramLoginStep === "code") {
    return { code: document.getElementById("tg-login-code").value.trim() };
  }
  if (telegramLoginStep === "2fa") {
    return { password: document.getElementById("tg-login-password").value };
  }
  return null;
}

function applyTelegramLoginStatus(status) {
  const msgEl = document.getElementById("telegram-login-msg");
  const nextBtn = document.getElementById("telegram-login-next");

  if (status.status === "need_credentials") {
    msgEl.textContent = "처음 연결이라 API_ID/API_HASH가 필요합니다 — my.telegram.org를 열었습니다.";
    msgEl.className = "modal-msg";
    renderTelegramLoginStep("credentials");
    window.pywebview.api.open_telegram_api_signup();
  } else if (status.status === "need_phone") {
    msgEl.textContent = "";
    msgEl.className = "modal-msg";
    renderTelegramLoginStep("phone");
  } else if (status.status === "code_sent") {
    // channel은 login_cli가 텔레그램 서버 응답에서 실제 전송 경로를 읽어온 값 —
    // "텔레그램 앱으로 보냈다"고 무조건 가정하면 실제로 SMS/전화로 갔을 때 엉뚱한 곳을
    // 보게 만든다(실사용 중 "코드가 안 온다" 문의의 원인이었음).
    msgEl.textContent = status.channel || "인증 코드를 보냈습니다.";
    msgEl.className = "modal-msg ok";
    renderTelegramLoginStep("code");
  } else if (status.status === "need_2fa") {
    msgEl.textContent = "2단계 인증이 걸려 있는 계정입니다.";
    msgEl.className = "modal-msg";
    renderTelegramLoginStep("2fa");
  } else if (status.status === "error") {
    // 단계는 그대로 — 같은 입력칸을 다시 보여주고 값만 비워 재입력을 유도한다
    // (기존 대화형 CLI가 같은 자리에서 재시도하던 것과 동일한 동작).
    msgEl.textContent = status.message || "오류가 발생했습니다. 다시 시도해주세요.";
    msgEl.className = "modal-msg fail";
    document.querySelectorAll("#telegram-login-fields input").forEach((el) => (el.value = ""));
    const firstInput = document.querySelector("#telegram-login-fields input");
    if (firstInput) firstInput.focus();
  } else if (status.status === "already_logged_in" || status.status === "ok") {
    const name = status.me ? `${status.me.first_name}${status.me.username ? " (@" + status.me.username + ")" : ""}` : "";
    msgEl.textContent = `로그인 완료${name ? ": " + name : ""}`;
    msgEl.className = "modal-msg ok";
    document.getElementById("telegram-login-fields").innerHTML = "";
    nextBtn.hidden = true;
    setModalInteractive("telegram-login-backdrop", false); // 닫히기까지 조작 차단
    const lensName = telegramLoginTargetLens;
    setTimeout(async () => {
      // 늦은 타이머가 사용자가 방금 다시 연 모달을 닫지 않도록 대상 확인.
      if (telegramLoginTargetLens !== lensName) return;
      try {
        // activate와 동일한 이유로 순서 고정 — 카드 먼저 갱신, 모달은 그 다음에 닫는다.
        const lens = await window.pywebview.api.diagnose_one(lensName, false);
        replaceCard(lensName, lens);
      } catch {
        /* 로그인 자체는 이미 끝났다 — 재진단 실패로 모달을 붙잡아두지 않는다 */
      }
      if (telegramLoginTargetLens === lensName) closeTelegramLoginModal(true);
    }, 1200);
  } else {
    msgEl.textContent = "알 수 없는 응답입니다.";
    msgEl.className = "modal-msg fail";
  }
}

async function openTelegramLoginModal(lensName) {
  telegramLoginTargetLens = lensName;
  telegramLoginStep = null;
  document.getElementById("telegram-login-next").hidden = false;
  document.getElementById("telegram-login-next").disabled = false;
  document.getElementById("telegram-login-msg").textContent = "연결하는 중…";
  document.getElementById("telegram-login-msg").className = "modal-msg";
  document.getElementById("telegram-login-fields").innerHTML = "";
  document.getElementById("telegram-login-backdrop").hidden = false;
  setModalInteractive("telegram-login-backdrop", true); // 직전 대기 중 잠근 걸 푼다

  const status = await window.pywebview.api.telegram_login_start();
  applyTelegramLoginStatus(status);
}

function closeTelegramLoginModal(completed = false) {
  document.getElementById("telegram-login-backdrop").hidden = true;
  window.pywebview.api.telegram_login_cancel();
  telegramLoginTargetLens = null;
  telegramLoginStep = null;
  if (completed) {
    onboardingHandleModalClosed("telegram-login");
  } else if (onboardingActive && onboardingSubStep === "telegram-login") {
    // 취소는 진행이 아니다 — 마법사를 멈추고 억지로 다음 단계로 넘기지 않는다.
    skipOnboarding();
  }
}

document.getElementById("telegram-login-cancel").addEventListener("click", () => closeTelegramLoginModal());
document.getElementById("telegram-login-next").addEventListener("click", async () => {
  const payload = telegramLoginPayloadForCurrentStep();
  if (!payload) return;
  const nextBtn = document.getElementById("telegram-login-next");
  const msgEl = document.getElementById("telegram-login-msg");
  nextBtn.disabled = true;
  msgEl.textContent = "확인하는 중…";
  msgEl.className = "modal-msg";
  try {
    const status = await window.pywebview.api.telegram_login_step(payload);
    applyTelegramLoginStatus(status);
  } catch {
    // 여기서 안 잡으면 "확인하는 중…"에서 버튼이 영구 비활성 — 로그인 도중 빠져나갈
    // 방법이 취소밖에 없었다.
    msgEl.textContent = "확인 중 오류가 발생했습니다. 다시 시도해주세요.";
    msgEl.className = "modal-msg fail";
  } finally {
    nextBtn.disabled = false;
  }
});

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;

  if (action === "copy-cmd") {
    const cmd = btn.dataset.cmd;
    window.pywebview.api.copy_to_clipboard(cmd).then((ok) => {
      showToast(ok ? "명령어가 복사되었습니다." : "클립보드 복사에 실패했습니다.");
    });
    return;
  }

  const lensName = btn.dataset.lens;
  if (action === "open-detail") {
    openDetailModal(lensName);
    return;
  }
  if (action === "activate") {
    openActivateModal(lensName);
    return;
  }
  if (action === "register") {
    openRegisterModal(lensName);
    return;
  }
  if (action === "uninstall") {
    const lens = lensDataCache[lensName];
    const displayName = lens ? lens.display_name : lensName;
    if (!confirm(`${displayName}를 삭제할까요?\n\n라이선스·API 키 등 설정은 남아있어서, 다시 설치하면 자동으로 이어집니다.`)) {
      return;
    }
    runAction("uninstall", lensName);
    return;
  }
  if (action === "telegram-login") {
    openTelegramLoginModal(lensName);
    return;
  }
  if (action === "copy") {
    copyDiagnosticText(lensName, btn);
    return;
  }
  runAction(action, lensName, btn.dataset.repairId);
});

let toastTimer = null;

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.hidden = true;
  }, 2200);
}

async function copyDiagnosticText(lensName, btn) {
  const text = await window.pywebview.api.diagnostic_text(lensName);
  const ok = await window.pywebview.api.copy_to_clipboard(text);
  if (!ok) {
    showToast("클립보드 복사에 실패했습니다.");
    return;
  }
  showToast("진단 결과가 복사되었습니다.");
  const original = btn.textContent;
  btn.textContent = "복사됨 ✓";
  btn.disabled = true;
  setTimeout(() => {
    btn.textContent = original;
    btn.disabled = false;
  }, 1400);
}

document.getElementById("modal-cancel").addEventListener("click", () => closeActivateModal());
document.getElementById("modal-confirm").addEventListener("click", confirmActivate);
document.getElementById("modal-apikey-reopen-signup").addEventListener("click", () => {
  window.pywebview.api.open_dart_api_signup();
});
document.getElementById("modal-key-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") confirmActivate();
  if (e.key === "Escape") closeActivateModal();
});
document.getElementById("refresh-btn").addEventListener("click", loadDiagnosis);

// 이 버튼 한 자리가 Claude Desktop 상황에 맞게 바뀐다 — Lens는 이 앱 위에서만 동작하니,
// 없는 사람에겐 "받기"가, 있는 사람에겐 "다시 시작"(등록 반영에 꼭 필요한 단계)이 맞다.
let claudeDesktopInstalled = null;
let claudeDesktopInstallUrl = null; // 백엔드가 주는 값만 쓴다(JS에 URL을 또 적어두지 않게)

async function refreshRestartClaudeButton() {
  const btn = document.getElementById("restart-claude-btn");
  try {
    const targets = await window.pywebview.api.available_targets("stocklens");
    const desktop = targets.find((t) => t.id === "claude-desktop");
    claudeDesktopInstalled = !!(desktop && desktop.installed);
    claudeDesktopInstallUrl = desktop ? desktop.install_url : null;
    btn.textContent = claudeDesktopInstalled ? "Claude 다시 시작" : "Claude Desktop 받기";
    btn.hidden = false;
  } catch {
    btn.hidden = true; // 확인 자체가 안 되면 엉뚱한 안내를 하느니 감춘다
  }
}

document.getElementById("restart-claude-btn").addEventListener("click", async (e) => {
  if (!claudeDesktopInstalled) {
    if (claudeDesktopInstallUrl) window.pywebview.api.open_url(claudeDesktopInstallUrl);
    showToast("설치가 끝나면 '진단 재실행'을 눌러주세요.");
    return;
  }
  // 다른 앱을 끄는 동작이라 반드시 확인을 받는다 — 대화 중이었을 수 있다.
  if (!confirm("Claude Desktop을 껐다 다시 켤까요?\n\n등록한 도구를 Claude가 새로 읽어들이려면 이 과정이 필요합니다.")) {
    return;
  }
  await restartClaudeDesktop(e.currentTarget);
});

/* ---------- 지원 문의 ---------- */

let supportInfo = null;

async function openSupportModal() {
  document.getElementById("support-backdrop").hidden = false;
  document.getElementById("support-status").textContent = "번들을 만드는 중…(로그·상태 파일을 모아 zip으로 저장합니다)";
  document.getElementById("support-status").className = "modal-msg";
  document.getElementById("support-to").textContent = "";
  document.getElementById("support-subject").textContent = "";
  document.getElementById("support-body").textContent = "";

  try {
    supportInfo = await window.pywebview.api.create_support_bundle();
    document.getElementById("support-status").textContent = "폴더가 열렸습니다 — zip 파일을 첨부해 보내주세요.";
    document.getElementById("support-status").className = "modal-msg ok";
    document.getElementById("support-to").textContent = supportInfo.to;
    document.getElementById("support-subject").textContent = supportInfo.subject;
    document.getElementById("support-body").textContent = supportInfo.body;
  } catch {
    document.getElementById("support-status").textContent = "번들을 만들지 못했습니다.";
    document.getElementById("support-status").className = "modal-msg fail";
  }
}

function closeSupportModal() {
  document.getElementById("support-backdrop").hidden = true;
}

document.getElementById("support-btn").addEventListener("click", openSupportModal);
document.getElementById("support-close").addEventListener("click", closeSupportModal);
document.getElementById("support-copy").addEventListener("click", async () => {
  if (!supportInfo) return;
  const text = `받는사람: ${supportInfo.to}\n제목: ${supportInfo.subject}\n\n${supportInfo.body}`;
  const ok = await window.pywebview.api.copy_to_clipboard(text);
  showToast(ok ? "메일 내용이 복사되었습니다." : "클립보드 복사에 실패했습니다.");
});

/* ---------- 단계별 가이드 투어 ----------
   설명만 하는 스포트라이트 — 이제 자동으로는 안 뜨고(최초 실행 자동 시작은 온보딩
   마법사가 대신함) #guide-btn으로 언제든 다시 볼 수 있는 참고용으로만 남는다. */

const TOUR_STEPS = [
  { selector: "#readout", title: "전체 상태 요약", desc: "몇 개 Lens가 정상인지, 업데이트나 조치가 필요한 게 있는지 한눈에 보여줍니다." },
  { selector: ".card:first-child .focus-ring", title: "상태 표시등", desc: "원 색깔로 상태를 보여줍니다. 틸색(가득 참)=정상, 주황=주의, 빨강=조치 필요, 회색=미설치." },
  { selector: ".card:first-child .field-list", title: "세부 정보", desc: "업데이트 · 라이선스 · MCP 등록 · 최근 진단 시각을 확인할 수 있습니다." },
  { selector: ".card:first-child", title: "문제 자세히 보기", desc: "카드에 문제가 있으면 \"문제 N건 — 자세히\" 줄이 나타나고, 누르면 무엇이 문제인지 창으로 자세히 봅니다(카드 크기는 그대로예요). 지금 이 Lens는 문제가 없어서 그 줄이 안 보이지만, 예시로 그 창을 띄워드릴게요 — \"조치\" 옆의 굵고 밑줄 있는 명령어를 누르면 그 명령어가 그대로 복사됩니다.", demo: "detail" },
  { selector: ".card:first-child .actions", title: "동작 버튼", desc: "진단(다시 검사) · MCP 등록(Claude Desktop/Code/Codex 중 골라서 연결) · 활성화(라이선스 키 입력) · 복구(자동으로 고침)를 여기서 할 수 있습니다. 결과 복사는 \"문제 자세히\" 창 안에 있습니다." },
  { selector: "#support-btn", title: "지원 문의", desc: "문제가 안 풀리면 여기를 눌러보세요. 로그 파일을 모아 zip으로 만들고 폴더를 열어줍니다. 뜨는 창의 받는사람 · 제목 · 내용을 복사해서 메일에 붙여넣고, zip 파일을 첨부해서 보내면 됩니다." },
  { selector: "#self-update-btn", title: "매니저 업데이트", desc: "LeetKit Manager 자체의 새 버전이 있을 때만 이 버튼이 나타납니다. 누르면 설치하고 앱이 자동으로 닫히니, 바탕화면 아이콘으로 다시 실행하면 됩니다.", requiresVisible: true },
  { selector: "#guide-btn", title: "가이드 다시 보기", desc: "이 설명은 여기 버튼을 눌러 언제든 다시 볼 수 있습니다." },
];

let tourSteps = [];
let tourIndex = 0;

function closeTourDemoModals() {
  // 지금은 데모가 상세 모달 하나뿐이지만, 나중에 다른 데모가 늘어도 여기 한 곳만
  // 손보면 되게 일반화해 둔다.
  closeDetailModal();
}

function applyStepDemo(step) {
  closeTourDemoModals();
  if (step.demo === "detail") {
    // 실제 첫 카드가 아니라 고정된 예시 데이터를 쓴다 — 마침 문제가 없는 상태에서
    // 가이드를 봐도 "문제 자세히" 창과 조치 클릭-복사 기능을 항상 보여주기 위해서다.
    openDetailModalExample();
  }
}

function positionTour(i) {
  const step = tourSteps[i];
  const rect = step.el.getBoundingClientRect();
  const pad = 6;

  const hl = document.getElementById("tour-highlight");
  hl.style.left = `${rect.left - pad}px`;
  hl.style.top = `${rect.top - pad}px`;
  hl.style.width = `${rect.width + pad * 2}px`;
  hl.style.height = `${rect.height + pad * 2}px`;

  document.getElementById("tour-step-label").textContent = `${i + 1} / ${tourSteps.length}`;
  document.getElementById("tour-title").textContent = step.title;
  document.getElementById("tour-desc").textContent = step.desc;
  document.getElementById("tour-prev").style.visibility = i === 0 ? "hidden" : "visible";
  document.getElementById("tour-next").textContent = i === tourSteps.length - 1 ? "완료" : "다음";

  applyStepDemo(step);

  const tooltip = document.getElementById("tour-tooltip");
  const tw = tooltip.offsetWidth || 300;
  const th = tooltip.offsetHeight || 140;

  if (step.demo) {
    // 데모 모달은 좌상단으로 옮겨 두었으니(applyStepDemo/CSS .demo-position),
    // 가이드 설명은 반대쪽 우하단에 고정 — 화면을 나눠 써서 겹칠 자리 자체가 없다.
    tooltip.style.left = `${window.innerWidth - tw - 24}px`;
    tooltip.style.top = `${window.innerHeight - th - 24}px`;
  } else {
    let top = rect.bottom + pad + 12;
    if (top + th > window.innerHeight - 10) top = Math.max(10, rect.top - pad - th - 12);
    const left = Math.min(Math.max(10, rect.left), window.innerWidth - tw - 10);
    tooltip.style.top = `${top}px`;
    tooltip.style.left = `${left}px`;
  }

  step.el.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function startTour() {
  tourSteps = TOUR_STEPS.map((s) => ({ ...s, el: document.querySelector(s.selector) })).filter(
    (s) => s.el && (!s.requiresVisible || !s.el.hidden)
  );
  if (!tourSteps.length) return;
  tourIndex = 0;
  document.getElementById("tour-overlay").hidden = false;
  document.getElementById("tour-tooltip").hidden = false;
  positionTour(tourIndex);
}

function endTour() {
  closeTourDemoModals();
  document.getElementById("tour-overlay").hidden = true;
  document.getElementById("tour-tooltip").hidden = true;
}

document.getElementById("guide-btn").addEventListener("click", startTour);
document.getElementById("patchnotes-btn").addEventListener("click", () => {
  window.pywebview.api.open_patch_notes();
});
document.getElementById("tour-skip").addEventListener("click", endTour);
document.getElementById("tour-next").addEventListener("click", () => {
  if (tourIndex >= tourSteps.length - 1) {
    endTour();
    return;
  }
  tourIndex += 1;
  positionTour(tourIndex);
});
document.getElementById("tour-prev").addEventListener("click", () => {
  if (tourIndex === 0) return;
  tourIndex -= 1;
  positionTour(tourIndex);
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!document.getElementById("tour-overlay").hidden) endTour();
  if (!document.getElementById("support-backdrop").hidden) closeSupportModal();
  if (!document.getElementById("detail-backdrop").hidden) closeDetailModal();
  if (!document.getElementById("modal-backdrop").hidden) closeActivateModal();
  if (!document.getElementById("register-backdrop").hidden) closeRegisterModal();
  if (!document.getElementById("telegram-login-backdrop").hidden) closeTelegramLoginModal();
});

/* ---------- 온보딩 마법사 ----------
   최초 실행에만 자동으로 뜬다(가이드 투어와 달리 실제로 설치·MCP 등록·활성화까지
   대신 진행) — 이미 만들어져 있는 활성화/등록 대상 선택/텔레그램 로그인 모달을 그대로
   순서대로 열어주는 "자동 진행자" 역할만 한다. 각 모달이 닫히면(성공이든 취소든)
   onboardingHandleModalClosed가 다음 단계로 넘어간다. 가이드 투어와 "최초 실행"을
   각자 다른 localStorage 키로 판단하면 서로 다시 튀어나올 수 있어 — 이 마법사가
   유일한 자동 시작 판단자이고, 투어는 이제 #guide-btn으로만 수동으로 본다. */

const ONBOARDING_DONE_KEY = "leetkit-manager-onboarding-done";

let onboardingActive = false;
let onboardingLensNames = []; // 순서만 들고 있고, 실제 데이터는 항상 lensDataCache에서 최신으로 조회
let onboardingIndex = 0;
let onboardingSubStep = null; // "register" | "credentials"

function onboardingCurrentLens() {
  return lensDataCache[onboardingLensNames[onboardingIndex]];
}

function onboardingProgressLabel() {
  return `${onboardingIndex + 1}/${onboardingLensNames.length}`;
}

// 이미 끝난 단계는 다시 안 보여준다 — 개발 중 재테스트하거나, 마법사가 우연히 다시
// 뜬 경우에도 이미 등록/활성화된 Lens를 붙잡고 늘어지지 않게.
//
// TelegramLens는 다른 두 Lens와 달리 자격증명이 두 가지다 — ①상품 라이선스 키(세
// Lens 전부 공통, DartLens API 키처럼 별개) ②텔레그램 계정 로그인(전화번호·SMS —
// TelegramLens에만 있는 개념). 이 둘은 서로 무관해서 하나가 끝났다고 다른 하나를
// 건너뛰면 안 된다 — 실제로 처음엔 이걸 헷갈려서 라이선스 입력칸이 통째로 빠지는
// 버그가 있었다.
// 설치된 MCP 클라이언트 목록은 자주 안 바뀌니 한 번만 조회해 재사용한다.
let installedTargetIdsCache = null;

async function installedTargetIds() {
  if (installedTargetIdsCache) return installedTargetIdsCache;
  try {
    const targets = await window.pywebview.api.available_targets("stocklens");
    installedTargetIdsCache = targets.filter((t) => t.installed).map((t) => t.id);
  } catch {
    installedTargetIdsCache = [];
  }
  return installedTargetIdsCache;
}

async function onboardingLensNeedsRegister(lens) {
  // 예전엔 "하나라도 등록돼 있으면 끝"으로 봤는데, 그러면 Claude Desktop에만 등록된
  // 사람은 Claude Code·Codex를 추가할 기회를 마법사에서 영영 못 받았다(Codex가
  // 세 번째 대상으로 생기면서 실제로 드러난 문제). *설치된* 클라이언트 중 아직 등록
  // 안 된 게 있으면 선택 화면을 보여준다 — 설치 안 된 건 애초에 대상이 아니라 무시.
  const installed = await installedTargetIds();
  if (!installed.length) return lens.targets.length === 0;
  return installed.some((id) => !lens.targets.includes(id));
}

function onboardingLensNeedsLicense(lens) {
  return lens.license_status !== "active";
}

function onboardingLensNeedsTelegramLogin(lens) {
  const loginCheck = lens.checks.find((c) => c.id === "TELEGRAM_LOGIN");
  return !(loginCheck && loginCheck.status === "ok");
}

function onboardingSkipButtonHtml() {
  return `<button class="action-btn" id="onboarding-skip-btn">마법사 끄기</button>`;
}

function onboardingWireSkipButton() {
  const btn = document.getElementById("onboarding-skip-btn");
  if (btn) btn.addEventListener("click", skipOnboarding);
}

// 어느 Lens가 서브스텝을 몇 개 거치는지 — TelegramLens만 텔레그램 로그인이 하나 더
// 있어서 전체 대비 정확한 진행률(연속 바)을 계산하려면 이 목록이 필요하다.
function onboardingSubStepsFor(lens) {
  const steps = ["register", "license"];
  if (lens && lens.name === "telegramlens") steps.push("telegram-login");
  return steps;
}

function onboardingRenderSteps() {
  const stepsEl = document.getElementById("onboarding-steps");
  if (!onboardingLensNames.length) {
    stepsEl.innerHTML = "";
    return;
  }
  stepsEl.innerHTML = onboardingLensNames
    .map((name, i) => {
      const lens = lensDataCache[name];
      const label = lens ? lens.display_name : name;
      let cls = "pending";
      let mark = String(i + 1);
      if (i < onboardingIndex) {
        cls = "done";
        mark = "✓";
      } else if (i === onboardingIndex) {
        cls = "active";
      }
      return `
        <div class="onboarding-step ${cls}">
          <div class="onboarding-step-dot">${mark}</div>
          <div class="onboarding-step-label">${label}</div>
        </div>`;
    })
    .join("");
}

// 세 Lens 전체를 100으로 놓고, 지금 Lens 안에서도 설치→등록→라이선스→(텔레그램
// 로그인) 서브스텝만큼 더 채운다 — 계단식이 아니라 매끄럽게 차오르는 바를 위해.
function onboardingProgressFraction() {
  const total = onboardingLensNames.length || 1;
  let fraction = onboardingIndex / total;
  const lens = onboardingCurrentLens();
  if (lens && onboardingSubStep) {
    const steps = onboardingSubStepsFor(lens);
    const idx = steps.indexOf(onboardingSubStep);
    if (idx >= 0) fraction += (idx / steps.length) / total;
  }
  return Math.min(1, Math.max(0, fraction));
}

function onboardingSetBanner(text, actionsHtml) {
  document.getElementById("onboarding-banner-text").textContent = text;
  document.getElementById("onboarding-banner-actions").innerHTML = actionsHtml;
  document.getElementById("onboarding-banner").hidden = false;
  onboardingRenderSteps();
  document.getElementById("onboarding-progress-fill").style.width = `${onboardingProgressFraction() * 100}%`;
}

function onboardingHideBanner() {
  document.getElementById("onboarding-banner").hidden = true;
}

async function onboardingAllLensesReady() {
  const lenses = Object.values(lensDataCache);
  if (!lenses.length) return false;
  for (const lens of lenses) {
    if (lens.not_installed || lens.incompatible) return false;
    if (await onboardingLensNeedsRegister(lens)) return false;
    if (onboardingLensNeedsLicense(lens)) return false;
    // 텔레그램 로그인은 TelegramLens에만 있는 개념 — StockLens/DartLens는 애초에
    // TELEGRAM_LOGIN 체크 자체가 없어서 이 함수가 항상 "로그인 필요"를 돌려준다.
    // 게이트 없이 모든 Lens에 적용하던 탓에 이 함수가 영영 false만 반환해,
    // "이미 다 끝난 사용자는 인트로를 건너뛴다"는 분기가 아예 죽어 있었다.
    if (lens.name === "telegramlens" && onboardingLensNeedsTelegramLogin(lens)) return false;
  }
  return true;
}

async function maybeShowOnboardingIntro() {
  if (localStorage.getItem(ONBOARDING_DONE_KEY)) return;

  // Manager를 재설치했거나 버전만 새로 받은 경우 등, 이미 3개 Lens 전부 설치·등록·
  // 라이선스(+텔레그램 로그인)까지 끝나 있으면 "시작할까요?"부터 물어볼 이유가 없다 —
  // 조용히 완료 처리하고 바로 평소 대시보드를 보여준다.
  if (await onboardingAllLensesReady()) {
    localStorage.setItem(ONBOARDING_DONE_KEY, "1");
    return;
  }

  onboardingSetBanner(
    "처음 오셨네요! 바로가기 저장 위치부터 정하고, StockLens·DartLens·TelegramLens 순서로 설치·등록·활성화를 도와드릴까요?",
    `<button class="action-btn" id="onboarding-later-btn">나중에</button>
     <button class="action-btn primary" id="onboarding-start-btn">시작</button>`
  );
  document.getElementById("onboarding-later-btn").addEventListener("click", skipOnboarding);
  document.getElementById("onboarding-start-btn").addEventListener("click", startOnboarding);
}

function skipOnboarding() {
  // "나중에"/"마법사 끄기"는 완료가 아니라 미룸 — 영구 플래그를 안 세워서 다음 실행에
  // 다시 물어본다. 실제로 다 끝냈을 때만(finishOnboarding) 영구히 안 뜨게 한다.
  onboardingActive = false;
  onboardingSubStep = null;
  onboardingHideBanner();
}

async function startOnboarding() {
  onboardingActive = true;
  onboardingSetBanner("실행 아이콘(바로가기)을 만들 폴더를 골라주세요 — 프로그램 파일 자체는 복사되지 않고, 그 폴더엔 아이콘 하나만 생깁니다.", "");

  const shortcutResult = await window.pywebview.api.choose_shortcut_location();
  if (!onboardingActive) return;
  if (shortcutResult && shortcutResult.ok === false) {
    showToast("바로가기 생성에 실패했습니다 — 나중에 이 프로그램 파일을 직접 바탕화면으로 드래그해서 만들 수 있습니다.");
  }

  const data = await window.pywebview.api.diagnose(false);
  render(data); // lensDataCache 채움(renderCard 내부에서)
  onboardingLensNames = data.lenses.map((l) => l.name);
  onboardingIndex = 0;
  await onboardingProcessCurrentLens();
}

async function onboardingProcessCurrentLens() {
  if (!onboardingActive) return;
  if (onboardingIndex >= onboardingLensNames.length) {
    finishOnboarding();
    return;
  }
  let lens = onboardingCurrentLens();

  if (lens.not_installed) {
    onboardingSetBanner(`${onboardingProgressLabel()} · ${lens.display_name} · 설치하는 중…`, onboardingSkipButtonHtml());
    onboardingWireSkipButton();
    // 최초 구매자가 실제로 기다리는 구간 — 여기가 가장 길고(수십 초) 가장 불안하다.
    // 이 동안 다른 버튼을 못 누르게 화면을 덮는다.
    showBusyOverlay(`${lens.display_name}를 설치하는 중…`);
    const stopPolling = startInstallProgressPolling((text) => {
      updateBusyOverlay(`${lens.display_name} · ${text}`);
      if (!onboardingActive) return;
      document.getElementById("onboarding-banner-text").textContent =
        `${onboardingProgressLabel()} · ${lens.display_name} · ${text}`;
    });
    let result, updated;
    try {
      result = await window.pywebview.api.install_or_update(lens.name);
    } finally {
      stopPolling();
      hideBusyOverlay();
    }
    if (!onboardingActive) return;
    updated = await window.pywebview.api.diagnose_one(lens.name, false);
    replaceCard(lens.name, updated); // lensDataCache 갱신
    lens = updated;

    // 설치가 실제로 안 됐으면(uv 실패·네트워크 등) 여기서 멈춘다 — 등록·라이선스
    // 모달을 열어봐야 어차피 "설치 안 됨" 에러만 반복될 뿐이라, 조용히 다음 단계로
    // 넘어가는 대신 원인을 보여주고 재시도/건너뛰기를 고르게 한다.
    if (!result.ok || lens.not_installed) {
      onboardingSetBanner(
        `${onboardingProgressLabel()} · ${lens.display_name} · 설치에 실패했습니다.`,
        `<button class="action-btn" id="onboarding-skip-btn">마법사 끄기</button>
         <button class="action-btn" id="onboarding-skip-lens-btn">이 Lens 건너뛰기</button>
         <button class="action-btn primary" id="onboarding-retry-btn">다시 시도</button>`
      );
      onboardingWireSkipButton();
      document.getElementById("onboarding-retry-btn").addEventListener("click", onboardingProcessCurrentLens);
      document.getElementById("onboarding-skip-lens-btn").addEventListener("click", onboardingAdvanceToNextLens);
      return;
    }
  }

  if (!onboardingActive) return;
  await onboardingOpenRegisterOrSkip(lens);
}

async function onboardingOpenRegisterOrSkip(lens) {
  if (await onboardingLensNeedsRegister(lens)) {
    onboardingSubStep = "register";
    onboardingSetBanner(`${onboardingProgressLabel()} · ${lens.display_name} · MCP 등록 대상을 골라주세요`, onboardingSkipButtonHtml());
    onboardingWireSkipButton();
    openRegisterModal(lens.name);
  } else {
    onboardingOpenLicenseOrSkip(lens);
  }
}

function onboardingOpenLicenseOrSkip(lens) {
  if (onboardingLensNeedsLicense(lens)) {
    onboardingSubStep = "license";
    onboardingSetBanner(`${onboardingProgressLabel()} · ${lens.display_name} · 라이선스 키를 입력해주세요`, onboardingSkipButtonHtml());
    onboardingWireSkipButton();
    openActivateModal(lens.name);
  } else {
    onboardingOpenTelegramLoginOrSkip(lens);
  }
}

function onboardingOpenTelegramLoginOrSkip(lens) {
  if (lens.name === "telegramlens" && onboardingLensNeedsTelegramLogin(lens)) {
    onboardingSubStep = "telegram-login";
    onboardingSetBanner(`${onboardingProgressLabel()} · ${lens.display_name} · 텔레그램 로그인`, onboardingSkipButtonHtml());
    onboardingWireSkipButton();
    openTelegramLoginModal(lens.name);
  } else {
    onboardingAdvanceToNextLens();
  }
}

function onboardingAdvanceToNextLens() {
  onboardingSubStep = null;
  onboardingIndex += 1;
  onboardingProcessCurrentLens();
}

function onboardingHandleModalClosed(closedSubStep) {
  if (!onboardingActive || onboardingSubStep !== closedSubStep) return;
  // 모달이 방금 닫히면서 이미 카드를 최신 데이터로 갱신해뒀다(register/activate 성공
  // 경로가 diagnose_one + replaceCard를 거침) — lensDataCache에서 그 최신 상태를 읽는다.
  const lens = onboardingCurrentLens();

  if (closedSubStep === "register") {
    onboardingOpenLicenseOrSkip(lens);
  } else if (closedSubStep === "license") {
    onboardingOpenTelegramLoginOrSkip(lens);
  } else if (closedSubStep === "telegram-login") {
    onboardingAdvanceToNextLens();
  }
}

// MCP 등록을 반영하는 마지막 한 걸음. 트레이에 남는 특성상 "완전히 종료"가 실제로
// 막히는 구간이라, 설명 대신 버튼으로 대신해준다.
async function restartClaudeDesktop(btn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "다시 시작하는 중…";
  try {
    const result = await window.pywebview.api.restart_claude_desktop();
    if (result.ok) {
      onboardingHideBanner(); // 마법사에서 눌렀다면 배너를 닫는다(밖에서 눌렀으면 무해)
      showToast("Claude Desktop을 다시 시작했습니다 — 이제 도구가 보일 거예요.");
    } else {
      showToast(result.error || "다시 시작하지 못했습니다. 직접 껐다 켜주세요.");
    }
  } catch {
    showToast("다시 시작하지 못했습니다. 직접 껐다 켜주세요.");
  } finally {
    // 성공 경로에서 그냥 return하면 상단 상시 버튼이 "다시 시작하는 중…"인 채로
    // 영구 비활성으로 굳는다(마법사 배너는 사라져서 티가 안 났다).
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function finishOnboarding() {
  onboardingSubStep = null;
  onboardingIndex = onboardingLensNames.length; // 스텝 전부 done으로, 바는 100%로
  onboardingActive = false;
  localStorage.setItem(ONBOARDING_DONE_KEY, "1");

  // MCP 등록은 설정 파일을 고치는 것뿐이고 Claude Desktop은 그 파일을 "켤 때" 읽는다 —
  // 이미 떠 있으면 도구가 안 보인다. 여기서 안내하지 않으면 고객은 "설치가 안 됐다"고
  // 판단한다. 실제로 떠 있을 때만 말해서 불필요하게 겁주지 않는다.
  let claudeRunning = false;
  try {
    claudeRunning = await window.pywebview.api.claude_desktop_running();
  } catch {
    /* 확인 실패 시엔 조용히 넘어간다 — 잘못된 안내보다 없는 게 낫다 */
  }

  // TelegramLens는 설정을 마쳐도 아직 모은 데이터가 없다 — 수집은 Claude Desktop을
  // 열어야 시작된다. 이 사실을 미리 깔아두지 않으면, 방금 다 설치한 사람이 "왜 아무
  // 내용이 없지?"라고 느낀다. 다 끝난 화면에서 한 줄로 미리 알려준다.
  const telegramNote = lensDataCache["telegramlens"]
    ? " Claude Desktop을 열면 텔레그램 채널 메시지 수집이 시작됩니다."
    : "";

  if (claudeRunning) {
    onboardingSetBanner(
      "설정이 끝났습니다! 마지막으로 Claude Desktop을 껐다 켜야 도구가 나타납니다." + telegramNote,
      `<button class="action-btn" id="onboarding-done-btn">나중에 직접 할게요</button>
       <button class="action-btn primary" id="onboarding-restart-claude-btn">지금 다시 시작</button>`
    );
    document.getElementById("onboarding-done-btn").addEventListener("click", () => {
      onboardingHideBanner();
      showToast("Claude Desktop을 껐다 켜면 도구가 나타납니다.");
    });
    document.getElementById("onboarding-restart-claude-btn").addEventListener("click", () =>
      restartClaudeDesktop(document.getElementById("onboarding-restart-claude-btn"))
    );
    return;
  }

  onboardingSetBanner(
    "모든 설정이 끝났습니다! Claude Desktop을 열어주세요." + telegramNote,
    `<button class="action-btn primary" id="onboarding-done-btn">알겠습니다</button>`
  );
  document.getElementById("onboarding-done-btn").addEventListener("click", () => {
    onboardingHideBanner();
    showToast("설정은 언제든 각 카드에서 다시 바꿀 수 있습니다.");
  });
}

/* ---------- 매니저 자기 자신 업데이트 ---------- */

async function checkSelfUpdate() {
  const info = await window.pywebview.api.check_self_update();
  const btn = document.getElementById("self-update-btn");
  if (info.update_available) {
    btn.hidden = false;
    btn.title = `v${info.current} → v${info.latest}`;
  } else {
    btn.hidden = true;
  }
}

document.getElementById("self-update-btn").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.textContent = "설치 중…";
  const result = await window.pywebview.api.self_update();
  if (result.ok) {
    showToast(`v${result.version}로 업데이트됨 — 앱을 다시 시작합니다`);
    setTimeout(() => window.pywebview.api.quit(), 1600);
  } else {
    showToast(result.error || "업데이트에 실패했습니다.");
    btn.disabled = false;
    btn.textContent = "업데이트";
  }
});

// 어떤 백엔드 호출이 예외를 던져도 최소한 사용자에게 보이게 하는 안전망.
// 개별 호출부에 try/catch가 빠져 있으면 "…중" 상태로 화면이 영원히 멈춘 채 아무 안내도
// 없었다(실제로 여러 곳에서 그랬다). 원인 표시까지는 못 해도 "멈춘 게 아니라 실패다"를
// 알려서 사용자가 다시 시도할 수 있게 한다.
window.addEventListener("unhandledrejection", (e) => {
  const detail = (e.reason && (e.reason.message || e.reason)) || "";
  showToast(`처리 중 오류가 발생했습니다. 다시 시도해주세요. ${detail}`.trim());
});

window.addEventListener("pywebviewready", async () => {
  // 각 단계를 독립적으로 — 예전엔 loadDiagnosis()가 실패하면 그 뒤 두 줄이 아예 실행되지
  // 않아, 하필 최초 실행 시 온보딩 마법사가 통째로 안 뜨는 결과가 됐다.
  try {
    await loadDiagnosis();
  } catch {
    document.getElementById("readout-text").textContent =
      "진단에 실패했습니다 — '진단 재실행'을 눌러 다시 시도해주세요.";
  }
  try {
    await checkSelfUpdate();
  } catch {
    /* 업데이트 확인 실패는 조용히 넘어간다 — 버튼이 안 뜰 뿐 사용에 지장 없음 */
  }
  try {
    await refreshRestartClaudeButton();
  } catch {
    /* 못 띄워도 나머지 기능엔 지장 없다 */
  }
  try {
    await maybeShowOnboardingIntro();
  } catch {
    /* 마법사를 못 띄워도 대시보드 자체는 쓸 수 있어야 한다 */
  }
});
