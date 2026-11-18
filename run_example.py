"""Submit one approved live-event asset to the local service."""

import json
import urllib.request


payload = json.dumps(
    {
        "player_id": "player-42",
        "live_event": "lunar-cup",
        "prompt": "A silver lunar shield reward icon on a transparent-looking dark field",
        "moderation": "approved",
        "link_ttl_seconds": 900,
    }
).encode("utf-8")
request = urllib.request.Request(
    "http://127.0.0.1:8000/assets",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request) as response:
    print(json.dumps(json.load(response), indent=2))
