/* LeetKit Manager 대시보드 로직. window.pywebview.api.* 가 유일한 백엔드 접점이다 —
   여기서 subprocess나 uv를 직접 다루지 않는다(그건 Python orchestrator의 몫). */

const TARGET_LABEL = { "claude-desktop": "Claude Desktop", "claude-code": "Claude Code" };

// 카드는 항상 고정 높이를 유지한다(문제를 펼쳐도 안 늘어남) — 상세 데이터는 모달에서만
// 보여주므로, 마지막으로 받은 각 Lens 데이터를 여기 캐싱해서 모달을 다시 fetch 없이 연다.
const lensDataCache = {};

function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
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
  const targets = lens.targets.length ? lens.targets.map((t) => TARGET_LABEL[t] || t).join(" · ") : "미등록";
  const problems = lens.checks.filter((c) => !["ok", "active", "skip", "info-skip"].includes(c.status));
  const inProgress = lens.checks.filter((c) => c.status === "active");

  const repairBtn = lens.repairable_repair_id
    ? `<button class="action-btn warn" data-action="repair" data-lens="${lens.name}" data-repair-id="${lens.repairable_repair_id}">복구</button>`
    : "";
  const installOrUpdateBtn = lens.not_installed
    ? `<button class="action-btn primary" data-action="install" data-lens="${lens.name}">설치</button>`
    : lens.update_available
    ? `<button class="action-btn primary" data-action="install" data-lens="${lens.name}">업데이트</button>`
    : "";

  return `
    <div class="card" data-card="${lens.name}">
      <div class="card-head">
        ${focusRingSvg(cls)}
        <div class="card-title">
          <span class="card-name">${lens.display_name}</span>
          <span class="card-version">${lens.installed_version ? "v" + lens.installed_version : "미설치"}</span>
        </div>
        <span class="card-readiness ${cls}">${lens.readiness}</span>
      </div>
      <div class="field-list">
        <div class="field-row"><span class="field-label">업데이트</span><span class="field-value">${updateLabel(lens.update_available)}</span></div>
        <div class="field-row"><span class="field-label">라이선스</span><span class="field-value emph">${licenseLabel(lens.license_status)}${lens.license_id_masked ? " · " + lens.license_id_masked : ""}</span></div>
        <div class="field-row"><span class="field-label">MCP 등록</span><span class="field-value">${targets}</span></div>
        <div class="field-row"><span class="field-label">최근 진단</span><span class="field-value">${formatCheckedAt(lens.checked_at)}</span></div>
      </div>
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
        ${repairBtn}
        ${installOrUpdateBtn}
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

function renderCheckItem(c) {
  const detailLines = (c.details && c.details.lines) || [];
  const linesHtml = detailLines.length
    ? `<ul class="check-detail-lines">${detailLines.map((l) => `<li>${l}</li>`).join("")}</ul>`
    : "";
  const actionHtml = c.action
    ? `<div class="check-action"><span class="check-action-label">조치</span><span class="check-action-cmd" data-action="copy-cmd" data-cmd="${escapeAttr(c.action)}" title="눌러서 복사">${c.action}</span></div>`
    : "";
  const cls = c.status === "active" ? "check-item active" : "check-item";
  return `<div class="${cls}"><span class="check-id">${c.id}</span>${c.summary}${linesHtml}${actionHtml}</div>`;
}

function renderDetailModal(lens) {
  const targets = lens.targets.length ? lens.targets.map((t) => TARGET_LABEL[t] || t).join(" · ") : "미등록";
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

async function runAction(action, lensName, extra) {
  const grid = document.getElementById("grid");
  const card = grid.querySelector(`[data-card="${lensName}"]`);
  if (card) card.style.opacity = "0.55";
  try {
    if (action === "diagnose") {
      const lens = await window.pywebview.api.diagnose_one(lensName, false);
      replaceCard(lensName, lens);
    } else if (action === "register") {
      await window.pywebview.api.register(lensName);
      const lens = await window.pywebview.api.diagnose_one(lensName, false);
      replaceCard(lensName, lens);
    } else if (action === "repair") {
      await window.pywebview.api.repair(lensName, extra);
      const lens = await window.pywebview.api.diagnose_one(lensName, false);
      replaceCard(lensName, lens);
    } else if (action === "install") {
      await window.pywebview.api.install_or_update(lensName);
      const lens = await window.pywebview.api.diagnose_one(lensName, false);
      replaceCard(lensName, lens);
    }
  } finally {
    if (card) card.style.opacity = "1";
  }
}

function replaceCard(lensName, lensData) {
  const grid = document.getElementById("grid");
  const card = grid.querySelector(`[data-card="${lensName}"]`);
  if (card) card.outerHTML = renderCard(lensData);
}

/* ---------- 활성화 모달 ---------- */

let activateTargetLens = null;

function openActivateModal(lensName) {
  activateTargetLens = lensName;
  const lens = lensDataCache[lensName];
  const displayName = lens ? lens.display_name : lensName;
  document.getElementById("modal-title").textContent = `${displayName} 라이선스 활성화`;
  document.getElementById("modal-key-input").value = "";
  document.getElementById("modal-backdrop").hidden = false;
  renderActivateModalState(lens, displayName);
}

// 활성화 모달 안에서 설치/업데이트까지 한 번에 — 처음 구매한 사람이 "먼저 설치하고,
// 창 찾아서 활성화 누르고" 순서를 몰라도 헤매지 않게 이 모달 하나로 끝나게 한다.
function renderActivateModalState(lens, displayName) {
  const msgEl = document.getElementById("modal-msg");
  const keyInput = document.getElementById("modal-key-input");
  const confirmBtn = document.getElementById("modal-confirm");
  const installBtn = document.getElementById("modal-install-btn");

  if (lens && lens.not_installed) {
    msgEl.textContent = `${displayName}가 아직 설치되지 않았습니다. 먼저 설치해야 활성화할 수 있어요.`;
    msgEl.className = "modal-msg fail";
    keyInput.disabled = true;
    confirmBtn.hidden = true;
    installBtn.hidden = false;
    installBtn.disabled = false;
    installBtn.textContent = "설치";
    installBtn.dataset.mode = "install";
  } else if (lens && lens.update_available) {
    msgEl.textContent = "새 버전이 있습니다. 업데이트하거나, 지금 버전으로 바로 활성화해도 됩니다.";
    msgEl.className = "modal-msg";
    keyInput.disabled = false;
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

  const result = await window.pywebview.api.install_or_update(lensName);
  const lens = await window.pywebview.api.diagnose_one(lensName, false);
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

function closeActivateModal() {
  document.getElementById("modal-backdrop").hidden = true;
  activateTargetLens = null;
}

async function confirmActivate() {
  const key = document.getElementById("modal-key-input").value.trim();
  const msgEl = document.getElementById("modal-msg");
  if (!key) {
    msgEl.textContent = "키를 입력하세요.";
    msgEl.className = "modal-msg fail";
    return;
  }
  msgEl.textContent = "확인 중…";
  msgEl.className = "modal-msg";
  const result = await window.pywebview.api.activate(activateTargetLens, key);
  document.getElementById("modal-key-input").value = "";
  if (result.ok) {
    msgEl.textContent = `활성화됨 (${result.license_id_masked})`;
    msgEl.className = "modal-msg ok";
    const lensName = activateTargetLens;
    setTimeout(async () => {
      closeActivateModal();
      const lens = await window.pywebview.api.diagnose_one(lensName, false);
      replaceCard(lensName, lens);
    }, 700);
  } else {
    msgEl.textContent = result.message || "활성화에 실패했습니다.";
    msgEl.className = "modal-msg fail";
  }
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;

  if (action === "copy-cmd") {
    const cmd = btn.dataset.cmd;
    navigator.clipboard
      .writeText(cmd)
      .then(() => showToast("명령어가 복사되었습니다."))
      .catch(() => showToast("클립보드 접근이 차단되어 복사하지 못했습니다."));
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
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    showToast("클립보드 접근이 차단되어 복사하지 못했습니다.");
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

document.getElementById("modal-cancel").addEventListener("click", closeActivateModal);
document.getElementById("modal-confirm").addEventListener("click", confirmActivate);
document.getElementById("modal-key-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") confirmActivate();
  if (e.key === "Escape") closeActivateModal();
});
document.getElementById("refresh-btn").addEventListener("click", loadDiagnosis);

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
  try {
    await navigator.clipboard.writeText(text);
    showToast("메일 내용이 복사되었습니다.");
  } catch {
    showToast("클립보드 접근이 차단되어 복사하지 못했습니다.");
  }
});

/* ---------- 단계별 가이드 투어 ---------- */

const GUIDE_SEEN_KEY = "leetkit-manager-guide-seen";

const TOUR_STEPS = [
  { selector: "#readout", title: "전체 상태 요약", desc: "몇 개 Lens가 정상인지, 업데이트나 조치가 필요한 게 있는지 한눈에 보여줍니다." },
  { selector: ".card:first-child .focus-ring", title: "상태 표시등", desc: "원 색깔로 상태를 보여줍니다. 틸색(가득 참)=정상, 주황=주의, 빨강=조치 필요, 회색=미설치." },
  { selector: ".card:first-child .field-list", title: "세부 정보", desc: "업데이트 · 라이선스 · MCP 등록 · 최근 진단 시각을 확인할 수 있습니다." },
  { selector: ".card:first-child", title: "문제 자세히 보기", desc: "카드에 문제가 있으면 \"문제 N건 — 자세히\" 줄이 나타나고, 누르면 무엇이 문제인지 창으로 자세히 봅니다(카드 크기는 그대로예요). 지금 이 Lens는 문제가 없어서 그 줄이 안 보이지만, 예시로 그 창을 띄워드릴게요 — \"조치\" 옆의 굵고 밑줄 있는 명령어를 누르면 그 명령어가 그대로 복사됩니다.", demo: "detail" },
  { selector: ".card:first-child .actions", title: "동작 버튼", desc: "진단(다시 검사) · MCP 등록(Claude 연결) · 활성화(라이선스 키 입력) · 복구(자동으로 고침)를 여기서 할 수 있습니다. 결과 복사는 \"문제 자세히\" 창 안에 있습니다." },
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
  localStorage.setItem(GUIDE_SEEN_KEY, "1");
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
});

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

window.addEventListener("pywebviewready", async () => {
  await loadDiagnosis();
  checkSelfUpdate();
  if (!localStorage.getItem(GUIDE_SEEN_KEY)) {
    startTour();
  }
});
