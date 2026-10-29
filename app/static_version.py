"""Cache-busting version stamp for static assets.

Computed once at process start from the content hash of style.css and grid.js, and
appended as `?v=...` to their links in base.html — a fresh deploy then always invalidates
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

_STATIC = Path(__file__).parent / "static"
# Every static file pages load as code. grid.js joined style.css here when the table
# script moved out of the templates — it needs the same cache-busting.
_VERSIONED = [_STATIC / "style.css", _STATIC / "grid.js"]


def compute_static_version(paths) -> str:
    digest = hashlib.sha1()
    for path in paths:
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"missing:" + str(path).encode())
    return digest.hexdigest()[:10]


STATIC_VERSION = compute_static_version(_VERSIONED)
