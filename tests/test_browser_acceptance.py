import os
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.mark.skipif(
    not os.getenv("PROJECT_COPILOT_BROWSER_URL"),
    reason="browser acceptance server is not running",
)
def test_direction_layout_keeps_composer_fixed_and_deemphasizes_context(
    tmp_path: Path,
) -> None:
    base_url = os.environ["PROJECT_COPILOT_BROWSER_URL"].rstrip("/")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(base_url, wait_until="networkidle")

        expect(page.get_by_test_id("direction-chat")).to_be_visible()
        expect(page.get_by_test_id("project-map")).to_be_hidden()
        expect(page.locator('a[href="/workbench"]')).to_have_count(0)
        initial_composer_bottom = page.locator("#direction-form").evaluate(
            "node => node.getBoundingClientRect().bottom"
        )

        page.evaluate(
            """() => {
                const payload = {
                  grounding_status: 'grounded',
                  answer_markdown: [
                    '## 结论',
                    'HP-03 在本次快照期内最节能。',
                    '1. HP-03：4.001643',
                    '2. HP-02：3.995067',
                    'CR-017 自 **2026-01-16 12:00（+08:00）**生效。',
                    '依据[当前资产台账](background/asset-register.md)进行核对。',
                    '另一份依据为[控制规范](docs/spec(v2).md)。',
                    '裸路径 docs/raw/internal.md 和 C:\\private\\runtime\\secret.md 不应显示。',
                    '也不要显示 datasets/telemetry.csv、configuration/current-unit-configuration.md、company/runtime/state.json 或 /workspace/private/index.sqlite。',
                    '<img src=x onerror="window.projectCopilotPwned=1">',
                    '### [分析规范](docs/research/2026-07-16-hvac-engineer-benchmark.md)',
                    '统计时段为 2026-01-15 至 2026-01-17，数据来自 telemetry.csv。',
                    '该结论未校正室外温度和负荷差异，不宜作为固有效率评级。'
                  ].join('\\n\\n'),
                  tables: [], charts: [],
                  citations: [
                    {
                      filename: '7月15日控制评审会议纪要.docx',
                      excerpt: '会议确认 HP-03 调整方案。',
                      location: '会议记录/2026年/7月/7月15日控制评审会议纪要.docx',
                      support_share_pct: 60
                    },
                    {
                      filename: 'HP-03机组配置表.xlsx',
                      excerpt: 'HP-03 当前参数配置。',
                      location: '机组资料/HP-03/HP-03机组配置表.xlsx',
                      support_share_pct: 40
                    }
                  ],
                  activities: [{tool: 'query_hvac_database', status: 'completed'}]
                };
                for (let index = 0; index < 12; index += 1) appendAssistantMessage(payload);
              }"""
        )

        composer_bottom = page.locator("#direction-form").evaluate(
            "node => node.getBoundingClientRect().bottom"
        )
        assert abs(composer_bottom - initial_composer_bottom) <= 1
        assert composer_bottom <= 800
        assert page.locator("#conversation").evaluate(
            "node => node.scrollHeight > node.clientHeight"
        )
        assert page.evaluate(
            "document.documentElement.scrollHeight <= window.innerHeight + 1"
        )

        last_answer = page.locator("#messages .assistant-message").last
        page.wait_for_timeout(500)
        project_map = page.get_by_test_id("project-map")
        expect(project_map).to_be_visible()
        project_map.locator("#project-map-expand").click()
        expect(project_map).to_have_attribute("data-expanded", "true")
        assert project_map.evaluate(
            "node => node.getBoundingClientRect().width > window.innerWidth * 0.8"
        )
        project_map.locator("#project-map-expand").click()
        expect(project_map).to_have_attribute("data-expanded", "false")
        conversation_top = page.locator("#conversation").evaluate(
            "node => node.getBoundingClientRect().top"
        )
        answer_top = last_answer.evaluate("node => node.getBoundingClientRect().top")
        assert conversation_top <= answer_top <= conversation_top + 40
        expect(last_answer.locator("ol li")).to_have_count(2)
        expect(
            last_answer.locator("strong").filter(has_text="2026-01-16 12:00（+08:00）")
        ).to_have_count(1)
        expect(last_answer.locator(".answer-context p")).to_have_count(2)
        citation_summary = last_answer.locator(".citations > summary")
        expect(citation_summary).to_contain_text("7月15日控制评审会议纪要.docx")
        expect(citation_summary).to_contain_text("HP-03机组配置表.xlsx")
        citation_summary.click()
        expect(last_answer.locator(".citation-card")).to_have_count(2)
        expect(last_answer).to_contain_text("当前资产台账")
        expect(last_answer).to_contain_text("分析规范")
        expect(last_answer).not_to_contain_text("background/asset-register.md")
        expect(last_answer).not_to_contain_text(
            "docs/research/2026-07-16-hvac-engineer-benchmark.md"
        )
        expect(last_answer).to_contain_text("控制规范")
        expect(last_answer).not_to_contain_text("docs/spec(v2).md")
        expect(last_answer).not_to_contain_text("docs/raw/internal.md")
        expect(last_answer).not_to_contain_text("C:\\private\\runtime\\secret.md")
        expect(last_answer).not_to_contain_text("datasets/telemetry.csv")
        expect(last_answer).not_to_contain_text(
            "configuration/current-unit-configuration.md"
        )
        expect(last_answer).not_to_contain_text("company/runtime/state.json")
        expect(last_answer).not_to_contain_text("/workspace/private/index.sqlite")
        expect(last_answer.locator("img")).to_have_count(0)
        assert page.evaluate("window.projectCopilotPwned") is None
        path_details = last_answer.locator(".evidence-path")
        expect(path_details.locator("summary")).to_contain_text("查看检索路径")
        path_details.locator("summary").click()
        expect(path_details.locator(".evidence-path-row")).to_have_count(2)
        expect(path_details).to_contain_text("会议记录")
        expect(path_details).to_contain_text("7月15日控制评审会议纪要.docx")
        main_size = last_answer.locator(".message-body > p").first.evaluate(
            "node => parseFloat(getComputedStyle(node).fontSize)"
        )
        context_size = last_answer.locator(".answer-context p").first.evaluate(
            "node => parseFloat(getComputedStyle(node).fontSize)"
        )
        assert context_size < main_size

        browser.close()


