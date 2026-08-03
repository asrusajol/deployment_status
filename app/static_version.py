"""Cache-busting version stamp for static assets.

Computed once at process start from style.css's own content hash, and appended as
`?v=...` to the stylesheet link in base.html — a fresh deploy then always invalidates
browsers' cached copy of the CSS instead of them silently keeping an old stylesheet
after an HTML/CSS mismatch. This bit us once for real: a new button's markup shipped
fine, but the CSS aligning it stayed cached in the browser, so it rendered as
unstyled/stacked blocks until a hard refresh.

A content hash rather than the file's mtime deliberately — mtime isn't reliably bumped
by every deployment mechanism (e.g. `git pull` can preserve original commit timestamps),
but the hash always changes exactly when, and only when, the CSS content actually does.
"""

import hashlib
from pathlib import Path

_STYLE_CSS_PATH = Path(__file__).parent / "static" / "style.css"


def _compute_static_version() -> str:
    try:
        return hashlib.sha1(_STYLE_CSS_PATH.read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


STATIC_VERSION = _compute_static_version()
