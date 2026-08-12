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

// 후기 모달 문구에서 **…**를 굵게. 먼저 통째로 이스케이프하고 그 뒤에 우리 표시만
// 태그로 바꾸므로, 원격 설정(review_prompt.json)에 HTML이 섞여 들어와도 그냥 글자로
// 나온다 — 문구를 릴리스 없이 고칠 수 있게 열어둔 통로라 이 순서가 중요하다.
function renderEmphasis(text) {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
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
  return (
    {
      active: "활성",
      missing: "없음",
      invalid: "유효하지 않음",
      expired: "기간 종료",
      revoked: "사용 중지됨",
      clock: "날짜 확인 필요",
    }[status] || "확인 필요"
  );
}

// 남은 날짜. 오늘이 만료일이면 0(= "오늘까지"), 지났으면 음수.
function daysUntil(isoDate) {
  if (!isoDate) return null;
  const end = new Date(isoDate + "T00:00:00Z");
  if (isNaN(end)) return null;
  const today = new Date();
  const utcToday = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  return Math.round((end.getTime() - utcToday) / 86400000);
}

// 기간이 있는 키에만 붙는 꼬리표. 남은 날이 적을수록 눈에 띄게 한다 — 마지막 날에야
// 알게 되면 결정할 시간이 없다.
function trialBadge(lens) {
  const left = daysUntil(lens.license_expires_on);
  if (left === null || lens.license_status !== "active") return "";
  const cls = left <= 2 ? "warn" : "";
  const text = left <= 0 ? "오늘까지" : `${left}일 남음`;
  return ` · <span class="trial-badge ${cls}">${text}</span>`;
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

// 디자인 안 8c — 카드 안 상황 알림(문제·진행중)을 구분선이 아니라 블록으로 보여준다.
// 예전엔 문제 줄에만 윗선을 그었는데, 카드마다 선이 있는 곳이 달라져 선의 의미가
// 흐려졌다. 블록으로 통일하면 "카드 안의 상황 알림"이 한 종류로 묶여 보인다.
//
// 건수 옆에 무엇이 걸렸는지도 같이 보여준다 — "문제 2건"만으로는 열어보기 전까지
// 알 수 없다. 넘치면 말줄임으로 자른다(카드 높이는 그대로 유지).
function statusBlock(label, checks, lensName, extraClass) {
  // 진행률 숫자가 있으면 바로 보여준다. "수집 중"이라는 말만으로는 멈춘 건지 도는
  // 건지 알 수 없어 조치가 필요한 상태로 오해하기 쉽다 — 바와 남은 시간이 있으면
  // "기다리면 되는 상태"라는 게 그 자리에서 읽힌다.
  const withProgress = checks.find((c) => c.details && c.details.progress);
  if (withProgress) return progressBlock(withProgress, lensName);

  const summary = checks
    .map((c) => c.summary)
    .filter(Boolean)
    .join(" · ");
  return `
      <div class="status-block ${extraClass}" data-action="open-detail" data-lens="${lensName}">
        <span class="status-count">${label} ${checks.length}건</span>
        <span class="status-summary">${escapeHtml(summary)}</span>
        <span class="status-chevron">›</span>
      </div>`;
}

function formatEta(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  if (seconds < 60) return "약 1분 남음";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `약 ${minutes}분 남음`;
  return `약 ${Math.round(minutes / 60)}시간 남음`;
}

function progressBlock(check, lensName) {
  const p = check.details.progress;
  const total = Number(p.total) || 0;
  const done = Math.min(Number(p.done) || 0, total);
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;
  const unit = p.unit ? escapeHtml(p.unit) : "";

  // 아래 줄은 있는 것만 붙인다 — 없는 값을 자리만 채우려고 지어내지 않는다.
  const notes = [];
  if (Number.isFinite(Number(p.fetched))) {
    notes.push(`${Number(p.fetched).toLocaleString("ko-KR")}건 수집`);
  }
  const eta = formatEta(Number(p.eta_sec));
  if (eta) notes.push(eta);
  notes.push("끝나면 알려드릴게요");

  return `
      <div class="progress-block" data-action="open-detail" data-lens="${lensName}">
        <div class="progress-head">
          <span class="progress-title">${escapeHtml(check.summary || "진행 중")}</span>
          <span class="progress-count mono">${done.toLocaleString("ko-KR")} / ${total.toLocaleString("ko-KR")}${unit ? " " + unit : ""}</span>
        </div>
        <div class="progress-track"><div class="progress-fill" style="width:${percent}%"></div></div>
        <span class="progress-note">${escapeHtml(notes.join(" · "))}</span>
      </div>`;
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
      <div class="card-main">
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
        <div class="field-row"><span class="field-label">라이선스</span><span class="field-value emph">${licenseLabel(lens.license_status)}${lens.license_id_masked ? ' · <span class="mono">' + escapeHtml(lens.license_id_masked) + "</span>" : ""}${trialBadge(lens)}</span></div>
        <div class="field-row"><span class="field-label">MCP 등록</span><span class="field-value">${targets}</span></div>
        <div class="field-row"><span class="field-label">최근 진단</span><span class="field-value mono">${formatCheckedAt(lens.checked_at)}</span></div>
      </div>
      </div>
      <div class="card-status">
      ${lens.problem_detail ? `
      <div class="problem-detail">${escapeHtml(lens.problem_detail)}</div>` : ""}
      ${problems.length ? statusBlock("문제", problems, lens.name, "") : ""}
      ${inProgress.length ? statusBlock("진행중", inProgress, lens.name, "progress") : ""}
      </div>
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

// 이 체크는 앱 안에서 바로 처리할 수 있다 — 어떤 흐름으로 보낼지의 표.
// 조치 문구를 그대로 터미널에서 실행하는 방식은 쓰지 않는다: 실제 문구 대부분이
// `<라이선스-키>`·`{claude-desktop|...}` 같은 자리표시자를 담고 있어서 그대로는
// 실행이 안 되고(직접 확인함), 키 입력·대상 선택·전화번호 인증은 이미 전용 모달이
// 제대로 처리하고 있다. 임의 문자열을 셸에 넘기지 않으니 더 안전하기도 하다.
const CHECK_RESOLVER = {
  MCP_CONFIG_VALID: "register",
  MCP_CONFIG_DESKTOP: "register",
  MCP_CONFIG_CODE: "register",
  MCP_CONFIG_CODEX: "register",
  LICENSE_ACTIVE: "activate",
  DART_API_KEY: "activate",
  TELEGRAM_LOGIN: "telegram-login",
};

function checkResolverFor(c) {
  if (c.repairable && c.repair_id) return "repair"; // Lens가 스스로 고칠 수 있는 항목
  return CHECK_RESOLVER[c.id] || null;
}

function renderCheckItem(c, lensName) {
  const detailLines = (c.details && c.details.lines) || [];
  const linesHtml = detailLines.length
    ? `<ul class="check-detail-lines">${detailLines.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ul>`
    : "";

  const resolver = checkResolverFor(c);
  // 가이드의 예시 카드는 실제 Lens가 아니다 — 버튼 모양은 그대로 보여주되 누르면
  // 없는 Lens를 호출하게 되므로 비활성으로 둔다.
  const isExample = lensName === EXAMPLE_LENS_DATA.name;
  const actionHtml = resolver && isExample
    ? `<div class="check-action">
         <button class="check-resolve-btn" disabled title="예시 화면입니다">지금 해결하기</button>
       </div>`
    : resolver
    ? `<div class="check-action">
         <button class="check-resolve-btn" data-action="resolve-check" data-lens="${escapeAttr(lensName || "")}"
                 data-check-id="${escapeAttr(c.id)}" data-resolver="${resolver}"
                 data-repair-id="${escapeAttr(c.repair_id || "")}">지금 해결하기</button>
       </div>`
    : c.action
    ? `<div class="check-action"><span class="check-action-label">조치</span><span class="check-action-cmd" data-action="copy-cmd" data-cmd="${escapeAttr(c.action)}" title="눌러서 복사">${escapeHtml(c.action)}</span></div>`
    : "";
  const cls = c.status === "active" ? "check-item active" : "check-item";
  const label = CHECK_ID_LABEL[c.id] || escapeHtml(c.id);
  return `<div class="${cls}"><span class="check-id">${label}</span>${escapeHtml(c.summary)}${linesHtml}${actionHtml}</div>`;
}

// MCP 등록을 마친 뒤, 그것만으로는 도구가 안 도는 경우를 한 줄로 알려준다.
// 정상이면 "이미 돼 있다"고 확인해주고(CLI가 하던 안심), 빠졌으면 무엇을 눌러야
// 하는지 말해준다. 여기서 보는 값은 방금 다시 받은 진단 결과라 항상 최신이다.
function credentialStatusNote(lens) {
  if (!lens || !Array.isArray(lens.checks)) return "";
  const missing = [];
  const ready = [];
  for (const check of lens.checks) {
    // 자격증명 성격의 항목만 본다 — 설치·등록 상태는 이 자리에서 할 말이 아니다.
    if (!["LICENSE_ACTIVE", "DART_API_KEY", "TELEGRAM_LOGIN"].includes(check.id)) continue;
    const label = CHECK_ID_LABEL[check.id] || check.id;
    (check.status === "ok" ? ready : missing).push(label);
  }
  if (missing.length) {
    const list = missing.join(", ");
    return `${list}${particle(list, "이", "가")} 아직입니다 — 카드에서 마저 끝내주세요. 그 전까지는 도구가 잠겨 있습니다.`;
  }
  if (ready.length) {
    // 키는 설정 파일이 아니라 OS 자격 증명 저장소에 들어간다. 파일만 보면 없어
    // 보이므로, 이미 돼 있다는 걸 말해주지 않으면 멀쩡한 걸 다시 넣게 된다.
    const list = ready.join(", ");
    return `${list}${particle(list, "은", "는")} 이미 등록돼 있어 그대로 씁니다.`;
  }
  return "";
}

// 받침 유무에 맞는 조사를 고른다. "DART API 키은(는)"처럼 괄호로 뭉개면 읽는 사람이
// 매번 걸린다 — 목록 항목이 상황마다 바뀌어서 문구를 고정으로 쓸 수 없는 자리다.
// 한글 음절이 아닌 글자로 끝나면(영문·숫자) 판단할 수 없으니 받침 없는 쪽으로 둔다.
function particle(word, withBatchim, withoutBatchim) {
  const last = (word || "").trim().slice(-1);
  const code = last.charCodeAt(0);
  if (!(code >= 0xac00 && code <= 0xd7a3)) return withoutBatchim;
  return (code - 0xac00) % 28 ? withBatchim : withoutBatchim;
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
    ? `<div class="detail-progress"><div class="detail-section-label">진행중</div>${inProgress.map((c) => renderCheckItem(c, lens.name)).join("")}</div>`
    : "";
  const problemsHtml = problems.length
    ? `${inProgress.length ? `<div class="detail-section-label">문제</div>` : ""}${problems.map((c) => renderCheckItem(c, lens.name)).join("")}`
    : `<div class="check-item">문제 없음</div>`;

  document.getElementById("detail-title").textContent = `${lens.display_name} 상세`;
  document.getElementById("detail-body").innerHTML = `
    <div class="field-list">
      <div class="field-row"><span class="field-label">버전</span><span class="field-value emph">${lens.installed_version ? "v" + escapeHtml(lens.installed_version) : "미설치"}</span></div>
      <div class="field-row"><span class="field-label">업데이트</span><span class="field-value emph">${updateLabel(lens.update_available)}</span></div>
      <div class="field-row"><span class="field-label">라이선스</span><span class="field-value emph">${licenseLabel(lens.license_status)}${lens.license_id_masked ? " · " + escapeHtml(lens.license_id_masked) : ""}</span></div>
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

// opts.skipClaudePrompt — 여러 Lens를 한 번에 처리할 때 쓴다. 낱개로 돌리면 실패할
// 때마다 "Claude를 껐다 켤까요?"가 따로 떠서, 3개면 3번 묻게 된다. 일괄 처리는
// 시작 전에 한 번만 묻고 여기서는 안 묻는다.
async function runAction(action, lensName, extra, opts = {}) {
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
      // 지금 Claude Desktop에 물려 있는지 / 새로 까는 건지를 **바꾸기 전에** 봐둔다.
      // 새로 까는 경우엔 아직 등록 전이라 재시작을 권할 자리가 아니고(MCP 등록 모달이
      // 따로 안내한다), 이미 쓰던 걸 올리는 경우에만 껐다 켜라고 해야 한다.
      const before = lensDataCache[lensName] || {};
      const wasOnClaudeDesktop = (before.targets || []).includes("claude-desktop");
      const wasInstalled = !before.not_installed;
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
        if (result.claude_blocking && !opts.skipClaudePrompt) {
          await offerCloseClaudeAndRetry(lensName, "install");
        } else if (result.claude_blocking) {
          // 일괄 처리 중 — 이미 한 번 물어봤으므로 다시 안 묻고 사실만 남긴다.
          showToast(`${displayName}: Claude Desktop이 파일을 쓰고 있어 건너뛰었습니다.`);
        } else {
          showToast(
            result.rollback_command
              // 이유를 먼저 말한다 — 예전엔 "실패했습니다"와 복구 명령뿐이라, 사용자도
              // 우리도 왜 실패했는지 알 방법이 없었다.
              ? `설치/업데이트 실패 — ${result.error || "원인을 알 수 없습니다"} (이전 버전 복구: ${result.rollback_command})`
              : `설치/업데이트 실패 — ${result.error || "원인을 알 수 없습니다"}`
          );
        }
      } else if (!opts.skipRestartPrompt) {
        // 성공했는데 아무 말도 안 하고 있었다 — 카드가 "최신"으로 바뀌는 게 전부라,
        // 켜져 있는 Claude가 아직 옛 버전을 돌리고 있다는 걸 알 방법이 없었다.
        // 윈도우는 파일 잠금 때문에 대개 claude_blocking으로 걸려 껐다 켜는 흐름을
        // 타지만, 맥은 잠금이 없어 켜진 채로 "성공"하고 조용히 옛 버전이 남는다.
        await noteLensFilesChanged({
          registeredOnClaudeDesktop: wasOnClaudeDesktop && wasInstalled,
          headline: wasInstalled
            ? `${displayName} 업데이트 완료${lens && lens.installed_version ? ` (v${lens.installed_version})` : ""}.`
            : `${displayName} 설치 완료.`,
          afterRestart: "아직 이전 버전을 쓰고 있습니다.",
          whenClosed: "다음에 Claude Desktop을 열면 새 버전이 적용됩니다.",
        });
      }
    } else if (action === "uninstall") {
      const displayName = (lensDataCache[lensName] || {}).display_name || lensName;
      // 삭제해도 MCP 설정은 남는다(패키지만 지운다) — 켜져 있는 Claude에는 도구가
      // 그대로 보이고, 누르면 그때서야 실패한다. 지우기 전에 물려 있었는지 봐둔다.
      const wasOnClaudeDesktop = ((lensDataCache[lensName] || {}).targets || []).includes("claude-desktop");
      showBusyOverlay(`${displayName}를 삭제하는 중…`);
      let result, lens;
      try {
        result = await window.pywebview.api.uninstall(lensName, extra === "with-license");
        lens = await window.pywebview.api.diagnose_one(lensName, false);
      } finally {
        hideBusyOverlay();
      }
      replaceCard(lensName, lens);
      if (result.ok) {
        await noteLensFilesChanged({
          registeredOnClaudeDesktop: wasOnClaudeDesktop,
          headline: result.license_removed
            ? `${displayName}를 삭제했습니다 — 라이선스도 이 컴퓨터에서 지웠습니다.`
            : `${displayName}를 삭제했습니다 — 다시 설치할 수 있습니다.`,
          afterRestart: "삭제된 도구를 아직 들고 있습니다.",
          whenClosed: "다음에 Claude Desktop을 열면 정리됩니다.",
        });
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
  verifyPendingResolve(lensName);
}

// "지금 해결하기"로 시작한 조치가 실제로 문제를 없앴는지 끝까지 확인해준다 —
// 조치를 하고도 "이게 된 건가?" 하고 다시 진단을 눌러봐야 하면 절반만 해준 셈이다.
// 모든 흐름이 끝나면 카드를 새로 그리므로(replaceCard) 그 시점에 확인한다.
let pendingResolveVerify = null; // {lensName, checkId}

function verifyPendingResolve(replacedLensName) {
  if (!pendingResolveVerify) return;
  const { lensName, checkId } = pendingResolveVerify;
  // 다른 Lens 카드가 갱신된 것뿐이면 건드리지 않는다 — 안 그러면 A의 조치를 취소해두고
  // B를 만졌을 때 A에 대한 엉뚱한 알림이 뜬다.
  if (replacedLensName && replacedLensName !== lensName) return;
  const lens = lensDataCache[lensName];
  if (!lens || !lens.checks) return;
  pendingResolveVerify = null;

  const label = CHECK_ID_LABEL[checkId] || checkId;
  const check = lens.checks.find((c) => c.id === checkId);
  const solved = !check || ["ok", "active", "skip", "info-skip"].includes(check.status);
  showToast(solved ? `${label} — 해결됐습니다.` : `${label} — 아직 남아 있습니다.`);
}

async function resolveCheck(lensName, checkId, resolver, repairId) {
  if (!lensName) return;
  pendingResolveVerify = { lensName, checkId };
  closeDetailModal(); // 조치 화면을 가리지 않게 상세 모달은 접는다

  if (resolver === "repair") {
    await runAction("repair", lensName, repairId);
    return; // runAction이 카드를 새로 그리며 verifyPendingResolve까지 태운다
  }
  if (resolver === "register") {
    openRegisterModal(lensName);
    return;
  }
  if (resolver === "activate") {
    openActivateModal(lensName);
    return;
  }
  if (resolver === "telegram-login") {
    openTelegramLoginModal(lensName);
    return;
  }
  pendingResolveVerify = null; // 처리할 방법이 없으면 확인 예약도 취소
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

  // 이미 활성화된 사람에게 "구매하세요"는 광고일 뿐이다 — 키가 없거나 유효하지
  // 않을 때만 보여준다. 그 순간이 살 곳을 알려줄 유일한 타이밍이기도 하다.
  document.getElementById("modal-buy").hidden = !!(lens && lens.license_status === "active");

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
  if (!completed) pendingResolveVerify = null;  // 취소했으면 나중에 엉뚱한 알림이 뜨지 않게
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

  await refreshRegisterTargets(lensName);
  startRegisterTargetWatch(lensName);
}

// 선택된 체크박스 목록 — 다시 그릴 때 사용자가 방금 고른 걸 잃지 않으려고 먼저 걷어둔다.
function checkedRegisterTargets() {
  return [...document.querySelectorAll("#register-targets input[type=checkbox]:checked")].map(
    (c) => c.value
  );
}

async function refreshRegisterTargets(lensName, { keepSelection = null } = {}) {
  const container = document.getElementById("register-targets");
  const targets = await window.pywebview.api.available_targets(lensName);
  const lens = lensDataCache[lensName];
  const currentTargets = (lens && lens.targets) || [];

  container.innerHTML = targets
    .map((t) => {
      const checked = keepSelection
        ? keepSelection.includes(t.id)
        : t.installed && (t.id !== "codex" || currentTargets.includes(t.id));
      // 아직 없는 앱은 등록해봐야 읽어갈 주체가 없다 — 막기만 하지 말고 받는 곳을
      // 바로 열 수 있게 해준다(없는 게 잘못이 아니라 다음 할 일을 알려주는 것).
      const getItHtml = t.installed
        ? ""
        : `<button type="button" class="target-install-link" data-install-url="${escapeAttr(t.install_url)}">받으러 가기</button>`;
      return `
        <label class="register-target-row${t.installed ? "" : " disabled"}">
          <input type="checkbox" value="${t.id}" ${t.installed ? "" : "disabled"} ${checked && t.installed ? "checked" : ""}>
          <span>${escapeHtml(t.label)}${t.installed ? "" : " — 아직 설치 안 됨"}</span>
          ${getItHtml}
        </label>`;
    })
    .join("");

  container.querySelectorAll(".target-install-link").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault(); // label 안이라 클릭이 체크박스로 새지 않게
      window.pywebview.api.open_url(btn.dataset.installUrl);
      const msgEl = document.getElementById("register-msg");
      msgEl.textContent = "설치가 끝나면 여기서 자동으로 확인합니다 — 창은 열어두세요.";
      msgEl.className = "modal-msg";
    });
  });

  return targets;
}

// "받으러 가기"로 앱을 받아 설치하고 돌아와도, 체크박스가 계속 잠겨 있어 더 진행할
// 수가 없었다 — 목록을 모달을 열 때 딱 한 번만 만들었기 때문이다. 창이 열려 있는
// 동안 빠진 앱이 생겼는지 계속 지켜보고, 확인되면 그 자리에서 풀어준다.
let registerTargetWatch = null;

function startRegisterTargetWatch(lensName) {
  stopRegisterTargetWatch();
  registerTargetWatch = setInterval(async () => {
    // 창이 닫혔으면 볼 이유가 없다(닫기 경로를 못 탄 경우까지 여기서 정리된다).
    if (document.getElementById("register-backdrop").hidden) {
      stopRegisterTargetWatch();
      return;
    }
    const missingBefore = [...document.querySelectorAll("#register-targets input[disabled]")].map(
      (c) => c.value
    );
    if (!missingBefore.length) {
      stopRegisterTargetWatch();
      return;
    }
    let targets;
    try {
      // 방금 고른 체크는 그대로 두고 다시 그린다.
      targets = await refreshRegisterTargets(lensName, { keepSelection: checkedRegisterTargets() });
    } catch {
      return; // 일시적인 실패는 다음 차례에 다시 본다
    }
    const nowInstalled = targets.filter((t) => t.installed && missingBefore.includes(t.id));
    if (!nowInstalled.length) return;

    // 방금 확인된 앱은 알아서 체크해준다 — 받으러 간 이유가 그것이므로.
    nowInstalled.forEach((t) => {
      const box = document.querySelector(`#register-targets input[value="${t.id}"]`);
      if (box) box.checked = true;
    });
    const msgEl = document.getElementById("register-msg");
    msgEl.textContent = `${nowInstalled.map((t) => t.label).join(", ")} 설치를 확인했습니다 — '등록'을 눌러 계속하세요.`;
    msgEl.className = "modal-msg ok";
  }, 3000);
}