@pytest.mark.skipif(
    not os.getenv("PROJECT_COPILOT_BROWSER_URL"),
    reason="browser acceptance server is not running",
)
def test_evidence_graph_highlights_only_the_cited_dataset() -> None:
    base_url = os.environ["PROJECT_COPILOT_BROWSER_URL"].rstrip("/")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route(
            "**/api/direction/graph",
            lambda route: route.fulfill(
                json={
                    "nodes": [
                        {"id": "project", "label": "HVAC", "kind": "project"},
                        {
                            "id": "datasets",
                            "label": "datasets",
                            "kind": "folder",
                            "location": "datasets",
                        },
                        {
                            "id": "telemetry",
                            "label": "telemetry.csv",
                            "kind": "file",
                            "category": "dataset",
                            "location": "datasets/telemetry.csv",
                        },
                        {
                            "id": "config-history",
                            "label": "config_history.csv",
                            "kind": "file",
                            "category": "dataset",
                            "location": "datasets/config_history.csv",
                        },
                    ],
                    "edges": [
                        {
                            "id": "project-datasets",
                            "source": "project",
                            "target": "datasets",
                            "kind": "contains",
                        },
                        {
                            "id": "datasets-telemetry",
                            "source": "datasets",
                            "target": "telemetry",
                            "kind": "contains",
                        },
                        {
                            "id": "datasets-config-history",
                            "source": "datasets",
                            "target": "config-history",
                            "kind": "contains",
                        },
                    ],
                }
            ),
        )
        page.route(
            "**/api/direction/query",
            lambda route: route.fulfill(
                json={
                    "mode": "data",
                    "grounding_status": "grounded",
                    "answer_markdown": "## Conclusion\n\nConfiguration history checked.",
                    "tables": [],
                    "charts": [],
                    "citations": [
                        {
                            "filename": "config_history.csv",
                            "excerpt": "Approved configuration history.",
                            "location": "datasets/config_history.csv",
                            "source_role": "dataset",
                        }
                    ],
                    "activities": [
                        {
                            "tool": "inspect_configuration_history",
                            "status": "completed",
                        }
                    ],
                }
            ),
        )
        page.goto(base_url, wait_until="networkidle")
        page.locator("#direction-question").fill("Check the configuration history")
        page.locator("#direction-form button[type='submit']").click()

        expect(page.get_by_test_id("project-map")).to_be_visible()
        page.wait_for_function("window.projectGraph !== undefined")
        classes = page.evaluate(
            """() => ({
              config: window.projectGraph.$('#config-history').classes(),
              telemetry: window.projectGraph.$('#telemetry').classes(),
            })"""
        )
        assert "is-cited" in classes["config"]
        assert "is-cited" not in classes["telemetry"]
        assert "is-path" not in classes["telemetry"]
        browser.close()


