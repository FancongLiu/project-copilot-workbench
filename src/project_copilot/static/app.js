const sameOriginHeaders = { "X-Project-Copilot": "1" };
const navButtons = document.querySelectorAll("[data-view]");
const viewPanels = document.querySelectorAll("[data-view-panel]");
const workspaceSelect = document.querySelector("#workspace-select");
const workspaceStatus = document.querySelector("#workspace-status");
const inventoryList = document.querySelector("#inventory-list");
const inventorySummary = document.querySelector("#inventory-summary");
const copilotForm = document.querySelector("#copilot-form");
const copilotQuestion = document.querySelector("#copilot-question");
const copilotSubmit = copilotForm.querySelector("button[type='submit']");
const copilotAnswerBlock = document.querySelector("#copilot-answer");
const copilotAnswer = copilotAnswerBlock.querySelector("p");
const diagnosticResult = document.querySelector("#diagnostic-result");
const answerStatus = document.querySelector("#answer-status");
const citationList = document.querySelector("#source-list");
const citationCount = document.querySelector("#source-count");
const activityList = document.querySelector("#tool-activity");
const analyticsStatus = document.querySelector("#analytics-status");
const metricStrip = document.querySelector("#metric-strip");
const deleteDialog = document.querySelector("#delete-dialog");
const deleteCancel = document.querySelector("#delete-cancel");
const deleteConfirm = document.querySelector("#delete-confirm");
const deleteError = document.querySelector("#delete-error");

let workspaceEpoch = 0;
let copilotController = null;
let currentRequestId = null;
let pendingDelete = null;
let deleteTrigger = null;
let confirmedProjectId = workspaceSelect.value;

const toolLabels = {
  project_search: "Search project sources",
  configuration_lookup: "Look up configuration",
  meeting_decision_lookup: "Look up meeting decisions",
  governed_analytics: "Run read-only telemetry analysis",
  source_inspection: "Inspect source evidence",
  clarification: "Request missing details",
  defrost_diagnostics: "Check defrost sequence",
  agent: "Bounded project assistant",
};