function stopRegisterTargetWatch() {
  if (registerTargetWatch !== null) {
    clearInterval(registerTargetWatch);
    registerTargetWatch = null;
  }
}

function closeRegisterModal(completed = false) {
  document.getElementById("register-backdrop").hidden = true;
  stopRegisterTargetWatch(); // 창이 닫혔는데 3초마다 계속 확인할 이유가 없다
  registerTargetLens = null;
  if (!completed) pendingResolveVerify = null;
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
    //
    // 예전엔 "Claude Desktop이 켜져 있나"만 보고 안내했다 — Codex만 골라 등록해도
    // Claude를 껐다 켜라고 해서, 시킨 대로 해도 아무 변화가 없었다. 방금 등록한
    // 대상에 대해서만 말한다.
    let claudeRunning = false;
    if (!onboardingActive && checked.includes("claude-desktop")) {
      try {
        claudeRunning = await window.pywebview.api.claude_desktop_running();
      } catch {
        /* 확인 실패 시 조용히 생략 */
      }
    }

    // Claude Code CLI와 Codex는 시작할 때 설정을 읽으므로 재시작할 대상이 따로 없다 —
    // 다음에 새로 여는 대화부터 반영된다. "껐다 켜라"고 하면 뭘 끄라는 건지 알 수 없다.
    const sessionTargets = checked
      .filter((t) => t === "claude-code" || t === "codex")
      .map((t) => TARGET_LABEL[t] || t);
    const notes = [];
    if (claudeRunning) notes.push("Claude Desktop을 껐다 켜야 도구가 나타납니다.");
    else if (!onboardingActive && checked.includes("claude-desktop")) {
      notes.push("Claude Desktop을 열면 도구가 나타납니다.");
    }
    if (!onboardingActive && sessionTargets.length) {
      notes.push(`${sessionTargets.join(", ")}는 새로 시작하는 대화부터 반영됩니다.`);
    }
    // 카드에서 직접 등록한 경우엔 마법사 완료 화면을 안 보므로 여기서 같이 알려준다 —
    // 처음 도구를 쓸 때 뜨는 허용 창에서 겁먹고 멈추지 않게.
    if (notes.length) {
      notes.push("처음 도구를 쓸 때 허용 여부를 물어봅니다 — 한 번 허용하면 다시 묻지 않습니다.");
    }

    // 등록만으로는 도구가 안 도는 경우를 말해준다.
    //
    // 실사용에서 나온 혼란: Codex에 등록한 뒤 "DART 인증키가 등록 안 됐다"는 말을
    // 들었다. 실제로는 키가 정상이었고 — 키는 설정 파일이 아니라 OS 자격 증명
    // 저장소에 들어가므로 설정 파일만 보면 없어 보인다 — 아무도 그 사실을 말해주지
    // 않은 게 문제였다. CLI는 "이미 등록된 키를 재사용합니다"라고 알려주는데
    // 매니저는 "등록 완료"만 하고 끝났다.
    //
    // 더 나쁜 쪽은 키가 정말 없을 때다. 등록은 "완료"라고 뜨는데 도구는 안 돈다.
    // 지금 상태를 그대로 말해주면 둘 다 사라진다.
    const credentialNote = credentialStatusNote(lens);
    if (credentialNote) notes.push(credentialNote);

    // 체크를 푼 곳은 실제로 해제된다 — 그걸 말해주지 않으면 정말 지워졌는지 알 수 없다.
    const removedLabels = (result.removed || []).map((t) => TARGET_LABEL[t] || t);
    const headline = removedLabels.length
      ? `${removedLabels.join(", ")} 등록을 해제했습니다.`
      : "등록 완료.";
    msgEl.textContent = notes.length ? `${headline.replace(/\.$/, "")} — ${notes.join(" ")}` : headline;
    msgEl.className = "modal-msg ok";
    const needsRestartNote = claudeRunning;
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
  if (!completed) pendingResolveVerify = null;
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
  if (action === "resolve-check") {
    resolveCheck(btn.dataset.lens, btn.dataset.checkId, btn.dataset.resolver, btn.dataset.repairId);
    return;
  }
  if (action === "uninstall") {
    openUninstallModal(lensName);
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

// 모달은 열어둔 채로 브라우저만 띄운다 — 결제하고 메일로 받은 키를 바로 여기 붙여넣게.
document.getElementById("modal-buy-btn").addEventListener("click", async () => {
  await window.pywebview.api.open_purchase_page();
  showToast("브라우저에서 구매 페이지를 열었습니다.");
});
document.getElementById("modal-confirm").addEventListener("click", confirmActivate);
document.getElementById("modal-apikey-reopen-signup").addEventListener("click", () => {
  window.pywebview.api.open_dart_api_signup();
});
document.getElementById("modal-key-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") confirmActivate();
  if (e.key === "Escape") closeActivateModal();
});
// "진단 재실행"은 Lens만 다시 보고 매니저 자신은 안 봤다 — 켠 채로 오래 두면 그 사이
// 새 버전이 나와도 다시 켜기 전까지 업데이트 버튼이 안 떴다. 여기서 같이 확인한다.
document.getElementById("refresh-btn").addEventListener("click", async () => {
  await loadDiagnosis();
  try {
    await checkSelfUpdate();
  } catch {
    /* 업데이트 확인 실패는 조용히 — 진단 결과는 이미 갱신됐다 */
  }
});

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

  const statusEl = document.getElementById("support-status");
  try {
    const info = await window.pywebview.api.create_support_bundle();

    // 만들지도 못했으면 왜 그런지 알려준다 — 도움이 필요한 사람이 도움을 요청할
    // 방법까지 잃으면 안 되니, 메일 주소는 그대로 띄워준다.
    if (info.ok === false) {
      statusEl.textContent = `파일을 만들지 못했습니다. ${info.error || ""}\n아래 주소로 이 문구와 함께 메일 주세요.`;
      statusEl.className = "modal-msg fail";
      document.getElementById("support-to").textContent = info.to || "";
      return;
    }

    supportInfo = info;
    // 폴더가 실제로 열렸을 때만 열렸다고 한다. 못 열었으면 어디 있는지 알려줘야
    // 사용자가 열리지도 않은 창을 찾지 않는다(맥에서 권한 때문에 막힐 수 있다).
    statusEl.textContent = info.revealed
      ? "폴더가 열렸습니다 — zip 파일을 첨부해 보내주세요."
      : `아래 파일을 첨부해 보내주세요.\n${info.zip_path}`;
    statusEl.className = "modal-msg ok";
    document.getElementById("support-to").textContent = info.to;
    document.getElementById("support-subject").textContent = info.subject;
    document.getElementById("support-body").textContent = info.body;
  } catch (e) {
    statusEl.textContent = `번들을 만들지 못했습니다. ${(e && e.message) || ""}`.trim();
    statusEl.className = "modal-msg fail";
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

// desc의 줄바꿈은 그대로 화면에 나온다(style.css의 .tour-desc가 white-space: pre-line).
// 예전엔 전부 한 문단짜리 줄글이라 300px 폭에서 예닐곱 줄로 뭉쳐 읽혔다 — 주 고객층
// 기준으로는 그 시점에 그냥 닫힌다. 한 줄에 한 가지씩, 목록은 목록으로 끊어 쓴다.
const TOUR_STEPS = [
  {
    selector: "#readout",
    title: "전체 상태 요약",
    desc: "몇 개 Lens가 정상인지 한눈에 보여줍니다.\n업데이트나 조치가 필요하면 여기 같이 표시됩니다.",
  },
  {
    selector: ".card:first-child .focus-ring",
    title: "상태 표시등",
    desc: "원 색깔로 상태를 보여줍니다.\n\n틸색(가득 참) — 정상\n주황 — 주의\n빨강 — 조치 필요\n회색 — 아직 설치 안 됨",
  },
  {
    selector: ".card:first-child .field-list",
    title: "세부 정보",
    desc: "이 Lens의 상태를 항목별로 보여줍니다.\n\n업데이트가 있는지\n라이선스가 활성화됐는지\nMCP에 등록됐는지\n마지막으로 진단한 시각",
  },
  {
    selector: ".card:first-child",
    title: "문제 자세히 보기",
    // "명령어를 누르면 복사된다"고 안내하고 있었는데, 지금은 누르면 그 자리에서
    // 실행되고 끝나면 자동으로 다시 진단한다 — 기능이 바뀐 뒤 설명이 안 따라왔었다.
    desc:
      "문제가 있으면 카드에 \"문제 N건\" 블록이 나타나고,\n무엇이 걸렸는지 옆에 같이 보입니다.\n" +
      "누르면 자세한 내용을 따로 창으로 보여줍니다.\n\n" +
      "지금 이 Lens는 문제가 없어 그 블록이 안 보이지만,\n예시로 그 창을 띄워드릴게요.\n\n" +
      "\"조치\" 옆의 굵은 명령어를 누르면 그 자리에서 실행되고,\n끝나면 해결됐는지 자동으로 다시 진단합니다.",
    demo: "detail",
  },
  {
    selector: ".card:first-child .actions",
    title: "동작 버튼",
    desc:
      "진단 — 지금 상태를 다시 검사합니다\n" +
      // 앱 이름을 여기 다 늘어놓으면 한 줄을 넘겨 접히고, 접힌 줄이 다음 항목처럼
      // 보여 목록 모양이 무너진다 — 어떤 앱이 있는지는 누르면 나오는 모달이 보여준다.
      "MCP 등록 — 어떤 앱에 연결할지 고릅니다\n" +
      "활성화 — 라이선스 키를 넣습니다\n" +
      "복구 — 발견된 문제를 자동으로 고칩니다\n\n" +
      // 아래 넷은 상황·Lens에 따라서만 나타난다. 특히 삭제는 빨간 버튼인데 설명이
      // 아예 없어서, 가이드를 다 본 사람도 그게 뭘 지우는지 모르는 채로 남았다.
      "상황에 따라 더 나타나는 버튼도 있습니다.\n\n" +
      "설치 / 업데이트 — 아직 없거나 새 버전이 있을 때\n" +
      "텔레그램 로그인 — TelegramLens 카드에만\n" +
      "삭제 — 이 Lens를 지웁니다(빨간 버튼).\n" +
      "받은 라이선스 키는 그대로라 다시 설치할 수 있습니다.\n\n" +
      "결과 복사는 \"문제 자세히\" 창 안에 있습니다.",
  },
  // 상단바 버튼은 여기서부터 왼→오른쪽이 아니라 "자주 쓰는 순"으로 설명한다.
  // 예전엔 지원 문의·매니저 업데이트·가이드 셋만 있어서, 정작 제일 많이 누르는
  // 진단 재실행과 문제 해결의 핵심인 Claude 다시 시작이 설명 없이 놓여 있었다.
  {
    selector: "#refresh-btn",
    title: "진단 재실행",
    desc:
      "지금 상태를 처음부터 다시 검사합니다.\n" +
      "Lens를 설치하거나 키를 넣은 뒤 눌러 확인하세요.\n\n" +
      "새 버전이 나왔는지도 같이 확인해서,\n있으면 위 버튼들에 업데이트 표시가 뜹니다.",
  },
  {
    selector: "#restart-claude-btn",
    // 이 버튼은 한 자리에서 두 가지로 바뀐다 — Claude Desktop이 없으면 "받기".
    // 설명이 "다시 시작" 하나로 고정돼 있으면, 정작 아직 안 깐 사람에게 엉뚱한
    // 말을 하게 된다(Lens는 Claude 위에서만 도니 그 사람에겐 이게 더 중요하다).
    title: (el) => (el.textContent.includes("받기") ? "Claude Desktop 받기" : "Claude 다시 시작"),
    desc: (el) =>
      el.textContent.includes("받기")
        ? "Lens는 Claude Desktop 안에서 동작합니다.\n아직 없으시면 여기를 눌러 받으세요.\n\n" +
          "설치가 끝나면 \"진단 재실행\"을 눌러주세요.\n그러면 이 버튼이 \"Claude 다시 시작\"으로 바뀝니다."
        : "Claude Desktop은 켜질 때 설정을 읽고 Lens를 띄웁니다.\n" +
          "그래서 켜져 있는 동안에 바꾼 것은 그대로 반영되지 않습니다.\n\n" +
          "MCP 등록을 바꿨을 때, Lens를 업데이트·삭제했을 때\n이 버튼을 눌러주세요.\n\n" +
          "\"등록은 됐다는데 도구가 안 보인다\",\n\"업데이트했는데 그대로다\" —\n대부분 여기를 누르면 해결됩니다.",
    requiresVisible: true,
  },
  {
    selector: "#patchnotes-btn",
    title: "패치노트",
    desc: "업데이트마다 무엇이 바뀌었는지 적어둔 페이지를 엽니다.",
  },
  {
    selector: "#support-btn",
    title: "지원 문의",
    desc:
      "문제가 안 풀리면 여기를 눌러보세요.\n로그를 모아 zip으로 만들고 그 폴더를 열어줍니다.\n\n" +
      "뜨는 창의 받는사람, 제목, 내용을 복사해\n메일에 붙여넣고 zip을 첨부해서 보내시면 됩니다.",
  },
  {
    selector: "#self-update-btn",
    // 이 버튼은 새 버전이 있을 때만 나타난다 — requiresVisible로 두면 최신인 사람은
    // 이 단계를 통째로 못 본다. 즉 "평소에는 아무도 설명을 못 받는" 단계였다.
    // 안 보일 때는 버튼들이 모인 자리를 대신 가리키고, 어디에 생기는지 알려준다.
    fallbackSelector: ".topbar-actions",
    title: "매니저 업데이트",
    desc: (el, usedFallback) =>
      // 예전엔 "닫히니 바탕화면 아이콘으로 다시 실행하세요"라고 안내했는데,
      // 지금은 새 버전이 스스로 뜬다 — 동작이 바뀐 뒤 설명이 안 따라왔었다.
      (usedFallback
        ? "LeetKit Manager 자체에 새 버전이 나오면\n이 줄 맨 앞에 \"업데이트\" 버튼이 생깁니다.\n" +
          "지금은 최신이라 안 보입니다.\n\n"
        : "LeetKit Manager 자체의 새 버전이 있을 때만 나타납니다.\n\n") +
      "누르면 설치한 뒤 앱이 잠깐 닫혔다가\n새 버전으로 다시 열립니다. 그대로 기다리시면 됩니다.\n\n" +
      "Lens 업데이트와는 별개입니다 — 그쪽은 각 카드에서 합니다.",
  },
  {
    selector: "#guide-btn",
    title: "가이드 다시 보기",
    desc: "이 설명은 여기 버튼을 눌러 언제든 다시 볼 수 있습니다.",
  },
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

// 설명 상자를 놓을 자리 — 아래 → 위 → 오른쪽 → 왼쪽 순으로 보고, 화면 안에 들어가면서
// **가리키는 대상을 안 덮는** 첫 자리를 쓴다.
//
// 예전엔 아래·위 둘만 보고, 둘 다 안 되면 화면 안으로 밀어넣기만 했다. 그래서 설명이
// 긴 단계에서 상자가 대상 위에 그대로 얹혔다 — "동작 버튼" 단계가 기본 창(1180x820)에서
// 정확히 그랬다(상자 494px, 아래로 두면 화면을 넘고, 위로 뒤집으면 음수라 10으로 강제,
// 결국 설명하려던 버튼 줄을 69% 덮음). 가리키면서 가리는 셈이라 제일 나쁜 모양이다.
//
// 옆자리는 세로로 안 되는 경우를 거의 다 흡수한다 — 상자가 360px이고 창은 최소 1040px이다.
function placeTooltip(rect, tw, th, pad) {
  const gap = 12; // 대상과 상자 사이 숨 쉴 틈
  const edge = 10; // 화면 가장자리 여백
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, hi));
  const maxLeft = window.innerWidth - tw - edge;
  const maxTop = window.innerHeight - th - edge;

  const candidates = [
    { top: rect.bottom + pad + gap, left: clamp(rect.left, edge, maxLeft) },
    { top: rect.top - pad - gap - th, left: clamp(rect.left, edge, maxLeft) },
    { left: rect.right + pad + gap, top: clamp(rect.top, edge, maxTop) },
    { left: rect.left - pad - gap - tw, top: clamp(rect.top, edge, maxTop) },
  ];

  const onScreen = (c) =>
    c.top >= edge && c.left >= edge && c.top + th <= window.innerHeight - edge && c.left + tw <= window.innerWidth - edge;
  const coveredArea = (c) => {
    const ox = Math.max(0, Math.min(c.left + tw, rect.right + pad) - Math.max(c.left, rect.left - pad));
    const oy = Math.max(0, Math.min(c.top + th, rect.bottom + pad) - Math.max(c.top, rect.top - pad));
    return ox * oy;
  };

  for (const c of candidates) {
    if (onScreen(c) && coveredArea(c) === 0) return c;
  }
  // 어디에도 온전히 안 들어가면(대상이 화면을 거의 다 차지하는 경우) 화면 안으로
  // 넣되 가장 덜 가리는 자리를 고른다. 아무것도 안 하고 대상 위에 얹는 것보단 낫다.
  const fallback = candidates
    .map((c) => ({ top: clamp(c.top, edge, Math.max(edge, maxTop)), left: clamp(c.left, edge, Math.max(edge, maxLeft)) }))
    .sort((a, b) => coveredArea(a) - coveredArea(b));
  return fallback[0];
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
    const spot = placeTooltip(rect, tw, th, pad);
    tooltip.style.top = `${spot.top}px`;
    tooltip.style.left = `${spot.left}px`;
  }

  step.el.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// 화면에 실제로 자리를 차지하고 있는지. hidden 속성만 보면 부모가 숨겨진 경우를 놓친다.
