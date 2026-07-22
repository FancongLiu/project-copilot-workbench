const form = document.querySelector("#direction-form");
const questionInput = document.querySelector("#direction-question");
const messages = document.querySelector("#messages");
const conversation = document.querySelector("#conversation");
const fileInput = document.querySelector("#direction-files");
const uploadStatus = document.querySelector("#upload-status");
const uploadButton = document.querySelector(".upload-button");
const directionShell = document.querySelector("[data-testid='direction-chat']");
const architecture = directionShell?.dataset.architecture || "baseline";
const promptQueue = document.querySelector("#prompt-queue");
const promptQueueItems = document.querySelector("#prompt-queue-items");
const evidenceWorkbench = document.querySelector("#evidence-workbench");
const evidenceWorkbenchContent = document.querySelector("#evidence-workbench-content");
const evidenceWorkbenchClose = document.querySelector("#evidence-workbench-close");
const artifactCanvas = document.querySelector("#artifact-canvas");
const artifactCanvasContent = document.querySelector("#artifact-canvas-content");
const artifactCanvasClose = document.querySelector("#artifact-canvas-close");
const projectMap = document.querySelector("[data-testid='project-map']");
const projectMapExpand = document.querySelector("#project-map-expand");
const projectMapClose = document.querySelector("#project-map-close");
const conversationHistory = [];
const queuedPrompts = [];
let requestInFlight = false;
window.projectGraph = null;
window.projectGraphSummarized = false;
window.projectGraphLoading = null;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const internalPathPatterns = [
  /\b(?:background|company|configuration|datasets|docs|project\.local|runtime|src)[\\/][^\s<>"'`，。；：、)\]}]+/gi,
  /\b[A-Za-z]:[\\/][^\s<>"'`，。；：、)\]}]+/g,
];
const posixAbsolutePathPattern = /(^|[\s(（【:：，,；;])(\/(?:[^/\s<>"'`，。；：、)\]}]+\/)+[^/\s<>"'`，。；：、)\]}]+)/g;

function hideInternalPaths(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);
  textNodes.forEach((node) => {
    let text = node.textContent;
    internalPathPatterns.forEach((pattern) => {
      text = text.replace(pattern, "");
    });
    node.textContent = text
      .replace(posixAbsolutePathPattern, (_match, prefix) => prefix)
      .replace(/\s{2,}/g, " ");
  });
}

function safeMarkdownFragment(markdown) {
  if (!window.marked || !window.DOMPurify) {
    throw new Error("Markdown renderer unavailable");
  }
  const normalizedMarkdown = String(markdown || "").replace(
    /(\*\*[^*\n]+\*\*)(?=[\u3400-\u9fff])/gu,
    "$1 ",
  );
  const template = document.createElement("template");
  template.innerHTML = window.DOMPurify.sanitize(
    window.marked.parse(normalizedMarkdown, {gfm: true, breaks: true}),
    {
      FORBID_TAGS: ["embed", "form", "iframe", "img", "object", "script", "style"],
      FORBID_ATTR: ["style"],
    },
  );
  template.content.querySelectorAll("a").forEach((link) => {
    const label = element("span", "inline-source-label", link.textContent || "参考资料");
    link.replaceWith(label);
  });
  hideInternalPaths(template.content);
  return template.content;
}

function isSecondaryContext(line) {
  const normalized = line.replace(/^[-*#\s]+/, "").replace(/\*\*/g, "").trim();
  const prefixes = [
    "\u7edf\u8ba1\u65f6\u6bb5",
    "\u7edf\u8ba1\u8303\u56f4",
    "\u6570\u636e\u6765\u81ea",
    "\u6570\u636e\u6765\u6e90",
    "\u8be5\u7ed3\u8bba",
    "\u672c\u7ed3\u8bba",
    "\u7531\u4e8e",
    "\u53e3\u5f84",
    "\u9650\u5236",
    "\u8bf4\u660e",
  ];
  return prefixes.some((prefix) => normalized.startsWith(prefix));
}

function renderMarkdown(container, markdown, hasStructuredTables = false) {
  const fragment = safeMarkdownFragment(markdown);
  fragment.querySelectorAll("h1, h2, h3, h4").forEach((heading) => {
    heading.classList.add("answer-heading");
  });
  fragment.querySelectorAll("ul, ol").forEach((list) => {
    list.classList.add("answer-list");
  });
  fragment.querySelectorAll("table").forEach((table) => {
    if (hasStructuredTables) table.remove();
    else table.classList.add("answer-table");
  });
  const contextNodes = Array.from(fragment.children).filter((node) =>
    node.tagName === "P" && isSecondaryContext(node.textContent || ""),
  );
  contextNodes.forEach((node) => node.remove());
  container.append(fragment);
  if (contextNodes.length) {
    const context = element("section", "answer-context");
    context.append(element("strong", "", "\u6570\u636e\u8303\u56f4\u4e0e\u9650\u5236"));
    contextNodes.forEach((node) => context.append(node));
    container.append(context);
  }
}

function renderTable(table) {
  const wrap = element("section", "result-card");
  wrap.append(element("h4", "", table.title));
  const grid = element("table", "answer-table");
  const head = element("thead");
  const headerRow = element("tr");
  table.columns.forEach((column) => headerRow.append(element("th", "", column)));
  head.append(headerRow);
  grid.append(head);
  const body = element("tbody");
  table.rows.forEach((row) => {
    const rowNode = element("tr");
    row.forEach((value) => rowNode.append(element("td", "", value)));
    body.append(rowNode);
  });
  grid.append(body);
  wrap.append(grid);
  return wrap;
}

function renderChart(chart) {
  const wrap = element("section", "result-card chart-card");
  wrap.append(element("h4", "", chart.title));
  const width = 640;
  const height = 190;
  const padding = 28;
  const values = chart.points.map((point) => point.value);
  const min = chart.kind === "bar" ? 0 : Math.min(...values) - 0.4;
  const max = chart.kind === "bar" ? Math.max(...values) * 1.1 : Math.max(...values) + 0.4;
  const x = (index) => padding + (index * (width - padding * 2)) / Math.max(values.length - 1, 1);
  const y = (value) => height - padding - ((value - min) * (height - padding * 2)) / Math.max(max - min, 1);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", chart.title);
  svg.classList.add("line-chart");
  if (chart.kind === "bar") {
    const barWidth = Math.max(12, (width - padding * 2) / Math.max(values.length, 1) * 0.55);
    chart.points.forEach((point, index) => {
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", x(index) - barWidth / 2);
      rect.setAttribute("y", y(point.value));
      rect.setAttribute("width", barWidth);
      rect.setAttribute("height", Math.max(2, height - padding - y(point.value)));
      rect.setAttribute("rx", "4");
      rect.setAttribute("fill", "#0f766e");
      svg.append(rect);
    });
  } else {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", chart.points.map((point, index) => `${index ? "L" : "M"} ${x(index)} ${y(point.value)}`).join(" "));
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "#0f766e");
    path.setAttribute("stroke-width", "4");
    path.setAttribute("stroke-linejoin", "round");
    svg.append(path);
  }
  wrap.append(svg);
  const scale = element("div", "chart-scale");
  scale.append(element("span", "", chart.points[0]?.label || ""));
  scale.append(element("strong", "", `${Math.min(...values).toFixed(1)}–${Math.max(...values).toFixed(1)} ${chart.unit}`));
  scale.append(element("span", "", chart.points.at(-1)?.label || ""));
  wrap.append(scale);
  return wrap;
}

function citationPathParts(citation) {
  const filename = String(citation.filename || "").trim();
  const rawLocation = String(citation.location || filename).replaceAll("\\", "/");
  const filenameIndex = filename ? rawLocation.lastIndexOf(filename) : -1;
  const cleanLocation = filenameIndex >= 0
    ? rawLocation.slice(0, filenameIndex + filename.length)
    : rawLocation;
  const parts = cleanLocation.split("/").map((part) => part.trim()).filter(Boolean);
  if (!parts.length || parts.at(-1) !== filename) parts.push(filename);
  return ["当前项目", ...parts];
}

function renderEvidencePath(citations) {
  const details = element("details", "evidence-path");
  details.append(element("summary", "", "查看检索路径"));
  const unique = new Map();
  citations.forEach((citation) => {
    unique.set(`${citation.filename}|${citation.location || ""}`, citation);
  });
  unique.forEach((citation) => {
    const row = element("div", "evidence-path-row");
    citationPathParts(citation).forEach((part, index, parts) => {
      row.append(element("span", index === parts.length - 1 ? "is-file" : "", part));
      if (index < parts.length - 1) row.append(element("i", "", "→"));
    });
    details.append(row);
  });
  return details;
}

function renderCitationCard(citation) {
  const card = element("article", "citation-card");
  const top = element("div", "citation-top");
  const identity = element("div", "citation-identity");
  identity.append(element("strong", "", citation.filename));
  if (citation.source_status) {
    const statusClass = citation.source_status === "已废止" ? "is-superseded" : "";
    identity.append(element("span", `source-status ${statusClass}`, citation.source_status));
  }
  top.append(identity);
  card.append(top);
  if (citation.source_role) card.append(element("small", "source-role", citation.source_role));
  card.append(element("p", "citation-excerpt", citation.excerpt));
  return card;
}

function openEvidenceWorkbench(citations) {
  if (!evidenceWorkbench || !evidenceWorkbenchContent) return;
  evidenceWorkbenchContent.replaceChildren(renderEvidencePath(citations));
  citations.forEach((citation) => {
    evidenceWorkbenchContent.append(renderCitationCard(citation));
  });
  evidenceWorkbench.hidden = false;
  directionShell.classList.add("panel-open");
}

function renderCitations(citations) {
  if (!citations.length) return null;
  const filenames = [...new Set(citations.map((citation) => citation.filename))];
  const visibleFilenames = filenames.slice(0, 2);
  const citationLabel = filenames.length > 2
    ? `参考资料：${visibleFilenames.join(" · ")} 等 ${filenames.length} 个`
    : `参考资料：${visibleFilenames.join(" · ")}`;
  if (architecture === "evidence") {
    const trigger = element(
      "button",
      "evidence-panel-trigger",
      citationLabel,
    );
    trigger.type = "button";
    trigger.addEventListener("click", () => openEvidenceWorkbench(citations));
    return trigger;
  }
  const wrap = element("details", "citations");
  wrap.append(element("summary", "", citationLabel));
  wrap.append(renderEvidencePath(citations));
  citations.forEach((citation) => {
    wrap.append(renderCitationCard(citation));
  });
  return wrap;
}

evidenceWorkbenchClose?.addEventListener("click", () => {
  evidenceWorkbench.hidden = true;
  directionShell.classList.remove("panel-open");
});

function renderCanvasSummary(markdown) {
  const summary = element("p", "canvas-chat-summary");
  const fragment = safeMarkdownFragment(markdown);
  const first = fragment.querySelector("p, li");
  if (!first) {
    summary.textContent = "已生成工程分析。";
    return summary;
  }
  while (first.firstChild) summary.append(first.firstChild);
  return summary;
}

function shouldAutoOpenArtifact(payload) {
  return window.matchMedia("(min-width: 801px)").matches
    && (payload.tables.length > 0 || payload.charts.length > 0 || payload.answer_markdown.length > 800);
}

function renderArtifactCanvas(payload) {
  if (!artifactCanvas || !artifactCanvasContent) return;
  artifactCanvasContent.replaceChildren();
  const answer = element("section", "artifact-answer");
  renderMarkdown(answer, payload.answer_markdown, payload.tables.length > 0);
  artifactCanvasContent.append(answer);
  payload.tables.forEach((table) => artifactCanvasContent.append(renderTable(table)));
  payload.charts.forEach((chart) => artifactCanvasContent.append(renderChart(chart)));
  const citations = renderCitations(payload.citations);
  if (citations) artifactCanvasContent.append(citations);
  artifactCanvas.hidden = false;
  directionShell.classList.add("panel-open");
}

artifactCanvasClose?.addEventListener("click", () => {
  artifactCanvas.hidden = true;
  directionShell.classList.remove("panel-open");
});

async function loadProjectGraph() {
  const container = document.querySelector("#project-graph");
  if (!container || !window.cytoscape) return;
  try {
    const response = await fetch("/api/direction/graph");
    if (!response.ok) throw new Error("graph unavailable");
    const payload = await response.json();
    window.projectGraphSummarized = payload.summarized === true;
    window.projectGraphNeedsRefresh = false;
    window.projectGraph?.destroy();
    window.projectGraph = window.cytoscape({
      container,
      elements: [
        ...payload.nodes.map((node) => ({data: node})),
        ...payload.edges.map((edge) => ({data: edge})),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#7f918a",
            label: "data(label)",
            color: "#31443d",
            "font-size": 9,
            "text-wrap": "wrap",
            "text-max-width": 120,
            "text-valign": "bottom",
            "text-halign": "center",
            "text-margin-y": 7,
            shape: "ellipse",
            width: 11,
            height: 11,
          },
        },
        {selector: "node[kind = 'project']", style: {"background-color": "#173b34", width: 22, height: 22, "font-size": 10, "font-weight": 800}},
        {selector: "node[kind = 'folder']", style: {"background-color": "#83b6a2", width: 15, height: 15, "font-weight": 700}},
        {selector: "node[kind = 'file']", style: {"background-color": "#9ba8a3", width: 10, height: 10}},
        {selector: "node[kind = 'file'][category = 'dataset']", style: {"background-color": "#5b8ec7"}},
        {selector: "edge", style: {width: 1, "line-color": "#c5d0cb", "curve-style": "bezier", opacity: 0.8}},
        {selector: ".is-path", style: {"background-color": "#35a878", "line-color": "#35a878", opacity: 1, "transition-property": "background-color, line-color, opacity", "transition-duration": "180ms"}},
        {selector: ".is-cited", style: {"background-color": "#f0aa3c", "border-color": "#d98513", "border-width": 3, width: 16, height: 16}},
        {selector: ".is-hidden", style: {display: "none"}},
      ],
      layout: {
        name: "cose",
        animate: true,
        animationDuration: 420,
        fit: true,
        padding: 22,
        idealEdgeLength: 52,
        nodeRepulsion: 6200,
        gravity: 0.35,
      },
      minZoom: 0.18,
      maxZoom: 2.5,
    });
    if (window.latestProjectPath) highlightProjectPath(window.latestProjectPath);
  } catch (error) {
    container.textContent = "\u9879\u76ee\u5730\u56fe\u6682\u65f6\u4e0d\u53ef\u7528";
  }
}

function highlightProjectPath(payload) {
  window.latestProjectPath = payload;
  const graph = window.projectGraph;
  if (!graph) return;
  graph.elements(".ephemeral").remove();
  graph.elements().removeClass("is-path is-cited is-hidden");
  graph.$("#project").addClass("is-path");
  let addedCitationNode = false;
  (payload.citations || []).forEach((citation, index) => {
    const citationLocation = String(citation.location || "").replaceAll("\\", "/");
    const exists = graph.nodes("[kind = 'file']").some((node) => {
      const nodeLocation = String(node.data("location") || "");
      return nodeLocation && citationLocation === nodeLocation;
    });
    if (exists) return;
    const role = String(citation.source_role || "background");
    const category = ["background", "configuration", "meeting", "decision", "SOP", "dataset"].includes(role)
      ? role
      : String(citation.filename).toLowerCase().endsWith(".csv") ? "dataset" : "background";
    const categoryNode = graph.$("#project");
    const nodeId = `query-file-${index}`;
    graph.add([
      {group: "nodes", data: {id: nodeId, label: citation.filename, kind: "file", category}, classes: "ephemeral"},
      {group: "edges", data: {id: `query-edge-${index}`, source: categoryNode.id(), target: nodeId}, classes: "ephemeral"},
    ]);
    addedCitationNode = true;
  });
  const citedLocations = (payload.citations || []).map((citation) =>
    String(citation.location || "").replaceAll("\\", "/"),
  );
  graph.nodes("[kind = 'file']").forEach((node) => {
    const location = String(node.data("location") || "");
    if (!citedLocations.some((cited) => location && cited === location)) return;
    node.addClass("is-cited");
    node.predecessors().addClass("is-path");
  });
  if (addedCitationNode || citedLocations.length) applyProjectGraphMode();
}

function applyProjectGraphMode() {
  const graph = window.projectGraph;
  if (!graph || !projectMap) return;
  graph.elements().removeClass("is-hidden");
  const expanded = projectMap.dataset.expanded === "true";
  if (!expanded) {
    const focus = graph.elements(".is-path, .is-cited");
    graph.elements().addClass("is-hidden");
    focus.removeClass("is-hidden");
    focus.connectedEdges().removeClass("is-hidden");
  }
  const visible = graph.elements().not(".is-hidden");
  visible.layout({
    name: "cose",
    animate: true,
    animationDuration: 320,
    fit: true,
    padding: expanded ? 42 : 22,
    idealEdgeLength: expanded ? 64 : 48,
    nodeRepulsion: expanded ? 7600 : 5200,
    gravity: expanded ? 0.2 : 0.45,
    randomize: false,
  }).run();
}

async function showProjectMap(payload) {
  if (!projectMap || !window.matchMedia("(min-width: 900px)").matches) return;
  if (!(payload.citations || []).length && !(payload.activities || []).length) return;
  projectMap.hidden = false;
  window.latestProjectPath = payload;
  if (!window.projectGraph || window.projectGraphNeedsRefresh) {
    if (!window.projectGraphLoading) {
      window.projectGraphLoading = loadProjectGraph().finally(() => {
        window.projectGraphLoading = null;
      });
    }
    await window.projectGraphLoading;
  } else {
    highlightProjectPath(payload);
  }
}

function appendUserMessage(question) {
  const article = element("article", "message user-message");
  article.append(element("div", "message-body", question));
  messages.append(article);
}

function appendAssistantMessage(payload) {
  const article = element("article", "message assistant-message");
  article.append(element("div", "avatar", "AI"));
  const body = element("div", "message-body");
  const states = {
    grounded: ["已依据项目证据回答", "is-grounded"],
    clarification: ["需要补充信息", "is-clarification"],
    refused: ["已拒绝不安全操作", "is-refused"],
    failed: ["回答失败，未采用无依据结果", "is-failed"],
    demo: ["离线方向演示", "is-demo"],
  };
  const state = states[payload.grounding_status] || states.failed;
  body.append(element("span", `answer-state ${state[1]}`, state[0]));
  if (architecture === "canvas") {
    body.append(renderCanvasSummary(payload.answer_markdown));
    const openCanvas = element("button", "canvas-open-button", "打开工程成果");
    openCanvas.type = "button";
    openCanvas.addEventListener("click", () => renderArtifactCanvas(payload));
    body.append(openCanvas);
    if (shouldAutoOpenArtifact(payload)) renderArtifactCanvas(payload);
  } else {
    renderMarkdown(body, payload.answer_markdown, payload.tables.length > 0);
    payload.tables.forEach((table) => body.append(renderTable(table)));
    payload.charts.forEach((chart) => body.append(renderChart(chart)));
    const citations = renderCitations(payload.citations);
    if (citations) body.append(citations);
  }
  if (payload.activities?.length) {
    const labels = {
      search_project_knowledge: "已核对项目资料",
      query_hvac_database: "已计算运行数据",
      "data-quality": "已检查数据质量",
      "cop-ranking": "已计算能效排名",
      schema: "已核对数据字段",
      inspect_hvac_snapshot: "已计算运行数据",
      inspect_configuration_change_effect: "已计算运行数据",
      inspect_metric_extreme: "已计算运行数据",
      inspect_configuration_history: "已核对配置历史",
      ask_for_clarification: "需要补充分析口径",
    };
    const trace = element("p", "human-trace");
    const completed = [...new Set(payload.activities
      .filter((item) => item.status === "completed")
      .map((item) => labels[item.tool])
      .filter(Boolean))];
    trace.textContent = completed.join(" · ");
    if (trace.textContent) body.append(trace);
  }
  article.append(body);
  messages.append(article);
  scrollConversationToMessage(article);
  void showProjectMap(payload);
}

function scrollConversationToBottom() {
  if (!conversation) return;
  conversation.scrollTo({top: conversation.scrollHeight, behavior: "smooth"});
}

function scrollConversationToMessage(message) {
  if (!conversation || !message) return;
  messages.style.paddingBottom = `${Math.max(24, conversation.clientHeight - 96)}px`;
  const conversationRect = conversation.getBoundingClientRect();
  const messageRect = message.getBoundingClientRect();
  const top = Math.max(
    0,
    conversation.scrollTop + messageRect.top - conversationRect.top - 12,
  );
  const previousBehavior = conversation.style.scrollBehavior;
  conversation.style.scrollBehavior = "auto";
  conversation.scrollTop = top;
  conversation.style.scrollBehavior = previousBehavior;
}

function renderPromptQueue() {
  if (!promptQueue || !promptQueueItems) return;
  promptQueueItems.replaceChildren();
  queuedPrompts.forEach((entry, index) => {
    const item = element("div", "queued-prompt");
    item.append(element("span", "", entry.question));
    const remove = element("button", "", "移除");
    remove.type = "button";
    remove.addEventListener("click", () => {
      queuedPrompts.splice(index, 1);
      renderPromptQueue();
    });
    item.append(remove);
    promptQueueItems.append(item);
  });
  promptQueue.hidden = queuedPrompts.length === 0;
}

function submitQuestion(question, workflowId = null) {
  if (requestInFlight) {
    queuedPrompts.push({question, workflowId});
    if (architecture === "conversation") renderPromptQueue();
    return;
  }
  ask(question, workflowId);
}

async function ask(question, workflowId = null) {
  requestInFlight = true;
  appendUserMessage(question);
  const pending = element("p", "pending", "正在核对文档和数据……");
  messages.append(pending);
  if (architecture !== "conversation") form.querySelector("button").disabled = true;
  try {
    const response = await fetch("/api/direction/query", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Project-Copilot": "1"},
      body: JSON.stringify({
        question,
        history: conversationHistory,
        workflow_id: workflowId,
      }),
    });
    if (!response.ok) throw new Error("查询失败");
    const payload = await response.json();
    appendAssistantMessage(payload);
    conversationHistory.push(
      {role: "user", content: question},
      {role: "assistant", content: payload.answer_markdown.slice(0, 2000)},
    );
    if (conversationHistory.length > 6) {
      conversationHistory.splice(0, conversationHistory.length - 6);
    }
  } catch (error) {
    appendAssistantMessage({
      answer_markdown: "### 查询失败\n\n没有生成或采用任何分析结果。请检查服务后重试。",
      tables: [], charts: [], citations: [], activities: [],
      grounding_status: "failed", refused: true, clarification: false,
    });
  } finally {
    pending.remove();
    requestInFlight = false;
    form.querySelector("button").disabled = false;
    if (queuedPrompts.length) {
      const nextPrompt = queuedPrompts.shift();
      if (architecture === "conversation") renderPromptQueue();
      ask(nextPrompt.question, nextPrompt.workflowId);
    }
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) return;
  questionInput.value = "";
  submitQuestion(question);
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => submitQuestion(
    button.dataset.question,
    button.dataset.workflow || null,
  ));
});

if (fileInput) {
  fileInput.addEventListener("change", async () => {
    if (!fileInput.files.length) return;
    const data = new FormData();
    [...fileInput.files].forEach((file) => data.append("files", file, file.name));
    uploadButton?.classList.add("is-busy");
    uploadStatus.textContent = `\u6b63\u5728\u5904\u7406 ${fileInput.files.length} \u4e2a\u6587\u4ef6\u2026`;
    try {
      const response = await fetch("/api/direction/sources", {
        method: "POST",
        headers: {"X-Project-Copilot": "1"},
        body: data,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "upload failed");
      const failed = payload.files.filter((file) => file.status !== "indexed");
      if (failed.length) {
        const failedNames = failed.map((file) => file.filename).join("\u3001");
        throw new Error(`\u672a\u5b8c\u6210\u89e3\u6790\uff1a${failedNames}`);
      }
      const filenames = payload.files.map((file) => file.filename).join("\u3001");
      uploadStatus.textContent = `\u5df2\u5b8c\u6210\u5165\u5e93\uff1a${filenames}`;
      window.projectGraphNeedsRefresh = true;
      if (projectMap?.dataset.collapsed !== "true") await loadProjectGraph();
    } catch (error) {
      uploadStatus.textContent = `\u6587\u4ef6\u5904\u7406\u5931\u8d25\uff1a${error.message}`;
    } finally {
      uploadButton?.classList.remove("is-busy");
      fileInput.value = "";
    }
  });
}

projectMapExpand?.addEventListener("click", () => {
  if (!projectMap || !window.projectGraph) return;
  const expanded = projectMap.dataset.expanded !== "true";
  projectMap.dataset.expanded = String(expanded);
  projectMapExpand.setAttribute("aria-expanded", String(expanded));
  projectMapExpand.textContent = expanded ? "还原" : "展开";
  window.projectGraph.resize();
  applyProjectGraphMode();
});

projectMapClose?.addEventListener("click", () => {
  if (!projectMap) return;
  projectMap.hidden = true;
  projectMap.dataset.expanded = "false";
  projectMapExpand?.setAttribute("aria-expanded", "false");
  if (projectMapExpand) projectMapExpand.textContent = "展开";
});