for (const button of navButtons) {
  button.addEventListener("click", () => {
    const view = button.dataset.view;
    navButtons.forEach((item) => {
      const selected = item === button;
      item.classList.toggle("is-active", selected);
      if (selected) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    viewPanels.forEach((panel) =>
      panel.classList.toggle("is-visible", panel.dataset.viewPanel === view),
    );
    if (view === "analytics") refreshAnalytics(activeProjectId());
  });
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const payload =
    response.status === 204
      ? null
      : contentType.includes("application/json")
        ? await response.json()
        : { detail: await response.text() };
  if (!response.ok) {
    const detail = Array.isArray(payload?.detail)
      ? payload.detail.map((item) => item.msg || JSON.stringify(item)).join("; ")
      : payload?.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return payload;
}

function activeProjectId() {
  return confirmedProjectId;
}

function requestId() {
  return globalThis.crypto?.randomUUID?.() || `request-${Date.now()}-${Math.random()}`;
}

function setStatus(element, message, isError = false) {
  element.textContent = message;
  element.setAttribute("role", isError ? "alert" : "status");
}

function setWorkspaceBusy(busy) {
  for (const element of document.querySelectorAll(
    "#workspace-create-form input, #workspace-create-form button, #workspace-select, #source-upload-form select, #source-upload-form input, #source-upload-form button, #reindex-button, #copilot-form textarea, #copilot-form button, #defrost-guide-form input, #defrost-guide-form button",
  )) {
    element.disabled = busy;
  }
}

function resetProjectResults() {
  copilotAnswerBlock.removeAttribute("data-state");
  answerStatus.textContent = "Ready";
  copilotAnswer.hidden = false;
  copilotAnswer.textContent =
    "Ask a question after confirming the current project and its imported evidence.";
  diagnosticResult.hidden = true;
  diagnosticResult.replaceChildren();
  citationList.replaceChildren();
  renderEmpty(citationList, "No cited evidence yet.");
  citationCount.textContent = "0";
  activityList.replaceChildren();
  renderEmpty(activityList, "No project checks have run yet.");
}

function resetAnalytics() {
  metricStrip.hidden = true;
  setStatus(analyticsStatus, "Opening the selected project's telemetry context…");
}

async function submitCopilot(question) {
  const normalizedQuestion = String(question || "").trim();
  if (!normalizedQuestion) return;
  const projectId = activeProjectId();
  const epoch = workspaceEpoch;
  const selectedRequestId = requestId();
  currentRequestId = selectedRequestId;
  if (copilotController) copilotController.abort();
  copilotController = new AbortController();

  copilotSubmit.disabled = true;
  copilotAnswerBlock.dataset.state = "running";
  answerStatus.textContent = "Checking project evidence";
  copilotAnswer.textContent = "Running bounded, read-only project checks…";
  copilotAnswer.hidden = false;
  diagnosticResult.hidden = true;
  diagnosticResult.replaceChildren();
  citationList.replaceChildren();
  activityList.replaceChildren();
  citationCount.textContent = "0";

  try {
    const result = await fetchJson(
      `/api/workspaces/${encodeURIComponent(projectId)}/copilot/query`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", ...sameOriginHeaders },
        body: JSON.stringify({ question: normalizedQuestion, request_id: selectedRequestId }),
        signal: copilotController.signal,
      },
    );
    if (
      epoch !== workspaceEpoch ||
      projectId !== activeProjectId() ||
      result.project_id !== projectId ||
      result.request_id !== currentRequestId
    ) {
      throw new Error("Project context changed before this answer completed. Please ask again.");
    }
    copilotAnswerBlock.dataset.state = result.refused
      ? "refused"
      : result.clarification
        ? "clarification"
        : "completed";
    answerStatus.textContent = result.refused
      ? "Request not completed safely"
      : result.clarification
        ? "More project details needed"
        : "Evidence-backed answer";
    copilotAnswer.textContent = result.answer;
    if (result.diagnostic) renderDiagnostic(result.diagnostic, result.answer);
    citationCount.textContent = String(renderCitations(result.citations));
    renderActivities(result.activities);
  } catch (error) {
    if (error.name === "AbortError") return;
    copilotAnswerBlock.dataset.state = "failed";
    answerStatus.textContent = "Request failed";
    copilotAnswer.hidden = false;
    diagnosticResult.hidden = true;
    copilotAnswer.textContent = error.message;
    renderEmpty(citationList, "No evidence was accepted for this response.");
    renderEmpty(activityList, "The project check did not complete.");
  } finally {
    if (selectedRequestId === currentRequestId) copilotSubmit.disabled = false;
  }
}

copilotForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitCopilot(new FormData(copilotForm).get("question"));
});

document.querySelector("#defrost-guide-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const asset = form.get("asset_id");
  const start = form.get("start");
  const end = form.get("end");
  const normalizedStart = String(start).length === 16 ? `${start}:00` : start;
  const normalizedEnd = String(end).length === 16 ? `${end}:00` : end;
  const question =
    `Analyze ${asset} from ${normalizedStart} to ${normalizedEnd}. ` +
    "Did the complete defrost sequence comply with the approved rule pack? " +
    "Show the first confirmed deviation, scope, controller/firmware binding, sampling limitation, and rule citation.";
  copilotQuestion.value = question;
  await submitCopilot(question);
});

