"""Static guards for the browser UI (the dynamic XSS check runs in tools/screenshots.py-style manual QA)."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_no_inline_event_handlers_or_scripts():
    for name in ("app.js", "index.html"):
        text = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"\son[a-z]+\s*=\s*['\"]", text), name
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline <script> blocks are blocked by the CSP"


def test_vendored_leaflet_has_license():
    assert (STATIC / "vendor" / "LICENSE-leaflet.txt").read_text(encoding="utf-8").startswith("BSD 2-Clause")


def test_escape_helper_covers_quotes():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const esc = .*", js)
    assert m and all(c in m.group(0) for c in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"))