@pytest.mark.parametrize("architecture", ["baseline", "evidence", "canvas"])
@pytest.mark.skipif(
    not os.getenv("PROJECT_COPILOT_BROWSER_URL"),
    reason="browser acceptance server is not running",
)
def test_non_queue_variants_serialize_all_question_entry_points(
    architecture: str,
) -> None:
    base_url = os.environ["PROJECT_COPILOT_BROWSER_URL"].rstrip("/")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(f"{base_url}/versions/{architecture}", wait_until="networkidle")
        page.evaluate(
            """() => {
              window.requestBodies = [];
              window.fetch = (url, options) => {
                const request = JSON.parse(options.body);
                window.requestBodies.push(request);
                return new Promise((resolve) => {
                  const delay = request.question.includes('第一问') ? 700 : 20;
                  setTimeout(() => resolve({
                    ok: true,
                    json: async () => ({
                      grounding_status: 'grounded',
                      answer_markdown: `## 结论\\n\\n已回答：${request.question}`,
                      tables: [], charts: [], citations: [], activities: []
                    })
                  }), delay);
                });
              };
            }"""
        )

        page.locator("#direction-question").fill("第一问：比较历史能耗")
        page.locator("#direction-form button[type='submit']").click()
        page.locator("[data-question]").first.click()

        expect(page.locator("#messages .assistant-message")).to_have_count(
            2, timeout=3000
        )
        requests = page.evaluate("window.requestBodies")
        assert [request["question"] for request in requests] == [
            "第一问：比较历史能耗",
            "这个项目当前的目标和未完成事项是什么？",
        ]
        assert requests[0]["history"] == []
        assert len(requests[1]["history"]) == 2
        answers = page.locator("#messages .assistant-message .message-body")
        expect(answers.nth(0)).to_contain_text("第一问：比较历史能耗")
        expect(answers.nth(1)).to_contain_text("项目当前的目标")
        browser.close()


@pytest.mark.skipif(
    not os.getenv("PROJECT_COPILOT_BROWSER_URL"),
    reason="browser acceptance server is not running",
)
def test_conversation_variant_queues_questions_while_answer_is_running() -> None:
    base_url = os.environ["PROJECT_COPILOT_BROWSER_URL"].rstrip("/")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(f"{base_url}/versions/conversation", wait_until="networkidle")
        page.evaluate(
            """() => {
              window.fetch = (url) => new Promise((resolve) => {
                setTimeout(() => resolve({
                  ok: true,
                  json: async () => ({
                    grounding_status: 'grounded',
                    answer_markdown: '## 结论\\n\\n已完成。',
                    tables: [], charts: [], citations: [], activities: []
                  })
                }), 1200);
              });
            }"""
        )

        composer = page.locator("#direction-question")
        send = page.locator("#direction-form button[type='submit']")
        composer.fill("先比较两台机组的历史能耗")
        send.click()
        expect(send).to_be_enabled()
        composer.fill("再检查异常时段并给出建议")
        send.click()

        queue = page.get_by_test_id("prompt-queue")
        expect(queue).to_be_visible()
        expect(queue).to_contain_text("再检查异常时段并给出建议")
        expect(page.locator("#messages .assistant-message")).to_have_count(
            2, timeout=3000
        )
        expect(queue).to_be_hidden()
        browser.close()


@pytest.mark.skipif(
    not os.getenv("PROJECT_COPILOT_BROWSER_URL"),
    reason="browser acceptance server is not running",
)
def test_evidence_variant_keeps_sources_out_of_answer_until_requested() -> None:
    base_url = os.environ["PROJECT_COPILOT_BROWSER_URL"].rstrip("/")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(f"{base_url}/versions/evidence", wait_until="networkidle")
        page.evaluate(
            """() => appendAssistantMessage({
              grounding_status: 'grounded',
              answer_markdown: '## 结论\\n\\nHP-03 需要优先核查。',
              tables: [], charts: [], activities: [],
              citations: [
                {
                  filename: '7月15日控制评审会议纪要.docx',
                  excerpt: '会议确认先核查阀门反馈。',
                  location: '会议记录/2026年/7月/7月15日控制评审会议纪要.docx'
                },
                {
                  filename: 'HP-03机组配置表.xlsx',
                  excerpt: '当前阀门量程为 0-100%。',
                  location: '机组资料/HP-03/HP-03机组配置表.xlsx'
                },
                {
                  filename: '2026年7月现场维修与复测记录完整版.pdf',
                  excerpt: '现场记录要求复核阀门反馈。',
                  location: '维修记录/2026年/2026年7月现场维修与复测记录完整版.pdf'
                }
              ]
            })"""
        )

        answer = page.locator("#messages .assistant-message").last
        expect(answer.locator(".citation-card")).to_have_count(0)
        trigger = answer.locator(".evidence-panel-trigger")
        expect(trigger).to_contain_text("7月15日控制评审会议纪要.docx")
        expect(trigger).to_contain_text("等 3 个")
        expect(trigger).not_to_contain_text("2026年7月现场维修与复测记录完整版.pdf")
        trigger.click()

        panel = page.get_by_test_id("evidence-workbench")
        expect(panel).to_be_visible()
        expect(panel.locator(".citation-card")).to_have_count(3)
        expect(panel).to_contain_text("会议确认先核查阀门反馈")
        expect(panel).to_contain_text("2026年7月现场维修与复测记录完整版.pdf")
        expect(panel.locator(".evidence-path-row")).to_have_count(3)
        page.locator("#evidence-workbench-close").click()
        expect(panel).to_be_hidden()
        browser.close()


