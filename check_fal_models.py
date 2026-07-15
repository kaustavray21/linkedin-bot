#!/usr/bin/env python3
"""Check all available fal.ai text-to-image models."""

from __future__ import annotations

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

FAL_KEY = os.getenv("FAL_API_KEY", "")
if not FAL_KEY:
    print("ERROR: FAL_API_KEY not found in .env")
    print("Add FAL_API_KEY=your_key_id:your_key_secret to .env")
    exit(1)

CATALOG_URL = "https://api.fal.ai/v1/models"
HEADERS = {"Authorization": f"Key {FAL_KEY}"}

print(f"Fetching fal.ai text-to-image models (key: {FAL_KEY[:8]}...)")
print(f"{'='*70}")

models = []
cursor = None

while True:
    params: dict = {"limit": 100, "category": "text-to-image"}
    if cursor:
        params["cursor"] = cursor

    r = httpx.get(CATALOG_URL, headers=HEADERS, params=params, timeout=30)
    if r.status_code != 200:
        print(f"ERROR: {r.status_code} - {r.text}")
        exit(1)

    data = r.json()
    models.extend(data["models"])
    if not data.get("has_more"):
        break
    cursor = data.get("next_cursor")

active = [m for m in models if m.get("metadata", {}).get("status") == "active"]
deprecated = [m for m in models if m.get("metadata", {}).get("status") == "deprecated"]

print(f"Found {len(active)} active, {len(deprecated)} deprecated\n")

print("ACTIVE MODELS")
print(f"{'='*70}")
for m in active:
    eid = m["endpoint_id"]
    name = m.get("metadata", {}).get("display_name", "")
    tags = m.get("metadata", {}).get("tags", [])
    tag_str = f"  [{', '.join(tags)}]" if tags else ""
    print(f"  {eid}  {name}{tag_str}")

if deprecated:
    print(f"\nDEPRECATED ({len(deprecated)})")
    print(f"{'='*70}")
    for m in deprecated:
        print(f"  {m['endpoint_id']}")
