#!/usr/bin/env python3
"""Readiness probe used by StudyAgent: key present and the AIHUB image models are listed."""
import json, os, urllib.request
key = os.environ.get("STUDY_IMAGE_API_KEY", "").strip()
base = os.environ.get("STUDY_IMAGE_BASE_URL", "https://aihub.gz4399.com/v1").rstrip("/")
ready = False
if key:
    try:
        request = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(request, timeout=8) as response:
            ready = any(m.get("id") == "gpt-image-2" for m in json.load(response).get("data", []))
    except Exception:
        ready = False
print(json.dumps({"ready": ready, "next_action": "run_skill" if ready else "configure_key"}))