@pytest.mark.skipif(
    not os.getenv("PROJECT_COPILOT_BROWSER_URL"),
    reason="browser acceptance server is not running",
)
def test_canvas_variant_keeps_full_engineering_deliverable_stable() -> None:
    base_url = os.environ["PROJECT_COPILOT_BROWSER_URL"].rstrip("/")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{base_url}/versions/canvas", wait_until="networkidle")
        page.evaluate(
            """() => appendAssistantMessage({
              grounding_status: 'grounded',
              answer_markdown: [
                '## 结论',
                'HP-03 应依据[当前资产台账](background/asset-register.md)优先核查阀门反馈。',
                '## 分问题分析',
                '历史趋势、异常窗口和建议动作均已完成。'
              ].join('\\n\\n'),
              tables: [{
                title: '机组比较',
                columns: ['机组', '异常次数'],
                rows: [['HP-03', 4], ['HP-02', 1]]
              }],
              charts: [], activities: [],
              citations: [{
                filename: 'HP-03机组配置表.xlsx',
                excerpt: '当前阀门量程为 0-100%。',
                location: '机组资料/HP-03/HP-03机组配置表.xlsx'
              }]
            })"""
        )

        answer = page.locator("#messages .assistant-message").last
        expect(answer.locator(".canvas-chat-summary")).to_contain_text("HP-03")
        expect(answer.locator(".canvas-chat-summary")).to_contain_text("当前资产台账")
        expect(answer.locator(".canvas-chat-summary")).to_contain_text(
            "优先核查阀门反馈"
        )
        expect(answer).not_to_contain_text("background/asset-register.md")
        expect(answer).not_to_contain_text("历史趋势、异常窗口和建议动作均已完成")
        canvas = page.get_by_test_id("artifact-canvas")
        expect(canvas).to_be_visible()
        expect(canvas).to_contain_text("分问题分析")
        expect(canvas.locator(".answer-table")).to_have_count(1)
        expect(canvas).to_contain_text("HP-03机组配置表.xlsx")
        page.locator("#artifact-canvas-close").click()
        expect(canvas).to_be_hidden()
        answer.locator(".canvas-open-button").click()
        expect(canvas).to_be_visible()
        browser.close()


@pytest.mark.skipif(
    not os.getenv("PROJECT_COPILOT_BROWSER_URL"),
    reason="browser acceptance server is not running",
)
def test_all_four_architectures_keep_mobile_chat_and_panels_usable() -> None:
    base_url = os.environ["PROJECT_COPILOT_BROWSER_URL"].rstrip("/")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for architecture in ("baseline", "conversation", "evidence", "canvas"):
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(
                f"{base_url}/versions/{architecture}",
                wait_until="networkidle",
            )
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth + 1"
            )
            assert page.locator("#direction-form").evaluate(
                "node => node.getBoundingClientRect().bottom <= window.innerHeight"
            )
            if architecture in {"evidence", "canvas"}:
                page.evaluate(
                    """() => appendAssistantMessage({
                      grounding_status: 'grounded',
                      answer_markdown: '## 结论\\n\\n移动端架构验收。',
                      tables: architecture === 'canvas' ? [{
                        title: '结果', columns: ['机组', '值'], rows: [['HP-03', 4]]
                      }] : [],
                      charts: [], activities: [],
                      citations: [{
                        filename: '移动端机组配置表.xlsx',
                        excerpt: '移动端证据片段。',
                        location: '机组资料/移动端机组配置表.xlsx'
                      }]
                    })""".replace("architecture", repr(architecture))
                )
                if architecture == "evidence":
                    page.locator(".evidence-panel-trigger").click()
                    panel = page.get_by_test_id("evidence-workbench")
                else:
                    panel = page.get_by_test_id("artifact-canvas")
                    expect(panel).to_be_hidden()
                    page.locator(".canvas-open-button").click()
                expect(panel).to_be_visible()
                assert panel.evaluate(
                    "node => node.getBoundingClientRect().right <= window.innerWidth"
                )
            page.close()
        browser.close()