function renderCitations(citations) {
  citationList.replaceChildren();
  if (!citations.length) {
    renderEmpty(citationList, "No cited project evidence returned.");
    return 0;
  }
  const grouped = new Map();
  for (const citation of citations) {
    const locationKey = [
      citation.source_id,
      citation.section || "",
      citation.page || "",
    ].join("|");
    const current = grouped.get(locationKey);
    if (!current || citation.excerpt.length > current.excerpt.length) {
      grouped.set(locationKey, citation);
    }
  }
  for (const citation of grouped.values()) {
    const item = document.createElement("article");
    item.className = "source-item";
    const title = document.createElement("strong");
    title.textContent = citation.source;
    const identity = document.createElement("p");
    const location = [citation.category, citation.section, citation.page ? `page ${citation.page}` : null]
      .filter(Boolean)
      .join(" · ");
    identity.textContent = location;
    const excerpt = document.createElement("p");
    let excerptText = citation.excerpt;
    if (citation.source.toLowerCase().endsWith(".json")) {
      excerptText =
        "Machine-readable rule evidence. The result card shows the validated rule, controller, firmware, scope, and time limits.";
    }
    excerpt.textContent =
      excerptText.length > 500 ? `${excerptText.slice(0, 500)}…` : excerptText;
    const sourceId = document.createElement("small");
    sourceId.textContent = `Source ID: ${citation.source_id}`;
    item.append(title, identity, excerpt, sourceId);
    if (citation.source.toLowerCase().endsWith(".json")) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Raw engineering evidence";
      const raw = document.createElement("pre");
      raw.textContent = citation.excerpt;
      details.append(summary, raw);
      item.append(details);
    }
    citationList.append(item);
  }
  return grouped.size;
}

function displayPairs(values) {
  return Object.entries(values || {})
    .map(([key, value]) => `${key.replaceAll("_", " ")}: ${value}`)
    .join("; ");
}

function relevantObserved(violation) {
  const keysByCode = {
    entry_without_candidate: [
      "candidate_dwell_seconds",
      "defrost_command",
      "outdoor_temp_c",
      "outdoor_coil_temp_c",
    ],
    candidate_dwell_too_short: [
      "candidate_dwell_seconds",
      "defrost_command",
      "outdoor_temp_c",
      "outdoor_coil_temp_c",
    ],
    defrost_started_after_max_delay: [
      "initiation_delay_seconds",
      "defrost_command",
    ],
    qualified_candidate_not_started: [
      "initiation_delay_seconds",
      "defrost_command",
    ],
    outdoor_fan_on_during_defrost: ["outdoor_fan_command", "defrost_command"],
    reversing_valve_state_mismatch: [
      "reversing_valve_command",
      "defrost_command",
    ],
    defrost_ended_before_exit_condition: [
      "defrost_duration_seconds",
      "outdoor_coil_temp_c",
      "defrost_command",
    ],
    defrost_duration_exceeded: ["defrost_duration_seconds", "defrost_command"],
    outdoor_fan_started_during_recovery_delay: [
      "recovery_delay_seconds",
      "outdoor_fan_command",
    ],
  };
  const keys = keysByCode[violation.code] || Object.keys(violation.observed || {});
  return Object.fromEntries(
    keys
      .filter((key) => key in (violation.observed || {}))
      .map((key) => [key, violation.observed[key]]),
  );
}

