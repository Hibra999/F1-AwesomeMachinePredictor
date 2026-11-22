"""Same-event FIA steward decisions reviewed for the 48 historical T−30 cuts."""

from functools import cache
import json
from pathlib import Path

from .engine import epoch


@cache
def _ledger():
    path = Path(__file__).resolve().parents[1] / "reports" / "grid-decisions.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_decisions(gp):
    """Read the fixed ledger without network access or a private cache.

    ``gp['race']`` must be the independently reconstructed scheduled start, not
    lights-out. The review applies to one instant; do not roll it forward/back.
    An empty decision list is never evidence that a driver had no penalty.
    """
    event = _ledger().get(gp["id"])
    if event is None:
        return {"listing_url": None, "reason": "outside_reviewed_events", "decisions": []}
    result = {"listing_url": event["listing_url"], "reason": event["reason"], "decisions": []}
    cutoff = gp["race"] - 1800
    if epoch(event["cutoff_utc"]) != cutoff:
        return {**result, "reason": "cutoff_not_reviewed"}
    if result["reason"] in {"ok", "partial_drivers"}:
        # Later rulings must neither create a penalty nor cancel evidence already known.
        minute = cutoff - cutoff % 60
        unresolved = [r.copy() for r in event.get("unresolved", ()) if epoch(r["available_utc"]) < minute]
        excluded = {r["driver"] for r in unresolved}
        result["reason"] = "partial_drivers" if excluded else "ok"
        if excluded:
            result.update(unresolved=unresolved, unresolved_drivers=sorted(excluded))
        result["decisions"] = [r.copy() for r in event["decisions"]
                               if epoch(r["available_utc"]) < minute and r["driver"] not in excluded]
    return result
