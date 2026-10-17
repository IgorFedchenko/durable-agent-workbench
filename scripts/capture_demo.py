"""Capture actual local UI frames after the real browser/server/Docker journey.

Use your optional local command prefix. Playwright Chromium must already be installed.
This script never fabricates UI or changes engine responses.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    frames: list[Image.Image] = []
    with tempfile.TemporaryDirectory(prefix="daw-capture-") as directory:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_workbench.cli",
                "--data",
                directory,
                "serve",
                "--port",
                "8766",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                try:
                    with urlopen("http://127.0.0.1:8766/", timeout=1):
                        break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError("capture server exited") from None
                    time.sleep(0.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(
                    viewport={"width": 1420, "height": 1000}, device_scale_factor=1
                )
                page.goto("http://127.0.0.1:8766/")

                def capture() -> None:
                    frames.append(Image.open(io.BytesIO(page.screenshot())).convert("RGB"))

                capture()
                page.locator("#start").click()
                page.get_by_text("INTERRUPTED", exact=True).wait_for(timeout=60000)
                failed_attempt = page.locator("#timeline details.failure").first
                failed_attempt.locator("summary").click()
                failed_attempt.scroll_into_view_if_needed()
                capture()
                page.locator("#resume").click()
                page.get_by_text("COMPLETED", exact=True).wait_for(timeout=60000)
                reviewer = (
                    page.locator("#timeline details").filter(has_text="Reviewer child linked").first
                )
                reviewer.locator("summary").click()
                reviewer.scroll_into_view_if_needed()
                capture()
                page.locator("#workspace").scroll_into_view_if_needed()
                capture()
                page.locator("#new-run").click()
                page.locator("#scenario").select_option("regions")
                page.locator("#pause").uncheck()
                page.locator("#start").click()
                page.get_by_text("COMPLETED", exact=True).wait_for(timeout=60000)
                page.locator("#workspace").scroll_into_view_if_needed()
                capture()
                # Cover framing is selected only after the real report/chart exists.
                page.locator("#report .chart-row").first.wait_for()
                run_id = page.locator("#report").get_attribute("data-run-id")
                if not run_id:
                    raise RuntimeError("completed report has no run identity")
                # A 4:3 desktop viewport keeps both columns; Chromium renders the
                # final pixels directly at 1000x750. No image resizing is used.
                cover_context = browser.new_context(
                    viewport={"width": 1420, "height": 1065},
                    device_scale_factor=1000 / 1420,
                )
                cover_page = cover_context.new_page()
                cover_page.goto("http://127.0.0.1:8766/")
                cover_page.locator("#run-list button").filter(has_text=run_id[:8]).click()
                cover_page.locator("#report .chart-row").first.wait_for()
                cover_page.get_by_text("COMPLETED", exact=True).wait_for()
                # Expand the persisted reviewer linkage through its actual UI
                # summary; the adjacent verified report/chart stays unchanged.
                cover_reviewer = (
                    cover_page.locator("#timeline details")
                    .filter(has_text="Reviewer child linked")
                    .first
                )
                cover_reviewer.locator("summary").click()
                cover_page.evaluate("document.fonts.ready")
                cover_page.locator("#workspace").evaluate(
                    "node => node.scrollIntoView({block: 'start'})"
                )
                cover_output = ROOT / "docs/portfolio-cover.png"
                cover_page.screenshot(path=str(cover_output))
                with Image.open(cover_output) as cover_image:
                    print(f"Actual browser cover: {cover_output} {cover_image.size}")
                cover_context.close()
                browser.close()
            output = ROOT / "docs/demo.gif"
            frames[0].save(
                output,
                save_all=True,
                append_images=frames[1:],
                duration=[2200, 2500, 3000, 3000, 3500],
                loop=0,
                optimize=True,
            )
            print(f"Actual browser capture: {output}")
        finally:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    main()
