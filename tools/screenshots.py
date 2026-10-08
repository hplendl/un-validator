"""Drive the UI headlessly and capture the README screenshots (1440x900). Needs Playwright.

python tools/screenshots.py --dataset NapervilleElectric
python tools/screenshots.py --url http://127.0.0.1:9000/ --out docs/img
python tools/screenshots.py --folder other          # multi-dataset dashboard (6_...)
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def _done(pg) -> None:
    pg.wait_for_selector("#hdrStatus.done, #hdrStatus.failed, #hdrStatus.cancelled", timeout=600_000)


def dataset_shots(pg, out: Path, name: str, prefix: str, pace: str) -> None:
    pg.wait_for_selector("#dsSelect option", state="attached")
    val = pg.eval_on_selector_all("#dsSelect option", "(os, n) => (os.find(o => o.text.includes(n)) || {}).value", name)
    if not val:
        raise SystemExit(f"No dataset matching {name!r} in the picker")
    pg.select_option("#dsSelect", val)
    pg.select_option("#paceSelect", pace)
    pg.click("#runBtn")
    if not prefix:
        pg.wait_for_selector("#stg-quality.active", timeout=180_000)
        time.sleep(2.5)
        pg.screenshot(path=str(out / "1_pipeline_running.png"))
    _done(pg)
    time.sleep(0.8)
    pg.screenshot(path=str(out / f"{prefix}2_results_dashboard.png"))
    pg.click("button[data-tab=findings]")
    time.sleep(0.5)
    pg.screenshot(path=str(out / f"{prefix}3_findings_table.png"))
    pg.click("button[data-tab=map]")
    pg.wait_for_timeout(1500)
    try:
        pg.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:  # noqa: BLE001 - tiles may keep loading
        pass
    time.sleep(1.5)
    pg.screenshot(path=str(out / f"{prefix}4_map.png"))
    pg.click("button[data-tab=details]")
    time.sleep(0.4)
    pg.screenshot(path=str(out / f"{prefix}5_layers_metadata.png"))


def folder_shot(pg, out: Path, folder: str) -> None:
    pg.wait_for_selector("#folderSelect option", state="attached")
    pg.click("#modeSeg button[data-mode=folder]")
    val = pg.eval_on_selector_all(
        "#folderSelect option", "(os, f) => (os.find(o => o.dataset.rel === f) || {}).value", folder
    )
    if not val:
        raise SystemExit(f"No folder {folder!r} in the picker")
    pg.select_option("#folderSelect", val)
    pg.select_option("#paceSelect", "0")
    pg.click("#runBtn")
    _done(pg)
    time.sleep(1)
    pg.screenshot(path=str(out / "6_folder_run_dashboard.png"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8765/")
    ap.add_argument("--out", default=str(ROOT / "screenshots"))
    ap.add_argument("--dataset", default="NapervilleElectric", help="part of the dataset name to pick")
    ap.add_argument("--folder", help="take the folder-run screenshot for this folder instead")
    ap.add_argument("--prefix", default="", help="file-name prefix (skips the 'running' shot)")
    ap.add_argument("--pace", default="120", help="demo pace in ms (a value offered in the UI)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs: list[str] = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(a.url)
        if a.folder:
            folder_shot(pg, out, a.folder)
        else:
            dataset_shots(pg, out, a.dataset, a.prefix, a.pace)
        print("console errors:", errs)
        b.close()


if __name__ == "__main__":
    main()