@pytest.mark.skipif(
    not os.getenv("PROJECT_COPILOT_BROWSER_URL"),
    reason="browser acceptance server is not running",
)
def test_desktop_and_mobile_workbench_acceptance(tmp_path: Path) -> None:
    base_url = os.environ["PROJECT_COPILOT_BROWSER_URL"]
    upload_path = tmp_path / "browser-import.md"
    upload_path.write_text(
        "# Browser acceptance meeting\n\n"
        "Decision D-UI-01 approved a 5.5 C chilled-water supply setpoint.",
        encoding="utf-8",
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base_url, wait_until="networkidle")

        expect(page).to_have_title("项目知识与数据助手")
        expect(page.get_by_test_id("direction-chat")).to_be_visible()
        expect(page.locator('a[href="/workbench"]')).to_have_count(0)
        expect(page.locator("#direction-form")).to_be_visible()

        page.locator("#direction-files").set_input_files(upload_path)
        expect(page.locator("#upload-status")).to_contain_text(
            "已完成入库：browser-import.md",
            timeout=30_000,
        )

        page.route(
            "**/api/direction/query",
            lambda route: route.fulfill(
                json={
                    "mode": "combined",
                    "grounding_status": "grounded",
                    "answer_markdown": "## 结论\n\nD-UI-01 批准供水设定为 5.5 °C。",
                    "tables": [],
                    "charts": [],
                    "citations": [
                        {
                            "filename": "browser-import.md",
                            "excerpt": "Decision D-UI-01 approved a 5.5 C chilled-water supply setpoint.",
                            "location": "meeting/browser-import.md",
                            "support_share_pct": 100,
                        }
                    ],
                    "activities": [
                        {"tool": "search_project_knowledge", "status": "completed"}
                    ],
                }
            ),
        )
        page.locator("#direction-question").fill("D-UI-01 批准了什么供水设定？")
        page.locator("#direction-form button[type='submit']").click()

        answer = page.locator("#messages .assistant-message").last
        expect(answer).to_contain_text("5.5 °C")
        expect(answer.locator(".citations > summary")).to_contain_text(
            "browser-import.md"
        )
        composer_bottom = page.locator("#direction-form").evaluate(
            "node => node.getBoundingClientRect().bottom"
        )
        assert composer_bottom <= 900

        page.set_viewport_size({"width": 320, "height": 700})
        expect(page.locator("#direction-question")).to_be_visible()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
        browser.close()


