const $ = (id) => document.getElementById(id);
const state = {
  status: null,
  jobs: [],
  filter: "",
  token: sessionStorage.getItem("jw-token") || "",
  user: null,
  refreshing: false,
};
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const sourceNames = {
  linkedin: "LinkedIn",
  hellowork: "HelloWork",
  apec: "APEC",
  glassdoor: "Glassdoor",
  indeed: "Indeed",
  jobteaser: "JobTeaser",
  monster: "Monster",
  wttj: "Welcome to the Jungle",
  francetravail: "France Travail (Pôle emploi)",
};
const sources = Object.keys(sourceNames);
const sourceName = (source) => sourceNames[source] || source;
const logo = (source) =>
  `<span class="source-logo ${esc(source)}">${esc(sourceName(source).slice(0, 2))}</span>`;
const date = (timestamp) =>
  timestamp
    ? new Date(timestamp * 1000).toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "Not checked yet";
function age(timestamp) {
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - timestamp));
  return seconds < 60
    ? `${seconds}s ago`
    : seconds < 3600
      ? `${Math.floor(seconds / 60)}m ago`
      : seconds < 86400
        ? `${Math.floor(seconds / 3600)}h ago`
        : date(timestamp);
}
let toastTimer;
function toast(message, error = false) {
  $("toast").textContent = message;
  $("toast").className = `visible${error ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").className = ""), 5000);
}
async function api(path, options = {}) {
  const response = await fetch(`/api/${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
      ...options.headers,
    },
  });
  if (response.status === 401 && path !== "auth/login") {
    state.token = "";
    sessionStorage.removeItem("jw-token");
    if (!$("auth-dialog").open) $("auth-dialog").showModal();
    throw new Error("Sign in to continue.");
  }
  const data = await response.json();
  if (!response.ok) {
    const detail = data.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail
              .map((issue) => {
                const reason = String(issue.msg || "Invalid value").replace(
                  /^Value error,\s*/,
                  "",
                );
                const field = Array.isArray(issue.loc)
                  ? issue.loc.filter((part) => part !== "body").join(" → ")
                  : "";
                return field ? `${field}: ${reason}` : reason;
              })
              .join("; ")
          : "The request was rejected. Check the form values and try again.";
    throw new Error(message);
  }
  return data;
}
function page(name) {
  document
    .querySelectorAll(".page")
    .forEach((el) => el.classList.toggle("active", el.id === `page-${name}`));
  document
    .querySelectorAll("[data-page]")
    .forEach((el) => el.classList.toggle("active", el.dataset.page === name));
}
function renderStatus() {
  const s = state.status,
    summary = s.summary,
    active = s.searches.filter((x) => x.enabled);
  state.user = s.user;
  $("current-user").textContent = s.user?.username || "";
  $("users-nav").classList.toggle("hidden", s.user?.role !== "admin");
  $("stat-jobs").textContent = summary.jobs;
  $("stat-sent").textContent = summary.sent;
  $("stat-queue").textContent =
    `${summary.pending} waiting · ${summary.failed} failed`;
  $("stat-searches").textContent = active.length;
  $("search-count").textContent = s.searches.length;
  $("stat-latency").textContent =
    summary.average_detection_to_delivery_ms == null
      ? "—"
      : `${(summary.average_detection_to_delivery_ms / 1000).toFixed(1)}s`;
  $("worker-label").textContent = s.monitor_running
    ? "Radar online"
    : "Monitoring stopped";
  $("worker-dot").classList.toggle("error", !s.monitor_running);
  $("updated").textContent =
    `UPDATED ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
  $("connections").innerHTML = sources
    .map((source) => {
      const searches = active
          .filter((x) => x.sources.includes(source))
          .map((x) => ({
            ...x,
            ...x.source_statuses.find((t) => t.source === source),
          })),
        errors = searches.filter((x) => x.error),
        latest = Math.max(0, ...searches.map((x) => x.last_success || 0));
      const badge = errors.length
        ? "Needs attention"
        : !searches.length
          ? "No searches"
          : !latest
            ? "Starting"
            : "Monitoring";
      return `<article class="connection">${logo(source)}<div><h3>${sourceName(source)}</h3><p>${searches.length} active ${searches.length === 1 ? "search" : "searches"} · ${latest ? `Last success ${esc(age(latest))}` : "Awaiting first scan"}</p></div><span class="badge ${errors.length ? "warning" : !searches.length ? "idle" : ""}">${badge}</span></article>`;
    })
    .join("");
  $("telegram-banner").classList.toggle("hidden", s.telegram_configured);
  $("delivery-title").textContent = s.telegram_configured
    ? "Credentials configured"
    : "Not connected yet";
  $("delivery-description").textContent = s.telegram_configured
    ? `${summary.sent} delivered · ${summary.pending} waiting · ${summary.failed} failed. Send a test to verify the connection.`
    : "Add your own bot token and chat ID below. New alerts will wait in your private queue until connected.";
  $("telegram-error").textContent = s.telegram_error || "";
  $("test-telegram").disabled = !s.telegram_configured;
  $("retry-telegram").disabled = !summary.failed;
  renderSearches();
}
function empty(title, description, action = true) {
  return `<div class="empty"><div class="empty-mark">◎</div><h3>${title}</h3><p>${description}</p>${action ? '<button class="primary" data-action="new-search">＋ Create your first search</button>' : ""}</div>`;
}
function renderJobs() {
  $("feed-count").textContent = state.jobs.length;
  if (!state.jobs.length) {
    $("jobs").innerHTML = state.status?.searches.length
      ? empty(
          "No opportunities in this view yet",
          "Your radar will show jobs after the first successful scan. Try another filter, or check your saved searches for source errors.",
          false,
        )
      : empty(
          "Your next move is out there.",
          "Start with a keyword like cloud or DevOps, or choose IT for a broader search. Your radar takes it from there.",
        );
    return;
  }
  const badges = {
    baseline: ["Previously skipped", "idle"],
    pending: ["Waiting to send", "warning"],
    sent: ["Alert delivered", ""],
    failed: ["Delivery failed", "warning"],
  };
  $("jobs").innerHTML = state.jobs
    .map((job) => {
      const badge = badges[job.status] || ["Unknown", "idle"];
      const safeUrl =
        /^https:\/\/(www\.linkedin\.com|www\.hellowork\.com|www\.welcometothejungle\.com|candidat\.francetravail\.fr|www\.jobteaser\.com)\//.test(
          job.url,
        )
          ? job.url
          : "#";
      return `<article class="job${job.applied ? " applied" : ""}">${logo(job.source)}<div class="job-main"><h3><a data-job-open="${esc(job.key)}" href="${esc(safeUrl)}" target="_blank" rel="noopener noreferrer">${esc(job.title)}</a></h3><p class="job-company">${esc(job.company)}</p><div class="job-meta"><span>⌖ ${esc(job.location)}</span><span class="job-contract">${esc(job.contract)}</span><span>${sourceName(job.source)}</span>${job.published_label ? `<span>Published ${esc(job.published_label)}</span>` : ""}</div>${job.error ? `<p class="search-error">${esc(job.error)}</p>` : ""}</div><div class="job-right">${job.applied ? '<div class="application-status"><span>✓ Applied</span><span aria-hidden="true">·</span><button aria-label="Undo applied" data-job-undo="' + esc(job.key) + '">Undo</button></div>' : `<span class="badge ${badge[1]}">${badge[0]}</span>`}<small>Found ${esc(age(job.first_seen))}</small><a data-job-open="${esc(job.key)}" href="${esc(safeUrl)}" target="_blank" rel="noopener noreferrer">View & apply ↗</a></div></article>`;
    })
    .join("");
}
function renderSearches() {
  const searches = state.status.searches;
  $("searches").innerHTML = searches.length
    ? searches
        .map((s) => {
          const health = s.source_statuses
            .map(
              (t) =>
                `<div class="search-health"><b>${sourceName(t.source)}</b> · ${!s.enabled ? "Paused" : t.error ? "Needs attention" : t.initialized ? "Watching" : "First scan pending"}<br>Last success: ${esc(date(t.last_success))} · ${t.last_count} matches · ${t.last_new} new alerts${t.last_duration_ms != null ? ` · ${(t.last_duration_ms / 1000).toFixed(2)}s scan` : ""}<br>${s.enabled ? `Next eligible check: ${esc(date(Math.max(t.next_check, state.status.sources.find((x) => x.source === t.source)?.next_request || 0)))}` : ""}${t.error ? `<p class="search-error">${esc(t.error)}</p>` : ""}</div>`,
            )
            .join("");
          return `<article class="search-card"><div class="search-card-head">${s.sources.map(logo).join("")}<div><h3>${esc(s.name)}</h3><span class="muted">${s.sources.map(sourceName).join(" + ")}</span></div><span class="badge ${s.error ? "warning" : !s.enabled ? "idle" : ""}">${!s.enabled ? "Paused" : s.error ? "Needs attention" : s.initialized ? "Watching" : "Starting"}</span></div><p class="search-detail"><b>${esc(s.keywords)}</b> · ${esc(s.location)} · ${esc(s.contract === "any" ? "Any contract" : s.contract)} · Every ${s.interval_seconds}s${s.exclude_keywords.length ? `<br>Excluding: ${esc(s.exclude_keywords.join(", "))}` : ""}</p>${health}<div class="button-row"><button class="secondary" data-search-action="check" data-id="${s.id}" ${!s.enabled ? "disabled" : ""}>Check all websites</button><button class="secondary" data-search-action="toggle" data-id="${s.id}">${s.enabled ? "Pause" : "Resume"}</button><button class="text-button" data-search-action="edit" data-id="${s.id}">Edit</button><button class="text-button" data-search-action="delete" data-id="${s.id}">Remove</button></div></article>`;
        })
        .join("")
    : empty(
        "A focused search is a faster start.",
        "Choose your keyword and contract, then select the websites to monitor together.",
      );
}
async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const params = new URLSearchParams({ limit: "100" });
    if ($("source-filter").value)
      params.set("source", $("source-filter").value);
    if (state.filter) params.set("status", state.filter);
    if ($("job-query").value.trim())
      params.set("q", $("job-query").value.trim());
    const [status, jobs] = await Promise.all([
      api("status"),
      api(`jobs?${params}`),
    ]);
    state.status = status;
    state.jobs = jobs;
    renderStatus();
    renderJobs();
    await refreshBans();
  } catch (error) {
    $("worker-label").textContent = error.message;
    $("worker-dot").classList.add("error");
  } finally {
    state.refreshing = false;
  }
}
function searchDialog(search = null) {
  $("search-form").reset();
  $("search-id").value = search?.id || "";
  $("search-dialog-title").textContent = search
    ? "Edit search"
    : "Create a search";
  for (const field of [
    "name",
    "keywords",
    "location",
    "contract",
    "experience",
  ]) {
    if (search) $(`search-${field}`).value = search[field];
  }
  $("search-interval").value = search?.interval_seconds || "60";
  $("search-exclusions").value = search?.exclude_keywords.join(", ") || "";
  $("search-enabled").checked = search?.enabled ?? true;
  for (const source of sources)
    $(`search-${source}`).checked = search
      ? search.sources.includes(source)
      : true;
  updateContractNote();
  $("search-dialog").showModal();
}
function updateContractNote() {
  $("contract-note").textContent = $("search-linkedin").checked
    ? "LinkedIn uses French contract names as search keywords. Exact contract filtering is available on HelloWork."
    : "HelloWork applies native contract filters. Experience categories use the platform’s own filters.";
}
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.page) {
    page(button.dataset.page);
    if (button.dataset.page === "users") refreshUsers();
    if (button.dataset.page === "delivery") loadTelegramSettings();
  }
  if (button.dataset.close) $(button.dataset.close).close();
  if (button.dataset.action === "new-search") searchDialog();
  if (button.dataset.action === "setup") $("setup-dialog").showModal();
  if (button.dataset.action === "telegram") {
    page("delivery");
    loadTelegramSettings();
  }
  if (button.hasAttribute("data-status")) {
    state.filter = button.dataset.status;
    document
      .querySelectorAll("[data-status]")
      .forEach((el) => el.classList.toggle("active", el === button));
    await refresh();
  }
  if (button.dataset.searchAction) {
    const s = state.status.searches.find(
      (x) => x.id === Number(button.dataset.id),
    );
    if (!s) return;
    const action = button.dataset.searchAction;
    if (action === "edit") {
      searchDialog(s);
      return;
    }
    button.disabled = true;
    try {
      if (action === "delete") {
        await api(`searches/${s.id}`, { method: "DELETE" });
        toast("Search removed. Discovered jobs are kept.");
      }
      if (action === "check") {
        await api(`searches/${s.id}/check`, { method: "POST" });
        toast("Check queued. Platform cooldowns still apply.");
      }
      if (action === "toggle") {
        await api(`searches/${s.id}`, {
          method: "PUT",
          body: JSON.stringify({ ...s, enabled: !s.enabled }),
        });
        toast(s.enabled ? "Search paused." : "Search resumed.");
      }
      await refresh();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }
});
for (const source of sources)
  $(`search-${source}`).addEventListener("change", updateContractNote);
$("search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const config = {};
  for (const field of [
    "name",
    "keywords",
    "location",
    "contract",
    "experience",
  ])
    config[field] = $(`search-${field}`).value.trim();
  config.sources = sources.filter((source) => $(`search-${source}`).checked);
  if (!config.sources.length) {
    toast("Select at least one website.", true);
    return;
  }
  config.exclude_keywords = $("search-exclusions")
    .value.split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  config.interval_seconds = Number($("search-interval").value);
  config.enabled = $("search-enabled").checked;
  const id = $("search-id").value;
  $("save-search").disabled = true;
  try {
    await api(id ? `searches/${id}` : "searches", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(config),
    });
    $("search-dialog").close();
    toast(
      id
        ? "Search updated."
        : "Search saved. Your first matches will be sent to Telegram.",
    );
    await refresh();
  } catch (error) {
    toast(error.message, true);
  } finally {
    $("save-search").disabled = false;
  }
});
async function loadTelegramSettings() {
  try {
    const settings = await api("telegram/settings");
    $("telegram-chat-id").value = settings.chat_id || "";
    $("telegram-token").value = "";
    $("telegram-token").placeholder = settings.configured
      ? "Token saved · leave blank to keep it"
      : "Paste your bot token from BotFather";
  } catch (error) {
    toast(error.message, true);
  }
}
async function refreshUsers() {
  try {
    const users = await api("users");
    $("user-list").innerHTML = users
      .map(
        (user) =>
          `<article class="job account-card"><div class="job-main"><h3>${esc(user.username)} ${user.id === state.user?.id ? "(you)" : ""}</h3><p class="muted">${esc(user.role)} · ${user.active ? "Active" : "Disabled"}</p></div>${user.id === state.user?.id ? "" : `<button class="secondary" data-user-active="${user.id}" data-active="${user.active ? "false" : "true"}">${user.active ? "Disable" : "Enable"}</button>`}</article>`,
      )
      .join("");
  } catch (error) {
    toast(error.message, true);
  }
}
$("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("auth-error").textContent = "";
  try {
    const result = await api("auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("login-username").value.trim(),
        password: $("login-password").value,
      }),
    });
    state.token = result.token;
    state.user = result.user;
    sessionStorage.setItem("jw-token", state.token);
    $("login-password").value = "";
    $("auth-dialog").close();
    await refresh();
  } catch (error) {
    $("auth-error").textContent = error.message;
  }
});
$("show-register").addEventListener("click", () => {
  $("auth-form").classList.add("hidden");
  $("register-form").classList.remove("hidden");
});
$("show-login").addEventListener("click", () => {
  $("register-form").classList.add("hidden");
  $("auth-form").classList.remove("hidden");
});
$("register-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("register-error").textContent = "";
  try {
    const result = await api("auth/register", {
      method: "POST",
      body: JSON.stringify({
        username: $("register-username").value.trim(),
        password: $("register-password").value,
      }),
    });
    state.token = result.token;
    state.user = result.user;
    sessionStorage.setItem("jw-token", state.token);
    $("register-form").reset();
    $("auth-dialog").close();
    $("register-form").classList.add("hidden");
    $("auth-form").classList.remove("hidden");
    await refresh();
  } catch (error) {
    $("register-error").textContent = error.message;
  }
});
$("user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("users", {
      method: "POST",
      body: JSON.stringify({
        username: $("new-username").value.trim(),
        password: $("new-password").value,
      }),
    });
    $("user-form").reset();
    toast("Account created. Share its username and temporary password securely.");
    await refreshUsers();
  } catch (error) {
    toast(error.message, true);
  }
});
$("telegram-settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const settings = await api("telegram/settings", {
      method: "PUT",
      body: JSON.stringify({
        bot_token: $("telegram-token").value,
        chat_id: $("telegram-chat-id").value,
      }),
    });
    $("telegram-token").value = "";
    if (state.status) {
      state.status.telegram_configured = settings.configured;
      renderStatus();
    }
    toast("Your Telegram bot is saved for this account.");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
});
$("disconnect-telegram").addEventListener("click", async () => {
  try {
    await api("telegram/settings", { method: "DELETE" });
    $("telegram-settings-form").reset();
    toast("Telegram disconnected for this account.");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
});
$("user-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-user-active]");
  if (!button) return;
  try {
    await api(`users/${button.dataset.userActive}/active?active=${button.dataset.active}`, {
      method: "PATCH",
    });
    await refreshUsers();
  } catch (error) {
    toast(error.message, true);
  }
});
$("change-password").addEventListener("click", () => $("password-dialog").showModal());
$("password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("auth/password", {
      method: "PUT",
      body: JSON.stringify({
        current_password: $("current-password").value,
        new_password: $("replacement-password").value,
      }),
    });
    sessionStorage.removeItem("jw-token");
    state.token = "";
    $("password-form").reset();
    $("password-dialog").close();
    $("auth-dialog").showModal();
    toast("Password changed. Sign in with your new password.");
  } catch (error) {
    toast(error.message, true);
  }
});
$("logout").addEventListener("click", async () => {
  try {
    await api("auth/logout", { method: "POST" });
  } finally {
    sessionStorage.removeItem("jw-token");
    state.token = "";
    state.user = null;
    $("auth-dialog").showModal();
  }
});
$("source-filter").addEventListener("change", refresh);
let queryTimer;
$("job-query").addEventListener("input", () => {
  clearTimeout(queryTimer);
  queryTimer = setTimeout(refresh, 250);
});
for (const [id, route, message] of [
  ["test-telegram", "telegram/test", "Test message sent. Check Telegram."],
  ["retry-telegram", "telegram/retry", "Failed alerts queued for retry."],
])
  $(id).addEventListener("click", async () => {
    $(id).disabled = true;
    try {
      await api(route, { method: "POST" });
      toast(message);
      await refresh();
    } catch (error) {
      toast(error.message, true);
    } finally {
      $(id).disabled = false;
    }
  });
refresh();
setInterval(() => {
  if (!document.hidden && !$("auth-dialog").open) refresh();
}, 5000);

let applicationKey = null;
document.addEventListener("click", async (event) => {
  const link = event.target.closest("[data-job-open]");
  if (link && link.getAttribute("href") !== "#") {
    const job = state.jobs.find((item) => item.key === link.dataset.jobOpen);
    if (job && !job.applied) {
      applicationKey = job.key;
      $("application-job").textContent = `${job.title} · ${job.company}`;
      if (!$("application-dialog").open) $("application-dialog").showModal();
    }
  }
  const undo = event.target.closest("[data-job-undo]");
  if (undo) await saveApplication(undo.dataset.jobUndo, false);
});
async function saveApplication(key, applied) {
  try {
    await api(`jobs/${encodeURIComponent(key)}/application`, {
      method: "PATCH",
      body: JSON.stringify({ applied }),
    });
    const job = state.jobs.find((item) => item.key === key);
    if (job) job.applied = applied;
    renderJobs();
    toast(applied ? "Marked as applied." : "Marked as not applied.");
    return true;
  } catch (error) {
    toast(error.message, true);
    return false;
  }
}
for (const [id, applied] of [
  ["application-yes", true],
  ["application-no", false],
]) {
  $(id).addEventListener("click", async () => {
    $("application-yes").disabled = $("application-no").disabled = true;
    if (await saveApplication(applicationKey, applied))
      $("application-dialog").close();
    $("application-yes").disabled = $("application-no").disabled = false;
  });
}

async function refreshBans() {
  const bans = await api("recruiter-bans");
  $("banned-recruiters").innerHTML = bans.length
    ? bans
        .map(
          (b) =>
            `<article class="job"><div class="job-main"><h3>${esc(b.name)}</h3><p class="muted">Excluded from every search</p></div><button class="secondary" data-unban="${esc(b.name)}">Remove ban</button></article>`,
        )
        .join("")
    : '<p class="muted">No blocked recruiters.</p>';
}
async function banRecruiter(name) {
  await api("recruiter-bans", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  toast("Recruiter blocked across all searches.");
  await refresh();
}
$("ban-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await banRecruiter($("ban-name").value);
    $("ban-form").reset();
  } catch (error) {
    toast(error.message, true);
  }
});
$("application-ban").addEventListener("click", async () => {
  const job = state.jobs.find((j) => j.key === applicationKey);
  if (!job) return;
  $("application-ban").disabled = true;
  try {
    await banRecruiter(job.company);
    $("application-dialog").close();
  } catch (error) {
    $("application-dialog").close();
    toast(`Can't ban this recruiter: ${error.message.replace(/^name:\s*/i, "")}`, true);
  } finally {
    $("application-ban").disabled = false;
  }
});
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-unban]");
  if (!button) return;
  try {
    await api(`recruiter-bans/${encodeURIComponent(button.dataset.unban)}`, {
      method: "DELETE",
    });
    await refresh();
    toast("Recruiter allowed again.");
  } catch (error) {
    toast(error.message, true);
  }
});