function isOnScreen(el) {
  return !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
}

// title·desc는 문자열이거나 (el, usedFallback) => 문자열이다. 버튼 하나가 상황에 따라
// 다른 것을 뜻할 때(예: "Claude 다시 시작" / "Claude Desktop 받기") 설명도 같이
// 바뀌어야 해서 — 고정 문구로 두면 둘 중 한쪽에겐 틀린 말을 하게 된다.
function resolveStepText(value, el, usedFallback) {
  // el이 없는 단계는 어차피 아래에서 걸러진다 — 여기서 함수를 부르면 그 전에 터진다.
  if (typeof value !== "function") return value;
  return el ? value(el, usedFallback) : "";
}

function startTour() {
  tourSteps = TOUR_STEPS.map((s) => {
    let el = document.querySelector(s.selector);
    let usedFallback = false;
    // 가리킬 대상이 지금 안 보이면, 대신 가리킬 자리를 준 단계는 그 자리로 넘어간다.
    // 안 그러면 "새 버전이 있을 때만 나타나는 버튼"처럼, 정작 평소에는 아무도
    // 설명을 못 받는 단계가 생긴다.
    if (!isOnScreen(el) && s.fallbackSelector) {
      const fallback = document.querySelector(s.fallbackSelector);
      if (isOnScreen(fallback)) {
        el = fallback;
        usedFallback = true;
      }
    }
    return {
      ...s,
      el,
      usedFallback,
      title: resolveStepText(s.title, el, usedFallback),
      desc: resolveStepText(s.desc, el, usedFallback),
    };
  }).filter((s) => s.el && (s.usedFallback || !s.requiresVisible || !s.el.hidden));
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
/* ---------- 패치노트 ---------- */

// checkSelfUpdate가 채운다 — 패치노트에서 "어디까지가 이미 쓰고 있는 버전인지"를
// 가르는 기준이라, 화면의 v표시(#manager-version)와 같은 값을 따로 들고 있는다.
let managerVersion = null;

// 지금 이 사람 기준으로 각 제품이 어떤 상태인지. 목록만 늘어놓으면 남의 이야기가
// 되고, "이건 이미 쓰고 계신 버전에 들어 있다 / 이건 업데이트하면 적용된다"를
// 구분해줘야 읽을 이유가 생긴다. 진단 결과를 이미 들고 있어서 여기서 판단한다.
function patchNotesState(name) {
  // label = 패널 머리말에 쓰는 자세한 표기, short = 탭에 들어가는 짧은 표기.
  // 탭은 넷이 한 줄에 들어가야 해서 "v0.6.12 → v0.6.13" 같은 건 안 들어간다.
  if (name === "leetkit-manager") {
    const hasUpdate = !document.getElementById("self-update-btn").hidden;
    if (!managerVersion) return { label: "", short: "", installed: null, update: false };
    return hasUpdate
      ? { label: `v${managerVersion} · 업데이트 있음`, short: "업데이트 있음", installed: managerVersion, update: true }
      : { label: `v${managerVersion} · 최신`, short: "최신", installed: managerVersion, update: false };
  }
  const lens = lensDataCache[name];
  if (!lens) return { label: "", short: "", installed: null, update: false };
  if (lens.not_installed) {
    return { label: "아직 설치 안 함", short: "미설치", installed: null, update: false };
  }
  if (lens.update_available && lens.latest_version) {
    return {
      label: `v${lens.installed_version} → v${lens.latest_version}`,
      short: "업데이트 있음",
      installed: lens.installed_version,
      update: true,
    };
  }
  return {
    label: `v${lens.installed_version} · 최신`,
    short: "최신",
    installed: lens.installed_version,
    update: false,
  };
}

// 지금 고른 제품. 모달을 다시 열면 처음부터 — 지난번에 뭘 보고 있었는지는
// 기억할 가치가 없고, 기억하면 "업데이트 있는 제품 먼저"라는 기본값을 덮어버린다.
let patchNotesProducts = [];
let patchNotesSelected = null;

// `2026-08-08` → `2026년 8월 8일`. 숫자와 붙임표만 있는 날짜는 훑을 때 눈에 안 걸린다.
// 형식이 다르면(사람이 손으로 쓰는 파일이다) 건드리지 않고 그대로 보여준다.
function formatPatchDate(raw) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(raw).trim());
  if (!m) return raw;
  return `${m[1]}년 ${Number(m[2])}월 ${Number(m[3])}일`;
}