# Historical V2 dashboard journey retained as a non-collected migration
# reference. The ordinary product path is the single Chat tested above.
def _legacy_desktop_and_mobile_workbench_acceptance() -> None:
    base_url = os.environ["PROJECT_COPILOT_BROWSER_URL"]
    screenshot_dir = Path(os.getenv("PROJECT_COPILOT_SCREENSHOT_DIR", "artifacts"))
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    upload_path = screenshot_dir / "browser-import.md"
    upload_path.write_text(
        "# Browser acceptance meeting\n\n"
        "Decision D-UI-01 approved a 5.5 C chilled-water supply setpoint.",
        encoding="utf-8",
    )
    project_id = f"browser-acceptance-{uuid4().hex[:8]}"
    console_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.goto(base_url, wait_until="networkidle")

        expect(page).to_have_title("Project Copilot Workbench")
        expect(page.get_by_test_id("copilot-panel")).to_be_visible()

        page.locator("button[data-view='workspace']").click()
        expect(page.get_by_test_id("workspace-panel")).to_be_visible()
        page.locator("#new-project-id").fill(project_id)
        page.locator("#new-display-name").fill("Browser Acceptance Project")
        page.locator("#workspace-create-form button[type='submit']").click()
        expect(page.locator("#active-workspace-name")).to_have_text(
            "Browser Acceptance Project"
        )

        page.locator("#source-category").select_option("decision")
        page.locator("#source-files").set_input_files(upload_path)
        page.locator("#source-upload-form button[type='submit']").click()
        expect(page.locator("#workspace-status")).to_contain_text("Indexed 1 source")
        expect(page.locator("#inventory-list")).to_contain_text("browser-import.md")
        expect(page.locator("#inventory-list")).to_contain_text("decision · indexed")

        page.locator("button[data-view='copilot']").click()
        page.locator("#copilot-question").fill(
            "What chilled-water setpoint did decision D-UI-01 approve?"
        )
        page.locator("#copilot-form button[type='submit']").click()
        expect(page.locator("#copilot-answer p")).to_contain_text("5.5 C")
        expect(page.locator("#source-list")).to_contain_text("browser-import.md")
        expect(page.locator("#tool-activity")).to_contain_text(
            "Look up meeting decisions"
        )

        page.locator("button[data-view='workspace']").click()
        delete_source_button = page.locator("#inventory-list [data-delete-source]")
        delete_source_button.click()
        delete_dialog = page.get_by_role("dialog", name="Delete project source")
        expect(delete_dialog).to_be_visible()
        expect(delete_dialog).to_contain_text("browser-import.md")
        expect(page.locator("#inventory-list")).to_contain_text("browser-import.md")
        delete_dialog.get_by_role("button", name="Cancel").click()
        expect(delete_dialog).not_to_be_visible()
        expect(delete_source_button).to_be_focused()
        expect(page.locator("#inventory-list")).to_contain_text("browser-import.md")

        delete_source_button.click()
        page.keyboard.press("Escape")
        expect(delete_dialog).not_to_be_visible()
        expect(delete_source_button).to_be_focused()

        page.route(
            "**/api/workspaces/*/sources/*",
            lambda route: (
                route.fulfill(
                    status=500,
                    content_type="application/json",
                    body='{"detail":"simulated delete failure"}',
                )
                if route.request.method == "DELETE"
                else route.continue_()
            ),
        )
        delete_source_button.click()
        delete_dialog.get_by_role("button", name="Delete this source").click()
        expect(delete_dialog).to_be_visible()
        expect(page.locator("#delete-error")).to_contain_text(
            "simulated delete failure"
        )
        expect(
            delete_dialog.get_by_role("button", name="Delete this source")
        ).to_be_enabled()
        expect(page.locator("#inventory-list")).to_contain_text("browser-import.md")
        delete_dialog.get_by_role("button", name="Cancel").click()
        page.unroute("**/api/workspaces/*/sources/*")

        delete_source_button.click()
        delete_dialog.get_by_role("button", name="Delete this source").click()
        expect(delete_dialog).not_to_be_visible()
        expect(page.locator("#inventory-list")).not_to_contain_text("browser-import.md")
        expect(page.locator("#workspace-status")).to_contain_text(
            "deleted and the project index was rebuilt"
        )

        page.screenshot(
            path=screenshot_dir / "workbench-project-files.png", full_page=True
        )

        page.locator("#workspace-select").select_option("synthetic-hvac-demo")
        expect(page.locator("#active-workspace-name")).to_have_text(
            "Synthetic HVAC Plant"
        )
        page.locator("button[data-view='copilot']").click()
        page.get_by_text("Check a defrost sequence").click()
        page.locator("#defrost-asset").fill("HP-01")
        page.locator("#defrost-start").fill("2026-07-15T15:59")
        page.locator("#defrost-end").fill("2026-07-15T16:08")
        page.locator("#defrost-guide-form button[type='submit']").click()
        expect(page.locator("#diagnostic-result")).to_contain_text(
            "Control sequence does not match the synthetic rule"
        )
        expect(page.locator("#diagnostic-result")).to_contain_text(
            "Synthetic demonstration only - not for field decisions"
        )
        expect(page.locator("#diagnostic-result")).to_contain_text("AuroraCTRL-700")
        expect(page.locator("#diagnostic-result")).to_contain_text("Expected:")
        expect(page.locator("#diagnostic-result")).to_contain_text("Observed:")
        engineer_facing_text = page.locator(".defrost-result-card").evaluate(
            """card => {
                const clone = card.cloneNode(true);
                clone.querySelector('details')?.remove();
                return clone.textContent;
            }"""
        )
        assert "entry_without_candidate" not in engineer_facing_text
        expect(page.locator(".engineering-details")).to_contain_text(
            "entry_without_candidate"
        )
        expect(page.locator("#tool-activity")).to_contain_text("Check defrost sequence")
        expect(page.locator("#tool-activity")).to_contain_text("Check executed")
        expect(page.locator("#source-list")).to_contain_text(
            "defrost-control-sequence.md"
        )

        page.screenshot(path=screenshot_dir / "workbench-desktop.png", full_page=True)

        page.locator("button[data-view='workspace']").click()
        page.locator("#workspace-select").select_option(project_id)
        expect(page.locator("#active-workspace-name")).to_have_text(
            "Browser Acceptance Project"
        )
        page.locator("button[data-view='copilot']").click()
        expect(page.locator("#source-count")).to_have_text("0")
        expect(page.locator("#source-list")).not_to_contain_text(
            "defrost-control-sequence.md"
        )
        expect(page.locator("#answer-status")).to_have_text("Ready")

        page.locator("button[data-view='workspace']").click()
        held_routes = []

        def hold_activation(route):
            held_routes.append(route)

        activation_pattern = "**/api/workspaces/synthetic-hvac-demo/activate"
        page.route(activation_pattern, hold_activation)
        page.locator("#workspace-select").select_option("synthetic-hvac-demo")
        expect(page.locator("#copilot-form button[type='submit']")).to_be_disabled()
        expect(
            page.locator("#defrost-guide-form button[type='submit']")
        ).to_be_disabled()
        page.wait_for_timeout(100)
        assert len(held_routes) == 1
        held_routes[0].fulfill(
            status=500,
            content_type="application/json",
            body='{"detail":"simulated activation failure"}',
        )
        expect(page.locator("#workspace-select")).to_have_value(project_id)
        expect(page.locator("#active-workspace-name")).to_have_text(
            "Browser Acceptance Project"
        )
        expect(page.locator("#workspace-status")).to_contain_text(
            "simulated activation failure"
        )
        page.unroute(activation_pattern)

        page.locator("button[data-view='copilot']").click()
        rendered = page.evaluate(
            """renderCitations([
                {source_id: 'shared-source', source: 'manual.pdf', category: 'SOP', section: 'Entry', page: 4, excerpt: 'Entry condition evidence'},
                {source_id: 'shared-source', source: 'manual.pdf', category: 'SOP', section: 'Exit', page: 9, excerpt: 'Exit condition evidence'}
            ])"""
        )
        assert rendered == 2
        expect(page.locator("#source-list")).to_contain_text("page 4")
        expect(page.locator("#source-list")).to_contain_text("page 9")

        page.locator("button[data-view='workspace']").click()
        page.locator("#workspace-select").select_option("synthetic-hvac-demo")
        expect(page.locator("#active-workspace-name")).to_have_text(
            "Synthetic HVAC Plant"
        )
        page.locator("button[data-view='copilot']").click()

        second_tab = browser.new_page(viewport={"width": 1100, "height": 760})
        second_tab.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        second_tab.goto(base_url, wait_until="networkidle")
        second_tab.locator("button[data-view='workspace']").click()
        second_tab.locator("#workspace-select").select_option(project_id)
        expect(second_tab.locator("#active-workspace-name")).to_have_text(
            "Browser Acceptance Project"
        )

        page.locator("#copilot-question").fill(
            "What is the current chilled-water supply setpoint?"
        )
        page.locator("#copilot-form button[type='submit']").click()
        second_tab.locator("button[data-view='copilot']").click()
        second_tab.locator("#copilot-question").fill(
            "What chilled-water setpoint did decision D-UI-01 approve?"
        )
        second_tab.locator("#copilot-form button[type='submit']").click()

        expect(page.locator("#source-list")).to_contain_text(
            "current-state-evidence-2026-07-13.md"
        )
        expect(second_tab.locator("#copilot-answer p")).to_contain_text(
            "do not contain enough evidence"
        )
        expect(second_tab.locator("#source-count")).to_have_text("0")

        page.locator("button[data-view='analytics']").click()
        expect(page.locator("#analytics-status")).to_contain_text("telemetry.csv")
        second_tab.locator("button[data-view='analytics']").click()
        expect(second_tab.locator("#analytics-status")).to_contain_text(
            "no approved telemetry.csv"
        )
        second_tab.close()

        mobile = browser.new_page(viewport={"width": 320, "height": 700})
        mobile.goto(base_url, wait_until="networkidle")
        assert mobile.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
        expect(mobile.locator("button[data-view='copilot']")).to_be_visible()
        expect(mobile.get_by_test_id("copilot-panel")).to_be_visible()

        mobile.locator("button[data-view='workspace']").click()
        mobile.locator("#workspace-select").select_option(project_id)
        expect(mobile.locator("#active-workspace-name")).to_have_text(
            "Browser Acceptance Project"
        )
        mobile.locator("#source-category").select_option("decision")
        mobile.locator("#source-files").set_input_files(upload_path)
        mobile.locator("#source-upload-form button[type='submit']").click()
        expect(mobile.locator("#inventory-list")).to_contain_text("browser-import.md")
        mobile.locator("#inventory-list [data-delete-source]").click()
        mobile_delete_dialog = mobile.get_by_role(
            "dialog", name="Delete project source"
        )
        expect(mobile_delete_dialog).to_be_visible()
        mobile_delete_dialog.get_by_role("button", name="Cancel").click()

        mobile.locator("button[data-view='copilot']").click()
        mobile.locator("#copilot-question").fill(
            "What chilled-water setpoint did decision D-UI-01 approve?"
        )
        mobile.locator("#copilot-form button[type='submit']").click()
        expect(mobile.locator("#copilot-answer p")).to_contain_text("5.5 C")
        expect(mobile.locator("#source-list")).to_contain_text("browser-import.md")

        mobile.locator("button[data-view='analytics']").click()
        expect(mobile.locator("#analytics-status")).to_contain_text(
            "no approved telemetry.csv"
        )
        mobile.locator("button[data-view='workspace']").click()
        mobile.locator("#workspace-select").select_option("synthetic-hvac-demo")
        expect(mobile.locator("#active-workspace-name")).to_have_text(
            "Synthetic HVAC Plant"
        )
        mobile.locator("button[data-view='copilot']").click()
        mobile.get_by_text("Check a defrost sequence").click()
        mobile.locator("#defrost-asset").fill("HP-01")
        mobile.locator("#defrost-start").fill("2026-07-15T15:59")
        mobile.locator("#defrost-end").fill("2026-07-15T16:08")
        mobile.locator("#defrost-guide-form button[type='submit']").click()
        expect(mobile.locator("#diagnostic-result")).to_contain_text(
            "Control sequence does not match the synthetic rule"
        )
        expect(mobile.locator("#source-list")).to_contain_text(
            "defrost-control-sequence.md"
        )
        expect(mobile.locator("#tool-activity")).to_contain_text("Check executed")
        mobile.screenshot(
            path=screenshot_dir / "workbench-mobile-defrost.png", full_page=True
        )

        mobile.locator("#copilot-question").fill("Stop the live chiller equipment now")
        mobile.locator("#copilot-form button[type='submit']").click()
        expect(mobile.locator("#answer-status")).to_have_text(
            "Request not completed safely"
        )
        expect(mobile.locator("#copilot-answer p")).to_contain_text(
            "direct equipment control"
        )
        mobile.locator("#copilot-question").focus()
        expect(mobile.locator("#copilot-question")).to_be_focused()
        assert mobile.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
        mobile.screenshot(path=screenshot_dir / "workbench-mobile.png", full_page=True)
        browser.close()

    expected_simulated_failures = [
        message
        for message in console_errors
        if "server responded with a status of 500" in message
    ]
    unexpected_console_errors = [
        message
        for message in console_errors
        if message not in expected_simulated_failures
    ]
    assert len(expected_simulated_failures) == 2
    assert unexpected_console_errors == []


