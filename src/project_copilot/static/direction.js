const form = document.querySelector("#direction-form");
const questionInput = document.querySelector("#direction-question");
const messages = document.querySelector("#messages");
const conversationHistory = [];

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function appendInline(container, text) {
  text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean).forEach((part) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      container.append(element("strong", "", part.slice(2, -2)));
    } else {
      container.append(document.createTextNode(part));
    }
  });
}

function renderMarkdown(container, markdown, hasStructuredTables = false) {
  let list = null;
  markdown.split("\n").forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) {
      list = null;
      return;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      list = null;
      const tag = heading[1].length === 1 ? "h2" : heading[1].length === 2 ? "h3" : "h4";
      container.append(element(tag, "answer-heading", heading[2]));
      return;
    }
    const boldHeading = line.match(/^\*\*([^*]+)\*\*[:：]?$/);
    if (boldHeading) {
      list = null;
      container.append(element("h3", "answer-heading", boldHeading[1]));
      return;
    }
    if (hasStructuredTables && line.startsWith("|") && line.endsWith("|")) return;
    if (line.startsWith("- ")) {
      if (!list) {
        list = element("ul", "answer-list");
        container.append(list);
      }
      const item = element("li");
      appendInline(item, line.slice(2));
      list.append(item);
      return;
    }
    list = null;
    const paragraph = element("p");
    appendInline(paragraph, line);
    container.append(paragraph);
  });
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

function renderCitations(citations) {
  if (!citations.length) return null;
  const wrap = element("section", "citations");
  wrap.append(element("h4", "", "参考依据"));
  citations.forEach((citation) => {
    const card = element("article", "citation-card");
    const top = element("div", "citation-top");
    const identity = element("div", "citation-identity");
    identity.append(element("strong", "", citation.filename));
    if (citation.source_status) {
      const statusClass = citation.source_status === "已废止" ? "is-superseded" : "";
      identity.append(element("span", `source-status ${statusClass}`, citation.source_status));
    }
    top.append(identity);
    top.append(element("span", "", `本次引用占比 ${citation.support_share_pct}%`));
    card.append(top);
    if (citation.source_role) card.append(element("small", "source-role", citation.source_role));
    card.append(element("p", "citation-excerpt", citation.excerpt));
    card.append(element("small", "", citation.location));
    wrap.append(card);
  });
  return wrap;
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
  renderMarkdown(body, payload.answer_markdown, payload.tables.length > 0);
  payload.tables.forEach((table) => body.append(renderTable(table)));
  payload.charts.forEach((chart) => body.append(renderChart(chart)));
  const citations = renderCitations(payload.citations);
  if (citations) body.append(citations);
  if (payload.activities?.length) {
    const labels = {
      search_project_knowledge: "已核对项目资料",
      query_hvac_database: "已计算运行数据",
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
}

async function ask(question) {
  appendUserMessage(question);
  const pending = element("p", "pending", "正在核对文档和数据……");
  messages.append(pending);
  form.querySelector("button").disabled = true;
  try {
    const response = await fetch("/api/direction/query", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Project-Copilot": "1"},
      body: JSON.stringify({question, history: conversationHistory}),
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
    form.querySelector("button").disabled = false;
    messages.lastElementChild?.scrollIntoView({behavior: "smooth", block: "end"});
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) return;
  questionInput.value = "";
  ask(question);
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => ask(button.dataset.question));
});
