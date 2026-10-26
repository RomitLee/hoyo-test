"""Fixed cloud endpoint shared by the master and slave desktop apps."""

from __future__ import annotations

import os
from urllib.parse import urlparse

CLOUD_RELAY_URL = "https://43.132.148.55"

# Explicit local preview is restricted to loopback; normal packages retain the
# fixed production endpoint. No user credentials are copied into the preview.
_preview_url = os.environ.get("HOYO_PREVIEW_RELAY_URL", "").rstrip("/")
_preview_parts = urlparse(_preview_url)
LOCAL_PREVIEW = bool(_preview_parts.scheme == "http" and _preview_parts.hostname in {"127.0.0.1", "localhost"}
                     and not _preview_parts.username and not _preview_parts.password
                     and not _preview_parts.query and not _preview_parts.fragment and not _preview_parts.path)
if LOCAL_PREVIEW:
    CLOUD_RELAY_URL = _preview_url

