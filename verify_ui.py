"""Exercise the running local UI with Playwright and Windows Edge."""
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent
sample = ROOT/"sample/ngwerere_20191103.mp4"
exports = ROOT/"local/browser-exports"
exports.mkdir(parents=True,exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(accept_downloads=True)
    page.set_default_timeout(180000)
    page.goto(os.environ.get("RIVER_URL","http://localhost:8501"))
    def toggle(label):
        page.get_by_text(label, exact=True).click()
    def run(tag):
        page.get_by_role("button", name="Analyze recording").click()
        page.get_by_text("Measurement results",exact=True).wait_for(timeout=900000)
        for file in ("summary.json","velocity.png"):
            with page.expect_download() as dl:
                page.get_by_text("Download "+file,exact=True).click()
            dl.value.save_as(exports/(tag+"-"+file))
        data=json.loads((exports/(tag+"-summary.json")).read_text())
        print(tag,data["frames"],data["run"],data["discharge"],flush=True)
        return data
    confirm = "I confirm this camera view matches the selected calibration."
    toggle(confirm)
    toggle("I verified the displayed sections cross the river at the surveyed locations.")
    first = run("sample")
    assert len(first["discharge"])==2, first
    toggle("Upload my MP4")
    video = page.locator('input[type=file]').first
    video.set_input_files(dict(name="renamed-river.mp4",mimeType="video/mp4",buffer=sample.read_bytes()))
    page.get_by_text("Choose a setup",exact=True).click()
    page.get_by_role("option",name="Ngwerere",exact=True).click()
    toggle("Use calibration water level for this recording")
    toggle(confirm)
    second = run("upload")
    assert second["source"]=="renamed-river.mp4" and not second["request_discharge"]
    assert second["surface_speed_median_m_s"]==first["surface_speed_median_m_s"]
    slider = page.get_by_role("slider").last
    slider.focus()
    slider.press("ArrowLeft")
    page.get_by_text("Inputs changed.",exact=False).wait_for()
    toggle("Estimate discharge")
    # Remove both surveys to exercise the missing-input branch.
    page.locator('[data-testid="stMultiSelect"]').get_by_role("button", name="Clear all").click()
    third=run("interval")
    assert third["frames"]!=second["frames"] and third["run"]!=second["run"]
    assert not third["discharge"] and third["quality_flags"]
    video.set_input_files(dict(name="broken.mp4",mimeType="video/mp4",buffer=b"invalid"))
    page.get_by_text("Cannot decode the MP4",exact=False).wait_for()
    expect(page.get_by_text("Measurement results",exact=True)).to_have_count(0)
    video.set_input_files(dict(name="renamed-river.mp4",mimeType="video/mp4",buffer=sample.read_bytes()))
    page.get_by_role("combobox").first.click()
    page.get_by_role("option",name="Import a setup").click()
    import time
    site="Resolution test "+str(time.time_ns())
    page.get_by_label("New site name").fill(site)
    cfg=json.loads((ROOT/"sample/ngwerere.json").read_text())
    cfg["width"]=640
    page.locator('input[type=file]').nth(1).set_input_files(dict(name="camera.json",mimeType="application/json",buffer=json.dumps(cfg).encode()))
    page.get_by_role("button",name="Save site").click()
    page.get_by_text("Camera configuration expects 640",exact=False).wait_for()
    expect(page.get_by_text("Measurement results",exact=True)).to_have_count(0)
    browser.close()
    (ROOT/"local/sites"/site/"camera.json").replace(exports/(site+".json"))
    print("Unreadable MP4 and mismatched calibration both suppressed earlier results.", flush=True)