function renderDiagnostic(diagnostic, fullAnswer) {
  diagnosticResult.replaceChildren();
  diagnosticResult.hidden = false;
  copilotAnswer.hidden = true;
  const card = document.createElement("section");
  card.className = "defrost-result-card";
  const title = document.createElement("h3");
  const verdicts = {
    compliant: "Control sequence matches the synthetic rule",
    non_compliant: "Control sequence does not match the synthetic rule",
    insufficient_data: "Not enough reliable data to decide",
    unobservable: "The complete sequence is not observable in this window",
  };
  title.textContent = verdicts[diagnostic.status] || "Defrost sequence check completed";
  const summary = document.createElement("p");
  summary.textContent = diagnostic.summary;

  const meta = document.createElement("div");
  meta.className = "diagnostic-meta";
  const entries = [
    ["Scope", "Synthetic demonstration only - not for field decisions"],
    ["Unit", diagnostic.asset_id],
    [
      "Controller / firmware",
      `${diagnostic.controller_model} / ${diagnostic.firmware_version}`,
    ],
    ["Rule", `${diagnostic.rule_id} (${diagnostic.rule_version})`],
    [
      "Window",
      `${diagnostic.window_start} to ${diagnostic.window_end} (${diagnostic.timezone})`,
    ],
    [
      "Sampling limit",
      `${diagnostic.sample_count} samples; event times are uncertain by at least +/- ${diagnostic.timestamp_uncertainty_seconds} seconds`,
    ],
  ];
  for (const [label, value] of entries) {
    const row = document.createElement("div");
    const labelElement = document.createElement("span");
    labelElement.textContent = label;
    const valueElement = document.createElement("strong");
    valueElement.textContent = value || "Not available";
    row.append(labelElement, valueElement);
    meta.append(row);
  }

  card.append(title, summary, meta);
  if (diagnostic.violations?.length) {
    const heading = document.createElement("h4");
    heading.textContent = `Confirmed deviations (${diagnostic.violations.length})`;
    const list = document.createElement("div");
    list.className = "deviation-list";
    for (const violation of diagnostic.violations) {
      const item = document.createElement("article");
      item.className = "deviation-item";
      const at = document.createElement("span");
      at.textContent = `Observed at ${violation.at} (${diagnostic.timezone})`;
      const message = document.createElement("strong");
      message.textContent = violation.message;
      const expected = document.createElement("p");
      expected.textContent = `Expected: ${displayPairs(violation.expected)}`;
      const observed = document.createElement("p");
      observed.textContent = `Observed: ${displayPairs(relevantObserved(violation))}`;
      item.append(at, message, expected, observed);
      list.append(item);
    }
    card.append(heading, list);
  }

  const safety = document.createElement("p");
  safety.className = "diagnostic-safety";
  safety.textContent =
    "This replay compares recorded commands with a synthetic rule pack. It does not prove physical root cause or authorize equipment operation.";
  const details = document.createElement("details");
  details.className = "engineering-details";
  const detailsSummary = document.createElement("summary");
  detailsSummary.textContent = "Engineering details";
  const raw = document.createElement("pre");
  raw.textContent = `${fullAnswer}\n\n${JSON.stringify(diagnostic, null, 2)}`;
  details.append(detailsSummary, raw);
  card.append(safety, details);
  diagnosticResult.append(card);
}

function renderActivities(activities) {
  activityList.replaceChildren();
  if (!activities.length) return renderEmpty(activityList, "No project checks were needed.");
  for (const activity of activities) {
    const row = document.createElement("div");
    row.className = `activity-row activity-${activity.status}`;
    const heading = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = toolLabels[activity.tool] || activity.tool;
    const status = document.createElement("span");
    status.className = "activity-status";
    status.textContent = activity.status === "completed" ? "Check executed" : "Failed";
    heading.append(name, status);
    const summary = document.createElement("span");
    summary.textContent = activity.summary;
    row.append(heading, summary);
    activityList.append(row);
  }
}

function renderEmpty(container, text) {
  const empty = document.createElement("p");
  empty.className = "empty-state";
  empty.textContent = text;
  container.append(empty);
}

async function openWorkspace(projectId) {
  const previousProjectId = confirmedProjectId;
  const previousTitle = document.querySelector("#active-workspace-name").textContent;
  const selectedEpoch = workspaceEpoch + 1;
  workspaceEpoch = selectedEpoch;
  if (copilotController) copilotController.abort();
  currentRequestId = null;
  resetProjectResults();
  resetAnalytics();
  setWorkspaceBusy(true);
  setStatus(workspaceStatus, "Opening project and refreshing its evidence…");
  try {
    const workspace = await fetchJson(
      `/api/workspaces/${encodeURIComponent(projectId)}/activate`,
      { method: "POST", headers: sameOriginHeaders },
    );
    if (workspaceEpoch !== selectedEpoch || workspace.project_id !== projectId) return;
    confirmedProjectId = projectId;
    workspaceSelect.value = projectId;
    document.querySelector("#active-workspace-name").textContent = workspace.display_name;
    await Promise.all([refreshInventory(projectId), refreshAnalytics(projectId)]);
    setStatus(workspaceStatus, `Opened ${workspace.display_name}.`);
  } catch (error) {
    if (workspaceEpoch !== selectedEpoch) return;
    confirmedProjectId = previousProjectId;
    workspaceSelect.value = previousProjectId;
    document.querySelector("#active-workspace-name").textContent = previousTitle;
    await Promise.all([refreshInventory(previousProjectId), refreshAnalytics(previousProjectId)]);
    setStatus(workspaceStatus, error.message, true);
  } finally {
    if (workspaceEpoch === selectedEpoch) setWorkspaceBusy(false);
  }
}