function renderPatchNotesTabs() {
  document.getElementById("patchnotes-tabs").innerHTML = patchNotesProducts
    .map((p) => {
      const state = patchNotesState(p.name);
      const selected = p.name === patchNotesSelected;
      return `
      <button class="patchnotes-tab${selected ? " selected" : ""}" data-product="${escapeAttr(p.name)}">
        <span class="patchnotes-tab-name">${escapeHtml(p.display_name)}</span>
        <span class="patchnotes-tab-state${state.update ? " update" : ""}">${escapeHtml(state.short)}</span>
      </button>`;
    })
    .join("");
}

function renderPatchNotesPanel() {
  const product = patchNotesProducts.find((p) => p.name === patchNotesSelected);
  const panel = document.getElementById("patchnotes-panel");
  if (!product) {
    panel.innerHTML = "";
    return;
  }
  const state = patchNotesState(product.name);
  if (!product.entries.length) {
    panel.innerHTML = `<div class="patchnotes-empty">아직 기록된 변경사항이 없습니다.</div>`;
    return;
  }

  // 쓰고 있는 버전보다 위(=아직 안 받은 것)와 아래(=이미 쓰는 것)를 선으로 가른다.
  // 이 선 하나가 "그래서 업데이트하면 뭐가 생기는데?"에 바로 답한다 — 없으면
  // 버전 번호를 하나하나 자기 것과 대조해봐야 한다.
  let dividerPlaced = false;
  const rows = product.entries.map((e) => {
    const pending = state.installed && versionGreater(e.version, state.installed);
    let divider = "";
    if (!pending && !dividerPlaced && state.installed) {
      dividerPlaced = true;
      // 첫 항목부터 이미 쓰는 버전이면 위에 새로 받을 게 없다는 뜻이라 선을 안 긋는다.
      if (product.entries.indexOf(e) > 0) {
        divider = `
        <div class="patchnotes-divider">
          <span>여기까지 쓰고 계십니다 · v${escapeHtml(state.installed)}</span>
        </div>`;
      }
    }
    // **굵게**를 살린다 — 마크다운 파일이라 쓰는 사람이 자연스럽게 쓰는 표기다.
    // renderEmphasis가 escapeHtml을 먼저 통과시키므로 태그는 글자로만 남는다.
    const items = e.items.map((t) => `<li>${renderEmphasis(t)}</li>`).join("");
    return `${divider}
      <div class="patchnotes-entry${pending ? " pending" : ""}">
        <div class="patchnotes-entry-head">
          <span class="patchnotes-version">v${escapeHtml(e.version)}</span>
          <span class="patchnotes-date">${escapeHtml(formatPatchDate(e.date))}</span>
          ${pending ? `<span class="patchnotes-badge">업데이트하면 적용</span>` : ""}
        </div>
        <ul class="patchnotes-items">${items}</ul>
        ${e.note ? `<div class="patchnotes-note">${renderEmphasis(e.note)}</div>` : ""}
      </div>`;
  });

  panel.innerHTML = `
    <div class="patchnotes-panel-head">
      <span class="patchnotes-name">${escapeHtml(product.display_name)}</span>
      <span class="patchnotes-state${state.update ? " update" : ""}">${escapeHtml(state.label)}</span>
    </div>
    ${rows.join("")}`;
  panel.scrollTop = 0; // 제품을 바꾸면 항상 맨 위부터 — 이전 제품의 스크롤 위치가 남으면 빈 화면처럼 보인다
}