@pytest.mark.skipif(
    not os.getenv("PROJECT_COPILOT_BROWSER_URL")
    or os.getenv("PROJECT_COPILOT_DIRECTION_BROWSER") != "1",
    reason="real-model direction browser acceptance is not enabled",
)
def test_direction_chat_model_backed_engineer_journey() -> None:
    base_url = os.environ["PROJECT_COPILOT_BROWSER_URL"].rstrip("/")
    screenshot_dir = Path(os.getenv("PROJECT_COPILOT_SCREENSHOT_DIR", "artifacts"))
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.goto(base_url, wait_until="networkidle")

        expect(page).to_have_title("项目知识与数据助手")
        expect(page.get_by_test_id("direction-chat")).to_be_visible()
        expect(page.get_by_text("真实模型 · 只读分析")).to_be_visible()
        expect(page.locator("[data-testid='direction-chat']")).to_have_count(1)

        page.locator("#direction-question").fill(
            "HP-02为什么修改送风设定，修改后的效果怎么样？"
        )
        page.locator("#direction-form button").click()
        answer = page.locator("#messages .assistant-message").last
        expect(answer).to_be_visible(timeout=120_000)
        expect(answer.locator(".answer-heading").first).to_be_visible()
        expect(answer.locator(".answer-table").first).to_be_visible()
        expect(answer.locator("svg[role='img']").first).to_be_visible()
        assert answer.locator(".citation-card").count() >= 2
        expect(answer).to_contain_text("controls-review.md")
        expect(answer).to_contain_text("telemetry.csv")
        expect(answer).to_contain_text("已依据项目证据回答")
        expect(answer).to_contain_text("已核对项目资料")
        expect(answer).to_contain_text("已计算运行数据")
        page.screenshot(
            path=screenshot_dir / "direction-model-backed-desktop.png",
            full_page=True,
        )

        answer_count = page.locator("#messages .assistant-message").count()
        page.locator("#direction-question").fill(
            "刚才这个修改前后，电耗变化是多少？请保留 kWh 单位。"
        )
        page.locator("#direction-form button").click()
        expect(page.locator("#messages .assistant-message")).to_have_count(
            answer_count + 1,
            timeout=120_000,
        )
        follow_up = page.locator("#messages .assistant-message").last
        expect(follow_up).to_contain_text("kWh")
        expect(follow_up).to_contain_text("telemetry.csv")
        expect(follow_up).to_contain_text("已依据项目证据回答")

        page.set_viewport_size({"width": 320, "height": 700})
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
        expect(page.locator("#direction-question")).to_be_visible()
        expect(page.locator("#direction-form button")).to_be_visible()
        page.screenshot(
            path=screenshot_dir / "direction-model-backed-mobile.png",
            full_page=True,
        )
        browser.close()

    assert console_errors == []