workspaceSelect.addEventListener("change", () => openWorkspace(workspaceSelect.value));

document.querySelector("#workspace-create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  setWorkspaceBusy(true);
  try {
    const workspace = await fetchJson("/api/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...sameOriginHeaders },
      body: JSON.stringify(Object.fromEntries(form)),
    });
    const option = document.createElement("option");
    option.value = workspace.project_id;
    option.textContent = workspace.display_name;
    workspaceSelect.append(option);
    workspaceSelect.value = workspace.project_id;
    formElement.reset();
    await openWorkspace(workspace.project_id);
  } catch (error) {
    setStatus(workspaceStatus, error.message, true);
  } finally {
    setWorkspaceBusy(false);
  }
});

const sourceFiles = document.querySelector("#source-files");
sourceFiles.addEventListener("change", () => {
  const preview = document.querySelector("#upload-preview");
  preview.replaceChildren();
  if (!sourceFiles.files.length) return;
  const list = document.createElement("ul");
  for (const file of sourceFiles.files) {
    const row = document.createElement("li");
    row.textContent = `${file.name} · ${file.size.toLocaleString()} bytes`;
    list.append(row);
  }
  preview.append(list);
});

document.querySelector("#source-upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formElement = event.currentTarget;
  const projectId = activeProjectId();
  const uploadData = new FormData(formElement);
  setWorkspaceBusy(true);
  setStatus(workspaceStatus, "Importing, validating, and indexing approved evidence…");
  try {
    const imported = await fetchJson(
      `/api/workspaces/${encodeURIComponent(projectId)}/sources`,
      { method: "POST", headers: sameOriginHeaders, body: uploadData },
    );
    if (projectId !== activeProjectId()) return;
    setStatus(workspaceStatus, `Indexed ${imported.length} source(s). Review any error status below.`);
    formElement.reset();
    document.querySelector("#upload-preview").replaceChildren();
    await Promise.all([refreshInventory(projectId), refreshAnalytics(projectId)]);
  } catch (error) {
    setStatus(workspaceStatus, error.message, true);
  } finally {
    setWorkspaceBusy(false);
  }
});

document.querySelector("#reindex-button").addEventListener("click", async () => {
  const projectId = activeProjectId();
  setWorkspaceBusy(true);
  try {
    const result = await fetchJson(
      `/api/workspaces/${encodeURIComponent(projectId)}/reindex`,
      { method: "POST", headers: sameOriginHeaders },
    );
    setStatus(workspaceStatus, `Rebuilt the evidence index with ${result.indexed_chunks} chunk(s).`);
    await Promise.all([refreshInventory(projectId), refreshAnalytics(projectId)]);
  } catch (error) {
    setStatus(workspaceStatus, error.message, true);
  } finally {
    setWorkspaceBusy(false);
  }
});

inventoryList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-delete-source]");
  if (!button) return;
  const item = button.closest("[data-source-id]");
  pendingDelete = {
    projectId: activeProjectId(),
    projectName: document.querySelector("#active-workspace-name").textContent,
    sourceId: button.dataset.deleteSource,
    filename: item.dataset.filename,
    category: item.dataset.category,
  };
  deleteTrigger = button;
  deleteError.hidden = true;
  deleteError.textContent = "";
  document.querySelector("#delete-project").textContent = pendingDelete.projectName;
  document.querySelector("#delete-filename").textContent = pendingDelete.filename;
  document.querySelector("#delete-category").textContent = pendingDelete.category;
  document.querySelector("#delete-source-id").textContent = pendingDelete.sourceId;
  deleteDialog.showModal();
  deleteCancel.focus();
});