function renderPatchNotes(products) {
  patchNotesProducts = products;
  // 업데이트가 있는 제품을 먼저 보여준다 — 지금 이 사람이 알고 싶은 게 그거다.
  const withUpdate = products.find((p) => p.entries.length && patchNotesState(p.name).update);
  const withEntries = products.find((p) => p.entries.length);
  patchNotesSelected = (withUpdate || withEntries || products[0]).name;
  renderPatchNotesTabs();
  renderPatchNotesPanel();
}

document.getElementById("patchnotes-tabs").addEventListener("click", (e) => {
  const tab = e.target.closest("[data-product]");
  if (!tab || tab.dataset.product === patchNotesSelected) return;
  patchNotesSelected = tab.dataset.product;
  renderPatchNotesTabs();
  renderPatchNotesPanel();
});

// a > b ? 숫자로만 비교한다 — 여기서 틀려도 배지 하나가 잘못 붙을 뿐이라
// package_service.version_gt처럼 엄밀할 필요는 없다. 못 읽으면 안 붙인다.
function versionGreater(a, b) {
  const parse = (v) => String(v).split(/[.\-+]/).map((n) => parseInt(n, 10));
  const x = parse(a);
  const y = parse(b);
  if (x.some(isNaN) || y.some(isNaN)) return false;
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] || 0) - (y[i] || 0);
    if (d !== 0) return d > 0;
  }
  return false;
}

