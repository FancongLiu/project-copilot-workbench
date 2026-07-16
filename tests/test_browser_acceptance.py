import os
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.mark.skipif(
    not os.getenv("PROJECT_COPILOT_BROWSER_URL"),
    reason="browser acceptance server is not running",
)
def test_desktop_and_mobile_workbench_acceptance() -> None:
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