deleteCancel.addEventListener("click", () => deleteDialog.close("cancel"));
deleteDialog.addEventListener("cancel", () => {
  deleteDialog.close("cancel");
});
deleteDialog.addEventListener("close", () => {
  const trigger = deleteTrigger;
  pendingDelete = null;
  deleteTrigger = null;
  if (trigger?.isConnected) trigger.focus();
  else workspaceSelect.focus();
});

deleteConfirm.addEventListener("click", async () => {
  if (!pendingDelete) return;
  const selected = { ...pendingDelete };
  if (selected.projectId !== activeProjectId()) {
    deleteDialog.close("cancel");
    setStatus(workspaceStatus, "Project context changed; the source was not deleted.", true);
    return;
  }
  deleteConfirm.disabled = true;
  try {
    await fetchJson(
      `/api/workspaces/${encodeURIComponent(selected.projectId)}/sources/${encodeURIComponent(selected.sourceId)}`,
      { method: "DELETE", headers: sameOriginHeaders },
    );
    deleteDialog.close("deleted");
    setStatus(workspaceStatus, `${selected.filename} was deleted and the project index was rebuilt.`);
    await Promise.all([refreshInventory(selected.projectId), refreshAnalytics(selected.projectId)]);
  } catch (error) {
    deleteError.hidden = false;
    setStatus(deleteError, `Delete failed: ${error.message}`, true);
    deleteError.focus();
  } finally {
    deleteConfirm.disabled = false;
  }
});

async function refreshInventory(projectId = activeProjectId()) {
  const sources = await fetchJson(
    `/api/workspaces/${encodeURIComponent(projectId)}/sources`,
  );
  if (projectId !== activeProjectId()) return;
  inventoryList.replaceChildren();
  const errors = sources.filter((source) => source.status === "error").length;
  inventorySummary.textContent = `${sources.length} source(s) · ${errors} error(s)`;
  if (!sources.length) return renderEmpty(inventoryList, "No project evidence imported.");
  for (const source of sources) {
    const item = document.createElement("article");
    item.className = `source-item source-${source.status}`;
    item.dataset.sourceId = source.source_id;
    item.dataset.filename = source.filename;
    item.dataset.category = source.category;
    const title = document.createElement("strong");
    title.textContent = source.filename;
    const meta = document.createElement("p");
    meta.textContent = `${source.category} · ${source.status} · ${source.parser}`;
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Audit details";
    const audit = document.createElement("p");
    audit.textContent = `ID: ${source.source_id}\nSHA-256: ${source.sha256}\nSize: ${source.size_bytes.toLocaleString()} bytes${source.error ? `\nError: ${source.error}` : ""}`;
    details.append(summary, audit);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.dataset.deleteSource = source.source_id;
    remove.setAttribute("aria-label", `Delete ${source.filename}`);
    remove.textContent = "Delete source";
    item.append(title, meta, details, remove);
    inventoryList.append(item);
  }
}

async function refreshAnalytics(projectId = activeProjectId()) {
  try {
    const summary = await fetchJson(
      `/api/workspaces/${encodeURIComponent(projectId)}/analytics/summary`,
    );
    if (projectId !== activeProjectId() || summary.project_id !== projectId) return;
    metricStrip.hidden = !summary.available;
    if (!summary.available) {
      setStatus(analyticsStatus, "Current project has no approved telemetry.csv dataset.");
      return;
    }
    setStatus(analyticsStatus, `Dataset: ${summary.dataset_filename} · current project only`);
    document.querySelector("#metric-rows").textContent = summary.row_count.toLocaleString();
    document.querySelector("#metric-power").textContent = `${summary.average_power_kw.toFixed(1)} kW`;
    document.querySelector("#metric-delta").textContent = `${summary.average_delta_t_c.toFixed(2)} °C`;
    document.querySelector("#metric-cop").textContent = summary.average_cop.toFixed(2);
  } catch (error) {
    metricStrip.hidden = true;
    setStatus(analyticsStatus, error.message, true);
  }
}

refreshInventory(activeProjectId()).catch((error) => setStatus(workspaceStatus, error.message, true));
