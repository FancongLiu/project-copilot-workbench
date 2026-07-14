import os
from pathlib import Path

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
        expect(page.get_by_test_id("knowledge-panel")).to_be_visible()
        assert page.locator("img").evaluate_all(
            "images => images.every(image => image.complete && image.naturalWidth > 0)"
        )

        page.locator("#knowledge-form button[type='submit']").click()
        expect(page.locator("#knowledge-answer p")).to_contain_text("7 摄氏度")
        assert page.locator("#source-list .source-item").count() >= 1
        expect(page.locator("#source-list .source-item").first).to_contain_text(
            "control.md"
        )

        page.locator("button[data-view='analytics']").click()
        expect(page.locator("[data-view-panel='analytics']")).to_be_visible()
        page.locator("#analysis-form button[type='submit']").click()
        expect(page.locator("#analysis-title")).to_have_text("Peak load")
        expect(page.locator("#analysis-summary")).to_contain_text("91.0%")
        expect(page.locator("#analysis-chart .chart-bar")).to_have_count(1)
        bar_box = page.locator("#analysis-chart .chart-bar").bounding_box()
        assert bar_box and bar_box["width"] > 0 and bar_box["height"] >= 8

        page.screenshot(path=screenshot_dir / "workbench-desktop.png", full_page=True)

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.goto(base_url, wait_until="networkidle")
        assert mobile.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
        expect(mobile.locator("button[data-view='knowledge']")).to_be_visible()
        mobile.screenshot(path=screenshot_dir / "workbench-mobile.png", full_page=True)
        browser.close()

    assert console_errors == []