async function openPatchNotes() {
  const panel = document.getElementById("patchnotes-panel");
  document.getElementById("patchnotes-tabs").innerHTML = "";
  panel.innerHTML = `<div class="patchnotes-empty">불러오는 중…</div>`;
  document.getElementById("patchnotes-backdrop").hidden = false;
  let products = [];
  try {
    products = await window.pywebview.api.patch_notes();
  } catch {
    /* 아래에서 안내한다 */
  }
  // 넷 다 비어 있으면 네트워크 문제다 — 빈 상자를 보여주면 고장으로 읽힌다.
  if (!products.length || products.every((p) => !p.entries.length)) {
    // 예전엔 여기서 없는 변수(body)를 써서 ReferenceError가 났다 — 못 불러왔다는
    // 안내 대신 "불러오는 중…"인 채로 창이 굳었고, 그 창이 화면을 덮고 있어서
    // 뒤에 있던 모달의 버튼까지 안 눌리는 것처럼 보였다.
    panel.innerHTML = `<div class="patchnotes-empty">패치노트를 불러오지 못했습니다.\n인터넷 연결을 확인하고 다시 눌러주세요.</div>`;
    return;
  }
  renderPatchNotes(products);
}

document.getElementById("patchnotes-btn").addEventListener("click", openPatchNotes);

