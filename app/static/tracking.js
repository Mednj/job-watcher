const applicationStatuses = {
  applied: "Applied",
  interviewing: "Interviewing",
  offer_received: "Offer received",
  accepted: "Accepted",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};
const trackingState = {
  offset: 0,
  total: 0,
  detail: null,
  promptKey: null,
  busy: false,
  generation: 0,
};
const applicationLabel = (value) => applicationStatuses[value] || value;
const trackingUrl = (value) =>
  /^https:\/\/(www\.linkedin\.com|www\.hellowork\.com|www\.welcometothejungle\.com|candidat\.francetravail\.fr|www\.jobteaser\.com|career\.polytech\.network)\//.test(
    value,
  )
    ? value
    : "#";
const calendarDate = (value) =>
  value ? new Date(`${value}T12:00:00`).toLocaleDateString() : "Date not set";
const localToday = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
};
for (const [value, label] of Object.entries(applicationStatuses)) {
  for (const id of ["tracking-status", "tracking-filter"]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    $(id).append(option);
  }
}
function clearTrackingWorkspace() {
  trackingState.generation++;
  trackingState.offset = 0;
  trackingState.detail = null;
  trackingState.promptKey = null;
  for (const id of ["tracking-prompt", "tracking-detail"]) $(id).close();
  $("tracking-form").reset();
  $("tracking-list").replaceChildren();
  $("tracking-history").replaceChildren();
  $("tracking-detail-job").textContent = "";
  $("tracking-prompt-job").textContent = "";
  $("tracking-add-date").value = "";
  $("tracking-summary").replaceChildren();
  $("tracking-count").textContent = "0";
  $("tracking-query").value = "";
  $("tracking-filter").value = "";
}
async function refreshTracking() {
  if (!state.token) return;
  const generation = ++trackingState.generation;
  const params = new URLSearchParams({
    limit: "50",
    offset: String(trackingState.offset),
  });
  if ($("tracking-filter").value)
    params.set("status", $("tracking-filter").value);
  if ($("tracking-query").value.trim())
    params.set("q", $("tracking-query").value.trim());
  try {
    const data = await api(`applications?${params}`);
    if (generation !== trackingState.generation) return;
    if (trackingState.offset && trackingState.offset >= data.total) {
      trackingState.offset = Math.max(0, Math.ceil(data.total / 50) * 50 - 50);
      return refreshTracking();
    }
    trackingState.total = data.total;
    const total = Object.values(data.counts).reduce((a, b) => a + b, 0);
    $("tracking-count").textContent = total;
    $("tracking-error").textContent = "";
    $("tracking-summary").innerHTML = [
      ["", "All applications", total],
      ...Object.entries(applicationStatuses).map(([key, label]) => [
        key,
        label,
        data.counts[key] || 0,
      ]),
    ]
      .map(
        ([key, label, count]) =>
          `<button class="tracking-stat${$("tracking-filter").value === key ? " selected" : ""}" data-tracking-filter="${key}" aria-pressed="${$("tracking-filter").value === key}"><strong>${count}</strong><span>${label}</span></button>`,
      )
      .join("");
    $("tracking-list").innerHTML = data.items.length
      ? data.items
          .map(
            (item) => `
      <article class="tracking-card">
        <div class="tracking-card-top"><div>${logo(item.source)}<span class="muted">${esc(sourceName(item.source))}</span></div><span class="tracking-badge ${esc(item.status)}">${esc(applicationLabel(item.status))}</span></div>
        <h3><a href="${esc(trackingUrl(item.url))}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a></h3>
        <p class="job-company">${esc(item.company)}</p>
        <p class="muted">${esc(item.location)} · Applied: ${esc(calendarDate(item.applied_on))}</p>
        <p class="tracking-note">${esc(item.notes ? item.notes.slice(0, 180) + (item.notes.length > 180 ? "…" : "") : "No notes yet.")}</p>
        <div class="tracking-card-bottom"><small class="muted">Updated ${esc(date(item.updated_at))}</small><button class="secondary" data-tracking-edit="${esc(item.job_key)}">Update progress</button></div>
      </article>`,
          )
          .join("")
      : empty(
          total
            ? "No applications match these filters"
            : "Your next chapter starts here",
          total
            ? "Try another status or search for a different company."
            : "After confirming you applied to an offer, choose Yes, track it. Already applied? Use Track application on its opportunity card.",
          false,
        );
    $("tracking-range").textContent = data.total
      ? `${trackingState.offset + 1}–${Math.min(trackingState.offset + 50, data.total)} of ${data.total}`
      : "0 applications";
    $("tracking-prev").disabled = trackingState.offset === 0;
    $("tracking-next").disabled = trackingState.offset + 50 >= data.total;
  } catch (error) {
    if (generation === trackingState.generation)
      $("tracking-error").textContent = error.message;
  }
}
function showTrackingPrompt(key, justConfirmed = false) {
  const job = state.jobs.find((item) => item.key === key);
  if (!job?.applied) return;
  if (job.tracked) return openTracking(key);
  trackingState.promptKey = key;
  $("tracking-prompt-job").textContent = `${job.title} · ${job.company}`;
  $("tracking-add-date").value = justConfirmed ? localToday() : "";
  $("tracking-prompt-error").textContent = "";
  $("tracking-prompt").showModal();
}
async function openTracking(key) {
  const token = state.token;
  try {
    const item = await api(`applications/${encodeURIComponent(key)}`);
    if (token !== state.token || !state.token) return;
    trackingState.detail = item;
    $("tracking-status").value = item.status;
    $("tracking-date").value = item.applied_on || "";
    $("tracking-notes").value = item.notes;
    $("tracking-detail-job").textContent = `${item.title} · ${item.company}`;
    $("tracking-save-error").textContent = "";
    $("tracking-history").innerHTML = item.history
      .map(
        (entry) =>
          `<li><b>${esc({ added: "Added to tracking", readded: "Added again", removed: "Removed from tracking", updated: "Progress updated" }[entry.kind] || entry.kind)} · ${esc(applicationLabel(entry.status))}</b><small>${esc(date(entry.created_at))} · Applied: ${esc(calendarDate(entry.applied_on))}</small>${entry.notes ? `<p>${esc(entry.notes.slice(0, 200))}${entry.notes.length > 200 ? "…" : ""}</p>` : ""}</li>`,
      )
      .join("");
    if (!$("tracking-detail").open) $("tracking-detail").showModal();
  } catch (error) {
    toast(error.message, true);
  }
}
document.addEventListener("click", async (event) => {
  const offer = event.target.closest("[data-track-offer]");
  if (offer) showTrackingPrompt(offer.dataset.trackOffer);
  const edit = event.target.closest("[data-tracking-edit]");
  if (edit) await openTracking(edit.dataset.trackingEdit);
  const filter = event.target.closest("[data-tracking-filter]");
  if (filter) {
    $("tracking-filter").value = filter.dataset.trackingFilter;
    trackingState.offset = 0;
    await refreshTracking();
  }
  if (event.target.closest('[data-page="applications"]'))
    await refreshTracking();
});
$("tracking-skip").addEventListener("click", () =>
  $("tracking-prompt").close(),
);
$("tracking-add").addEventListener("click", async () => {
  if (!$("tracking-add-date").reportValidity()) return;
  const key = trackingState.promptKey;
  $("tracking-add").disabled = $("tracking-skip").disabled = true;
  try {
    await api(`applications/${encodeURIComponent(key)}`, {
      method: "POST",
      body: JSON.stringify({
        applied_on: $("tracking-add-date").value || null,
      }),
    });
    const job = state.jobs.find((item) => item.key === key);
    if (job) job.tracked = true;
    $("tracking-prompt").close();
    renderJobs();
    await refreshTracking();
    toast("Added to My applications.");
  } catch (error) {
    $("tracking-prompt-error").textContent = error.message;
  } finally {
    $("tracking-add").disabled = $("tracking-skip").disabled = false;
  }
});
$("tracking-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!trackingState.detail || trackingState.busy) return;
  trackingState.busy = true;
  $("tracking-save").disabled = $("tracking-remove").disabled = true;
  try {
    await api(
      `applications/${encodeURIComponent(trackingState.detail.job_key)}`,
      {
        method: "PUT",
        body: JSON.stringify({
          status: $("tracking-status").value,
          notes: $("tracking-notes").value,
          applied_on: $("tracking-date").value || null,
          version: trackingState.detail.version,
        }),
      },
    );
    $("tracking-detail").close();
    await refreshTracking();
    toast("Application progress saved.");
  } catch (error) {
    $("tracking-save-error").textContent = error.message;
  } finally {
    trackingState.busy = false;
    $("tracking-save").disabled = $("tracking-remove").disabled = false;
  }
});
$("tracking-remove").addEventListener("click", async () => {
  if (!trackingState.detail || trackingState.busy) return;
  trackingState.busy = true;
  $("tracking-save").disabled = $("tracking-remove").disabled = true;
  const key = trackingState.detail.job_key;
  try {
    await api(`applications/${encodeURIComponent(key)}`, { method: "DELETE" });
    const job = state.jobs.find((item) => item.key === key);
    if (job) job.tracked = false;
    $("tracking-detail").close();
    renderJobs();
    await refreshTracking();
    toast("Removed from tracking. Your offer is still marked Applied.");
  } catch (error) {
    $("tracking-save-error").textContent = error.message;
  } finally {
    trackingState.busy = false;
    $("tracking-save").disabled = $("tracking-remove").disabled = false;
  }
});
$("tracking-filter").addEventListener("change", () => {
  trackingState.offset = 0;
  refreshTracking();
});
let trackingQueryTimer;
$("tracking-query").addEventListener("input", () => {
  clearTimeout(trackingQueryTimer);
  trackingQueryTimer = setTimeout(() => {
    trackingState.offset = 0;
    refreshTracking();
  }, 250);
});
$("tracking-prev").addEventListener("click", () => {
  trackingState.offset = Math.max(0, trackingState.offset - 50);
  refreshTracking();
});
$("tracking-next").addEventListener("click", () => {
  trackingState.offset += 50;
  refreshTracking();
});
