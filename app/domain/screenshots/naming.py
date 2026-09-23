"""Screenshot naming (docs/DATA_MODEL.md § Screenshot file naming).

Deterministic, collision-resistant, never timestamp-only:

    ``{session_id}_{timestamp}_{uuid}.jpg``
    e.g. ``sess_20260923_001_20260923T103000_8f2a.jpg``
"""

from __future__ import annotations

from datetime import UTC, datetime


def build_screenshot_filename(
    session_id: int,
    captured_at: datetime,
    unique_id: str,
) -> str:
    """Render a collision-resistant screenshot filename.

    ``captured_at`` must be timezone-aware (UTC); formatted as
    ``YYYYmmddTHHMMSS`` so files sort chronologically within a session.
    """
    if captured_at.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware (UTC)")
    stamp = captured_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S")
    return f"sess_{session_id:06d}_{stamp}_{unique_id}.jpg"