document.getElementById("patchnotes-close").addEventListener("click", () => {
  document.getElementById("patchnotes-backdrop").hidden = true;
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

// 처음 업데이트하는 사람에게 "왜 껐다 켜야 하는지"를 한 번만 설명한다. 매번 붙이면
// 아는 사람에겐 잔소리이고, 한 번도 안 하면 시킨 대로 눌러놓고 이유를 모른 채 남는다.
const RESTART_EXPLAINED_KEY = "leetkit-manager-restart-explained";

// Lens 파일을 바꾼 뒤(업데이트·삭제) Claude Desktop이 켜져 있으면 재시작을 권한다.
// 띄웠으면 true — 안 켜져 있으면 지금 다시 시작할 대상이 없으므로 false를 주고,
// 호출한 쪽이 그 상황에 맞는 안내를 대신 한다("다음에 열면 적용됩니다").
//
// 토스트로 하지 않는 이유: 안 하면 새 버전이 적용되지 않는 **필수** 단계인데,
// 토스트는 몇 초 뒤 사라지고 누를 것도 없다. 실제로 이 한 걸음을 건너뛴 채
// "업데이트했는데 그대로다"라고 느끼기 딱 좋은 자리다.
async function offerClaudeRestart(bodyText) {
  let running = false;
  try {
    running = await window.pywebview.api.claude_desktop_running();
  } catch {
    return false; // 확인 실패 — 확실하지 않은 걸 시키지 않는다
  }
  if (!running) return false;

  document.getElementById("restart-body").textContent = bodyText;

  const whyEl = document.getElementById("restart-why");
  const alreadyExplained = localStorage.getItem(RESTART_EXPLAINED_KEY);
  if (alreadyExplained) {
    whyEl.hidden = true;
  } else {
    whyEl.textContent =
      "처음이시니 한 번만 설명드릴게요.\n\n" +
      "Claude Desktop은 켜질 때 Lens 프로그램을 같이 띄웁니다. " +
      "그래서 새 파일을 받아도, 이미 떠 있는 쪽은 받기 전 버전 그대로 돌아갑니다.\n\n" +
      "껐다 켜면 새로 받은 파일로 다시 뜹니다. 대화 내용은 지워지지 않습니다.";
    whyEl.hidden = false;
    localStorage.setItem(RESTART_EXPLAINED_KEY, "1");
  }

  document.getElementById("restart-backdrop").hidden = false;
  return true;
}

function closeRestartModal() {
  document.getElementById("restart-backdrop").hidden = true;
  // 이 모달 때문에 물러났던 후기 요청을 이어붙인다(maybeShowReviewPrompt는 다른
  // 모달이 떠 있으면 물러난다) — 일괄 업데이트 끝에 둘이 겹치는 경로가 있다.
  handOffToReviewPrompt();
}

document.getElementById("restart-later").addEventListener("click", () => {
  closeRestartModal();
  showToast("Claude Desktop을 껐다 켜면 적용됩니다 — 위 \"Claude 다시 시작\" 버튼으로도 됩니다.");
});

document.getElementById("restart-now").addEventListener("click", async (e) => {
  await restartClaudeDesktop(e.currentTarget);
  closeRestartModal();
});

// Lens를 업데이트·삭제한 뒤 부른다. Claude Desktop에 등록돼 있지 않으면 지금 다시
// 읽어갈 쪽이 없으므로 아무 말도 하지 않는다(신규 설치 직후가 그렇다 — 그쪽은
// MCP 등록 모달이 따로 안내한다).
async function noteLensFilesChanged({ registeredOnClaudeDesktop, headline, afterRestart, whenClosed }) {
  if (!registeredOnClaudeDesktop) {
    showToast(headline);
    return;
  }
  const shown = await offerClaudeRestart(
    `${headline}\n\n지금 켜져 있는 Claude Desktop은 ${afterRestart}\n껐다 켜야 반영됩니다.`
  );
  // 안 켜져 있으면 시킬 일이 없다 — 다음에 열면 알아서 적용된다는 사실만 알려준다.
  if (!shown) showToast(`${headline} ${whenClosed}`);
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

  // 처음 도구를 쓸 때 Claude가 "허용하시겠습니까?"를 묻는다. 미리 말해두지 않으면
  // 설치가 잘못됐거나 위험한 걸 깐 줄 알고 거기서 멈춘다 — 한 번 허용하면 다시
  // 안 묻는다는 것까지 같이 알려줘야 안심하고 누른다.
  const permissionNote =
    " 처음 도구를 쓸 때 Claude가 허용 여부를 물어봅니다 — 한 번 허용하면 다시 묻지 않습니다.";

  if (claudeRunning) {
    onboardingSetBanner(
      "설정이 끝났습니다! 마지막으로 Claude Desktop을 껐다 켜야 도구가 나타납니다." + telegramNote + permissionNote,
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
    "모든 설정이 끝났습니다! Claude Desktop을 열어주세요." + telegramNote + permissionNote,
    `<button class="action-btn primary" id="onboarding-done-btn">알겠습니다</button>`
  );
  document.getElementById("onboarding-done-btn").addEventListener("click", () => {
    onboardingHideBanner();
    showToast("설정은 언제든 각 카드에서 다시 바꿀 수 있습니다.");
  });
}

/* ---------- 기간 종료 안내 ---------- */

// 체험이 끝난 그 순간이 사는 사람과 떠나는 사람이 갈리는 자리다. 다만 여기서
// 하지 않기로 한 것들이 있다 — 가짜 마감("오늘까지 할인"), 죄책감("이만큼 써놓고"),
// 데이터 인질("구매 안 하면 지웁니다"). 셋 다 한 번 들키면 그 뒤 모든 문구가
// 의심받고, 우리가 파는 게 "믿을 수 있는 데이터"라 그 손해가 특히 크다.
//
// 대신 사실만 쓴다: (1) 본인이 체험 기간에 쌓은 기록, (2) 그게 지워지지 않고 그대로
// 남는다는 것, (3) 사는 곳. 남의 광고 문구보다 자기가 만든 숫자가 훨씬 세다.
const EXPIRED_SHOWN_KEY = "leetkit-manager-expired-shown";

function expiredLenses() {
  return Object.values(lensDataCache).filter((l) => l.license_status === "expired");
}

function closeExpiredModal() {
  document.getElementById("expired-backdrop").hidden = true;
}

function renderExpiredUsage(usage) {
  const box = document.getElementById("expired-usage");
  const rows = (usage || []).filter((u) => u.value);
  if (!rows.length) {
    // 기록을 못 읽었으면 빈 상자를 보여주지 않는다 — "0건"으로 읽히면
    // "안 썼네"가 되어 오히려 반대로 설득된다.
    box.hidden = true;
    return;
  }
  box.innerHTML = rows
    .map(
      (u) => `<div class="expired-usage-row"><span>${escapeHtml(u.label)}</span>
        <span class="num">${escapeHtml(String(u.value))}</span></div>`
    )
    .join("");
  box.hidden = false;
}

async function maybeShowExpiredNotice() {
  if (!localStorage.getItem(ONBOARDING_DONE_KEY)) return;
  if (document.querySelector(".modal-backdrop:not([hidden])")) return;

  const expired = expiredLenses();
  if (!expired.length) return;

  // 같은 조합으로 한 번만. 켤 때마다 같은 말을 반복하면 안내가 아니라 압박이 된다.
  const signature = expired.map((l) => l.name).sort().join(",");
  if (localStorage.getItem(EXPIRED_SHOWN_KEY) === signature) return;
  localStorage.setItem(EXPIRED_SHOWN_KEY, signature);

  const names = expired.map((l) => l.display_name).join(", ");
  document.getElementById("expired-title").textContent = `${names} 사용 기간이 끝났습니다`;
  document.getElementById("expired-lead").textContent =
    "써보시는 동안 도움이 되셨길 바랍니다.\n계속 쓰시려면 라이선스를 구매하시면 됩니다.";

  let usage = [];
  try {
    usage = await window.pywebview.api.trial_usage();
  } catch {
    /* 못 읽으면 그냥 안 보여준다 — 없는 숫자를 지어내지 않는다 */
  }
  renderExpiredUsage(usage);

  document.getElementById("expired-keep").textContent =
    "설정과 모아둔 데이터는 그대로 있습니다. 구매하신 키를 넣으시면 이어서 쓰실 수 있고, 처음부터 다시 하실 필요는 없습니다.";

  document.getElementById("expired-backdrop").hidden = false;
}

document.getElementById("expired-later").addEventListener("click", closeExpiredModal);

// 이미 산 사람이 여기서 막히면 안 된다 — 결제하고 온 사람이 가장 먼저 찾는 버튼이다.
document.getElementById("expired-have-key").addEventListener("click", () => {
  const target = expiredLenses()[0];
  closeExpiredModal();
  if (target) openActivateModal(target.name);
});

// 창은 닫지 않는다 — 사고 돌아와 바로 "키가 있어요"를 누를 수 있어야 한다.
document.getElementById("expired-buy").addEventListener("click", async () => {
  await window.pywebview.api.open_purchase_page();
  showToast("브라우저에서 구매 페이지를 열었습니다.");
});

/* ---------- 삭제 확인 ---------- */

// 예전엔 브라우저 confirm() 한 줄이었다 — 라이선스를 같이 지울지 고를 수가 없어서,
// 쓰던 컴퓨터를 정리하는 사람도 키를 그대로 두고 갈 수밖에 없었다.
let uninstallTargetLens = null;

function openUninstallModal(lensName) {
  uninstallTargetLens = lensName;
  const lens = lensDataCache[lensName];
  const displayName = lens ? lens.display_name : lensName;
  document.getElementById("uninstall-title").textContent = `${displayName} 삭제`;
  document.getElementById("uninstall-msg").textContent =
    "프로그램을 지웁니다. 다시 설치하면 그대로 이어서 쓰실 수 있습니다.";
  // 기본은 남기기 — 삭제의 대부분은 "지웠다 다시 깔기"라, 매번 키를 다시 넣게 하면 안 된다.
  document.getElementById("uninstall-with-license").checked = false;
  document.getElementById("uninstall-backdrop").hidden = false;
}

function closeUninstallModal() {
  document.getElementById("uninstall-backdrop").hidden = true;
  uninstallTargetLens = null;
}

document.getElementById("uninstall-cancel").addEventListener("click", closeUninstallModal);

document.getElementById("uninstall-confirm").addEventListener("click", () => {
  const lensName = uninstallTargetLens;
  const withLicense = document.getElementById("uninstall-with-license").checked;
  closeUninstallModal();
  if (lensName) runAction("uninstall", lensName, withLicense ? "with-license" : null);
});

/* ---------- 업데이트 알림 ---------- */

// 예전엔 새 버전이 나와도 카드 안의 작은 "업데이트" 버튼 하나가 전부여서, 그게
// 있는 줄 모르면 옛 버전을 계속 썼다. 켤 때 한 번 정면으로 알린다.
const UPDATE_NOTICE_KEY = "leetkit-manager-update-notice-dismissed";
// 첫 업데이트 안내를 이미 보여줬는지. 재시작 설명(RESTART_EXPLAINED_KEY)과 따로
// 세는 이유: 카드에서 바로 업데이트한 사람은 이 모달을 안 보고 재시작 안내만 보고,
// "나중에"만 누른 사람은 반대다 — 한 키로 묶으면 한쪽이 설명을 통째로 못 받는다.
const UPDATE_EXPLAINED_KEY = "leetkit-manager-update-explained";

function lensesWithUpdates() {
  return Object.values(lensDataCache).filter(
    (l) => l.update_available && !l.not_installed && l.latest_version
  );
}

// "나중에"를 고른 조합을 기억한다. 매번 다시 띄우면 잔소리가 되고, 아예 안 띄우면
// 다음 새 버전을 놓친다 — 버전 조합이 바뀌었을 때만 다시 띄운다.
function updateNoticeSignature(lenses) {
  return lenses
    .map((l) => `${l.name}@${l.latest_version}`)
    .sort()
    .join(",");
}

function closeUpdateModal() {
  document.getElementById("update-backdrop").hidden = true;
}

function maybeShowUpdateNotice() {
  // 설치를 아직 안 끝낸 사람에게는 마법사가 먼저다 — 그 위에 겹쳐 띄우면 흐름이 끊긴다.
  if (!localStorage.getItem(ONBOARDING_DONE_KEY)) return;
  if (document.querySelector(".modal-backdrop:not([hidden])")) return;

  const lenses = lensesWithUpdates();
  if (!lenses.length) return;
  if (localStorage.getItem(UPDATE_NOTICE_KEY) === updateNoticeSignature(lenses)) return;

  document.getElementById("update-list").innerHTML = lenses
    .map(
      (l) => `
      <div class="update-row">
        <span class="name">${escapeHtml(l.display_name)}</span>
        <span class="versions">v${escapeHtml(l.installed_version || "?")} → <span class="to">v${escapeHtml(l.latest_version)}</span></span>
      </div>`
    )
    .join("");

  // 처음 업데이트하는 사람에게만 한 번 — 누르면 무슨 일이 일어나는지, 얼마나
  // 걸리는지, Claude를 건드리는지. 모르는 채로 누르는 버튼이 제일 무섭고,
  // 그래서 "나중에"만 계속 누르다 옛 버전에 머무는 일이 생긴다.
  const noteEl = document.getElementById("update-first-note");
  if (localStorage.getItem(UPDATE_EXPLAINED_KEY)) {
    noteEl.hidden = true;
  } else {
    noteEl.textContent =
      "처음이시죠? \"지금 업데이트\"를 누르면 새 파일을 받아 바꿔 끼웁니다. " +
      "Lens 하나에 1~2분쯤 걸립니다.\n\n" +
      "Claude Desktop이 켜져 있으면 잠시 껐다 켜도 될지 먼저 여쭤봅니다. " +
      "대화 내용은 지워지지 않습니다.";
    noteEl.hidden = false;
    localStorage.setItem(UPDATE_EXPLAINED_KEY, "1");
  }

  document.getElementById("update-backdrop").hidden = false;
}

document.getElementById("update-later").addEventListener("click", async () => {
  localStorage.setItem(UPDATE_NOTICE_KEY, updateNoticeSignature(lensesWithUpdates()));
  closeUpdateModal();
  await handOffToReviewPrompt();
});

// 창은 닫지 않는다 — 패치노트를 브라우저에서 읽고 돌아와 바로 "지금 업데이트"를
// 누를 수 있어야 한다.
document.getElementById("update-patchnotes").addEventListener("click", openPatchNotes);

document.getElementById("update-now").addEventListener("click", async () => {
  const lenses = lensesWithUpdates();
  closeUpdateModal();

  // Claude Desktop이 켜져 있으면 Lens 파일을 쥐고 있어 교체가 막힌다. 예전엔 Lens마다
  // 실패한 뒤에 "껐다 켤까요?"가 따로 떠서, 3개면 3번 물어봤다 — 시작 전에 한 번만
  // 묻고, 끝나면 다시 켜준다.
  const closedClaude = await closeClaudeForBulkUpdate(lenses.length);

  // 껐다 켜라는 안내가 Lens마다 따로 뜨면 3개일 때 3번이다 — 낱개 안내는 끄고
  // 아래에서 다 끝난 뒤 한 번만 말한다(물어보는 것도 한 번, 안내도 한 번).
  const onClaudeDesktop = lenses.filter((l) => (l.targets || []).includes("claude-desktop"));

  // 한 번에 하나씩 — uv tool install이 같은 디렉터리를 건드리므로 동시에 돌리면
  // 서로의 파일을 쥔 채 실패한다. runAction이 진행률 오버레이까지 맡는다.
  for (const lens of lenses) {
    await runAction("install", lens.name, undefined, { skipClaudePrompt: true, skipRestartPrompt: true });
  }
  recomputeSummaryFromCache();

  if (closedClaude) {
    showBusyOverlay("Claude Desktop을 다시 켜는 중…");
    try {
      await window.pywebview.api.launch_claude_desktop();
    } catch {
      /* 못 켜도 업데이트 자체는 끝났다 — 아래 안내에서 직접 켜라고 말한다 */
    }
    hideBusyOverlay();
  }

  const left = lensesWithUpdates();
  if (left.length) {
    showToast("일부 업데이트가 남았습니다 — 카드에서 다시 시도해주세요.");
    await handOffToReviewPrompt();
    return;
  }
  if (closedClaude) {
    // 우리가 껐다 켰으니 새 버전으로 이미 다시 떴다 — 더 시킬 게 없다.
    showToast("업데이트를 마치고 Claude Desktop을 다시 켰습니다.");
    await handOffToReviewPrompt();
    return;
  }

  // 여기까지 왔다는 건 Claude가 꺼져 있었거나("잠시 껐다 켤까요?"를 안 물어봤다),
  // 물어봤는데 사용자가 거절했다는 뜻이다. 거절한 경우 Claude는 여전히 옛 버전을
  // 돌리고 있는데, 예전엔 그냥 "업데이트를 마쳤습니다"로 끝나서 끝난 줄 알았다.
  const done = onClaudeDesktop.map((l) => l.display_name).join(", ");
  await noteLensFilesChanged({
    registeredOnClaudeDesktop: onClaudeDesktop.length > 0,
    headline: done ? `${done} 업데이트 완료.` : "업데이트를 마쳤습니다.",
    afterRestart: "아직 이전 버전을 쓰고 있습니다.",
    whenClosed: "다음에 Claude Desktop을 열면 새 버전이 적용됩니다.",
  });
  // 재시작 모달을 띄웠으면 그게 닫힐 때 이어서 후기를 묻는다(closeRestartModal).
  if (document.getElementById("restart-backdrop").hidden) await handOffToReviewPrompt();
});

// 일괄 업데이트 전에 Claude Desktop을 닫을지 한 번만 묻는다. 닫았으면 true —
// 호출자가 끝나고 다시 켜준다.
async function closeClaudeForBulkUpdate(count) {
  let running = false;
  try {
    running = await window.pywebview.api.claude_desktop_running();
  } catch {
    return false; // 확인 실패 — 괜히 묻지 말고 그냥 진행한다
  }
  if (!running) return false;

  const ok = confirm(
    `Claude Desktop이 켜져 있으면 Lens 파일을 쓰고 있어 업데이트가 막힐 수 있습니다.\n\n` +
      `잠시 껐다가 ${count}개를 업데이트하고, 끝나면 다시 켤까요?`
  );
  if (!ok) return false;

  showBusyOverlay("Claude Desktop을 종료하는 중…");
  try {
    const quit = await window.pywebview.api.quit_claude_desktop();
    if (!quit.ok) {
      hideBusyOverlay();
      showToast(quit.error || "Claude Desktop을 종료하지 못했습니다 — 그대로 진행합니다.");
      return false;
    }
    return true;
  } catch {
    hideBusyOverlay();
    return false;
  }
}

// 업데이트 알림이 물러난 자리에 후기 요청을 이어붙인다. 예전엔 업데이트가 뜬 세션에서는
// 후기가 통째로 건너뛰어졌다 — maybeShowReviewPrompt가 시작할 때 딱 한 번 돌면서
// "다른 모달이 떠 있으면 물러난다" 규칙에 걸렸기 때문이다.
//
// 곧바로 갈아끼우면 눈에는 창이 깜빡한 것처럼 보인다 — 잠깐 쉬었다 띄운다.
async function handOffToReviewPrompt() {
  await new Promise((resolve) => setTimeout(resolve, 500));
  try {
    await maybeShowReviewPrompt();
  } catch {
    /* 후기 요청은 없어도 그만인 기능 */
  }
}

/* ---------- 후기 요청 ---------- */

// 후기를 물어봐도 되는 상태인지. "라이선스가 활성인 Lens가 하나라도 있고, 그게
// 정상"이라는 건 곧 사서 설치까지 끝내고 실제로 쓰고 있다는 뜻이다. 아직 아무것도
// 못 깐 사람이나 문제를 고치는 중인 사람에게 후기를 달라고 하면 역효과고, 리틀리
// 후기란은 애초에 구매자에게만 열린다.
//
// Python 쪽도 라이선스 파일이 있는지 따로 확인한다(review_prompt.license_activated_at) —
// 그쪽은 "언제 샀나"를 알아내는 게 주목적이고, 여기서는 "지금 제대로 쓰고 있나"를 본다.
function reviewPromptReady() {
  const lenses = Object.values(lensDataCache);
  if (!lenses.length) return false;
  if (lenses.some(lensHasActionableProblem)) return false;
  return lenses.some(
    (l) => l.overall === "ok" && !l.incompatible && l.license_status === "active"
  );
}

function closeReviewModal() {
  document.getElementById("review-backdrop").hidden = true;
}

// 한 번 물어보면 Python이 그 자리에서 횟수를 세므로(review_prompt.mark_asked),
// 한 세션에서 두 번 부르면 남은 기회를 공짜로 까먹는다. 실제로 그럴 수 있는 경로가
// 생겼다 — 시작할 때 한 번, 업데이트 모달이 닫힐 때 또 한 번.
let reviewPromptAsked = false;

async function maybeShowReviewPrompt() {
  if (reviewPromptAsked) return;
  // 마법사를 아직 안 끝낸 사람은 설정 중이다 — 그 위에 후기 모달을 겹쳐 띄우면
  // 설치 흐름을 가로막는다.
  if (!localStorage.getItem(ONBOARDING_DONE_KEY)) return;
  // 다른 모달이 떠 있으면 그 위에 겹치지 않는다(자동 복구·업데이트 등). 여기서
  // 물러나도 그 모달이 닫힐 때 다시 불러주므로(handOffToReviewPrompt) 기회를 잃지 않는다.
  if (document.querySelector(".modal-backdrop:not([hidden])")) return;

  reviewPromptAsked = true;
  // 여기서 null이면 "아직 때가 아니다"이거나 원격 설정이 꺼져 있다는 뜻 — 어느 쪽이든
  // 조용히 넘어간다(Python 쪽 review_prompt.pending_prompt 참고).
  const prompt = await window.pywebview.api.review_prompt(reviewPromptReady());
  if (!prompt) return;

  document.getElementById("review-title").textContent = prompt.title;
  // 본문에서도 **…**로 강조할 수 있게 — 메일 제목처럼 눈으로 찾아야 하는 부분을
  // 문구만 고쳐서 굵게 만들 수 있다.
  document.getElementById("review-body").innerHTML = renderEmphasis(prompt.body);

  // 남은 기간 — 기한이 지났거나 안 쓰기로 한 설정이면 Python이 빈 문자열을 준다.
  const deadlineEl = document.getElementById("review-deadline");
  deadlineEl.innerHTML = prompt.deadline_note ? renderEmphasis(prompt.deadline_note) : "";
  deadlineEl.hidden = !prompt.deadline_note;

  // 링크가 없으면(리틀리 후기란처럼 구매자마다 주소가 다른 경우) 여는 버튼을 아예
  // 안 만든다 — 눌러도 아무 일 없는 버튼을 두면 "고장난 앱"으로 읽힌다.
  const openBtn = document.getElementById("review-open");
  openBtn.hidden = !prompt.has_url;
  openBtn.textContent = prompt.cta;

  // 여는 버튼이 빠지면 남는 둘이 다 흐린 보조 버튼이 되어, 읽고 나서 어디를 눌러야
  // 창이 닫히는지 알 수 없다. 안내를 다 읽었다는 뜻의 버튼 하나를 주 버튼으로 세운다
  // (동작은 그대로 "나중에" — 정해진 기간 뒤에 다시 묻는다).
  const laterBtn = document.getElementById("review-later");
  laterBtn.textContent = prompt.has_url ? "나중에" : "알겠습니다";
  laterBtn.classList.toggle("primary", !prompt.has_url);

  document.getElementById("review-backdrop").hidden = false;
}

document.getElementById("review-open").addEventListener("click", async () => {
  closeReviewModal();
  // 주소는 JS가 들고 있지 않다 — Python이 원격 설정에서 받아둔 값으로 연다.
  const opened = await window.pywebview.api.open_review_url();
  showToast(
    opened
      ? "브라우저에서 열었습니다. 정말 고맙습니다!"
      : "브라우저를 열지 못했습니다. 나중에 다시 안내드릴게요."
  );
});

// "나중에"는 아무 표시도 안 남긴다 — 이미 review_prompt()가 물어본 횟수를 세뒀으므로
// 정해진 기간 뒤에 다시 뜨고, 횟수를 다 쓰면 알아서 그만 묻는다.
document.getElementById("review-later").addEventListener("click", closeReviewModal);

document.getElementById("review-never").addEventListener("click", async () => {
  closeReviewModal();
  await window.pywebview.api.review_prompt_never_again();
});

/* ---------- 매니저 자기 자신 업데이트 ---------- */

async function checkSelfUpdate() {
  const info = await window.pywebview.api.check_self_update();
  // 지금 돌고 있는 버전을 항상 보여준다. 어디에도 안 보여서 "업데이트했는데 버튼이 안
  // 사라진다" 같은 상황에서 실제로 몇 버전이 도는지 확인할 방법이 없었다.
  managerVersion = info.current || null;
  document.getElementById("manager-version").textContent = info.current ? `v${info.current}` : "";

  const btn = document.getElementById("self-update-btn");
  if (info.update_available) {
    btn.hidden = false;
    btn.title = `v${info.current} → v${info.latest}`;
  } else {
    btn.hidden = true;
    btn.title = ""; // 남겨두면 다시 보이게 됐을 때 옛 버전이 그대로 뜬다
  }
}

document.getElementById("self-update-btn").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.textContent = "설치 중…";
  // Lens 설치와 똑같이 전면 오버레이를 띄운다. 예전엔 버튼 글자만 바뀌어서, 다운로드가
  // 오래 걸리는 동안 멈춘 건지 진행 중인지 알 수 없었다(맥에서 실제로 그랬다).
  showBusyOverlay("LeetKit Manager를 업데이트하는 중…");
  let result;
  try {
    result = await window.pywebview.api.self_update();
  } finally {
    hideBusyOverlay();
  }
  if (result.ok) {
    // 다시 띄우지 못했으면 그렇다고 말한다 — 저절로 뜰 줄 알고 기다리게 두면 안 된다.
    // (relaunching이 아예 안 오는 단일 exe 경로는 replace_running_exe가 같이 띄운다.)
    showToast(
      result.relaunching === false
        ? `v${result.version}로 업데이트됨 — 앱을 닫습니다. 바로가기로 다시 열어주세요.`
        : `v${result.version}로 업데이트됨 — 앱을 다시 시작합니다`
    );
    showBusyOverlay("앱을 다시 시작하는 중…");
    setTimeout(() => window.pywebview.api.quit(), 1600);
  } else {
    showToast(result.error || "업데이트에 실패했습니다.");
    btn.disabled = false;
    btn.textContent = "업데이트";
    // 실패했으면 화면에 남은 안내가 사실과 다를 수 있다 — 지금 상태로 다시 맞춘다.
    try {
      await checkSelfUpdate();
    } catch {
      /* 확인 실패는 조용히 — 버튼은 그대로 남아 다시 시도할 수 있다 */
    }
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
  // 업데이트 알림이 후기 요청보다 먼저다 — 이건 지금 눌러서 할 일이 있는 알림이고,
  // 후기는 부탁이다. 후기 쪽은 다른 모달이 떠 있으면 알아서 물러난다.
  // 기간이 끝난 건 지금 당장 막혀 있는 상태다 — 업데이트·후기보다 먼저 말해야 한다.
  try {
    await maybeShowExpiredNotice();
  } catch {
    /* 못 띄워도 카드에 "기간 종료"는 그대로 보인다 */
  }
  try {
    maybeShowUpdateNotice();
  } catch {
    /* 알림을 못 띄워도 카드의 업데이트 버튼은 그대로 있다 */
  }
  // 마법사 판단이 끝난 뒤에 본다 — 위에서 "이미 다 끝난 사용자"로 판명돼 완료 플래그가
  // 방금 세워졌을 수 있고, 그 사람이야말로 후기를 물어볼 대상이다.
  try {
    await maybeShowReviewPrompt();
  } catch {
    /* 후기 요청은 없어도 그만인 기능 — 실패를 사용자에게 보일 이유가 없다 */
  }
});
