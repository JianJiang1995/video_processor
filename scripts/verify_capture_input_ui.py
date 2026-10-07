#!/usr/bin/env python3
"""Check actual discovery and isolated missing-card/scan-error UI states."""
import argparse
import asyncio
import json
from pathlib import Path
import shutil

from playwright.async_api import async_playwright


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"page_errors": [], "connect_requests": [], "hardware_stream_tested": False}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"],
                                                   executable_path=shutil.which("google-chrome"))
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        await page.add_init_script("""localStorage.setItem('surg_ui_language', 'zh');
          sessionStorage.setItem('surgr1_capture_auto_connect_started', '1');""")
        page.on("pageerror", lambda error: result["page_errors"].append(str(error)))

        async def intercept_connect(route):
            result["connect_requests"].append(route.request.post_data_json)
            await route.fulfill(status=400, json={"detail": "UI test: capture not started"})

        await page.route("**/api/video/connect-capture", intercept_connect)
        await page.goto(args.url)
        await page.locator(".mode-selector, .stream-input-panel").first.wait_for()
        if not await page.locator(".stream-input-panel").count():
            await page.get_by_role("button", name="实时视频流", exact=True).click()
        await page.locator(".stream-input-panel").wait_for()
        card = page.locator(".device-card", has_text="DeckLink Mini Recorder 4K")
        await card.wait_for()
        await card.click()
        await page.locator("#capture-connection").select_option("sdi")
        await page.locator("#capture-mode").select_option("1080p50")
        label = await page.locator("#capture-mode option:checked").inner_text()
        assert "1920x1080" in label and "50" in label and "逐行" in label, label
        result["selected_label"] = label
        response = await page.request.get(args.url + "/api/video/capture-devices")
        actual = await response.json()
        result["actual_detected_devices"] = actual["devices"]
        actual_card = next(d for d in actual["devices"] if d["backend"] == "decklink")
        await page.screenshot(path=str(args.output / "input_actual_1280.png"))
        for width, height in ((1920, 1080), (390, 844)):
            await page.set_viewport_size({"width": width, "height": height})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            await page.screenshot(path=str(args.output / f"input_actual_{width}.png"))
        await page.set_viewport_size({"width": 1280, "height": 900})
        await page.get_by_role("button", name="连接采集卡").click()
        await page.locator(".connection-error").wait_for()
        assert result["connect_requests"][-1]["mode"] == "1080p50"
        assert result["connect_requests"][-1]["connection"] == "sdi"
        assert result["connect_requests"][-1]["auto_analyze"] is True

        offline = {**actual_card, "available": False, "hardware_ready": False,
                   "unavailable_reason": "decklinkPciMissing"}
        async def missing_card(route):
            await route.fulfill(json={"success": True,
                                     "devices": [d for d in actual["devices"] if d["backend"] != "decklink"],
                                     "unavailable_devices": [offline]})
        await page.route("**/api/video/capture-devices", missing_card)
        await page.locator(".refresh-btn").click()
        await page.locator(".capture-unavailable").wait_for()
        assert await page.locator("#capture-mode").input_value() == "1080p50"
        assert await page.get_by_role("button", name="连接采集卡").is_disabled()
        assert await page.locator("#capture-mode").is_enabled()
        result["offline_configuration_retained"] = True
        await page.unroute("**/api/video/capture-devices", missing_card)
        await page.locator(".refresh-btn").click()
        await page.wait_for_function("!document.querySelector('.capture-unavailable')")
        assert await page.locator("#capture-mode").input_value() == "1080p50"
        assert await page.get_by_role("button", name="连接采集卡").is_enabled()
        result["rescan_recovers_card"] = True
        await page.locator(".device-card", has_text="手术室采集卡模拟源").click()
        assert await page.locator("#simulator-path").is_visible()
        assert not await page.locator("#capture-mode").count()
        result["simulator_does_not_advertise_fixed_30fps"] = True
        await card.click()
        async def timeout(route):
            await route.fulfill(json={"success": False, "scan_error": "timeout", "devices": []})
        await page.route("**/api/video/capture-devices", timeout)
        await page.locator(".refresh-btn").click()
        await page.locator(".connection-error").wait_for()
        assert await page.get_by_role("button", name="连接采集卡").is_disabled()
        result["scan_failure_visible"] = True
        assert not result["page_errors"], result["page_errors"]
        await browser.close()
    (args.output / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5133")
    parser.add_argument("--output", type=Path, default=Path("recordings/capture_input_20261007"))
    asyncio.run(main(parser.parse_args()))
