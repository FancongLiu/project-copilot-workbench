const navButtons = document.querySelectorAll("[data-view]");
const viewPanels = document.querySelectorAll("[data-view-panel]");

for (const button of navButtons) {
  button.addEventListener("click", () => {
    const view = button.dataset.view;
    navButtons.forEach((item) => item.classList.toggle("is-active", item === button));
    viewPanels.forEach((panel) => {
      panel.classList.toggle("is-visible", panel.dataset.viewPanel === view);
    });
  });
}

const knowledgeForm = document.querySelector("#knowledge-form");
const knowledgeAnswer = document.querySelector("#knowledge-answer p");
const sourceList = document.querySelector("#source-list");
const sourceCount = document.querySelector("#source-count");

knowledgeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = new FormData(knowledgeForm).get("question");
  knowledgeAnswer.textContent = "Searching project evidence...";
  sourceList.replaceChildren();
  try {
    const response = await fetch("/api/knowledge/query", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Project-Copilot": "1",
      },
      body: JSON.stringify({ question }),
    });
    const result = await response.json();
    knowledgeAnswer.textContent = result.answer;
    sourceCount.textContent = String(result.citations.length);
    if (result.citations.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No supporting evidence found.";
      sourceList.append(empty);
      return;
    }
    for (const citation of result.citations) {
      const item = document.createElement("article");
      item.className = "source-item";
      const title = document.createElement("strong");
      title.textContent = citation.source;
      const excerpt = document.createElement("p");
      excerpt.textContent = citation.excerpt;
      item.append(title, excerpt);
      sourceList.append(item);
    }
  } catch (error) {
    knowledgeAnswer.textContent = "The query could not be completed.";
    sourceCount.textContent = "0";
  }
});

const analysisForm = document.querySelector("#analysis-form");
const analysisQuestion = document.querySelector("#analysis-question");
const analysisTitle = document.querySelector("#analysis-title");
const analysisSummary = document.querySelector("#analysis-summary");
const analysisSql = document.querySelector("#analysis-sql");
const analysisChart = document.querySelector("#analysis-chart");

for (const button of document.querySelectorAll("[data-question]")) {
  button.addEventListener("click", () => {
    analysisQuestion.value = button.dataset.question;
    analysisForm.requestSubmit();
  });
}

analysisForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  analysisTitle.textContent = "Running approved analysis";
  analysisSummary.textContent = "Validating the request and read-only query.";
  analysisSql.textContent = "";
  analysisChart.replaceChildren();
  try {
    const response = await fetch("/api/analytics/analyze", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Project-Copilot": "1",
      },
      body: JSON.stringify({ question: analysisQuestion.value }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || "Analysis request was rejected");
    }
    analysisTitle.textContent = result.title;
    analysisSummary.textContent = result.summary;
    analysisSql.textContent = result.sql;
    renderBars(result.rows);
  } catch (error) {
    analysisTitle.textContent = "Request rejected";
    analysisSummary.textContent = error.message;
    analysisSql.textContent = "";
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No query was executed.";
    analysisChart.append(empty);
  }
});

function renderBars(rows) {
  analysisChart.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "The approved query returned no rows.";
    analysisChart.append(empty);
    return;
  }
  const valueKey = Object.keys(rows[0]).find((key) => key !== "timestamp");
  const values = rows.map((row) => Number(row[valueKey]));
  const maxValue = Math.max(...values, 1);
  values.forEach((value) => {
    const bar = document.createElement("div");
    bar.className = "chart-bar";
    bar.style.height = `${Math.max(8, (value / maxValue) * 220)}px`;
    bar.dataset.value = value.toFixed(2);
    analysisChart.append(bar);
  });
}
