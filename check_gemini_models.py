#!/usr/bin/env python3
"""Check all available Google Gemini models."""

from __future__ import annotations

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
if not GEMINI_KEY:
    print("ERROR: GEMINI_API_KEY not found in .env")
    print("Add GEMINI_API_KEY=your_key to .env")
    exit(1)

MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"

print(f"Fetching Gemini models (key: {GEMINI_KEY[:8]}...)")
print(f"{'='*70}")

models = []
page_token = None

while True:
    params: dict = {"key": GEMINI_KEY, "pageSize": 100}
    if page_token:
        params["pageToken"] = page_token

    r = httpx.get(MODELS_URL, params=params, timeout=30)
    if r.status_code != 200:
        print(f"ERROR: {r.status_code} - {r.text}")
        exit(1)

    data = r.json()
    models.extend(data.get("models", []))
    page_token = data.get("nextPageToken")
    if not page_token:
        break

print(f"Found {len(models)} models total\n")

generative = [m for m in models if "generateContent" in m.get("supportedGenerationMethods", [])]
embedding = [m for m in models if "embedContent" in m.get("supportedGenerationMethods", [])]
other = [m for m in models if m not in generative and m not in embedding]

print(f"GENERATIVE: {len(generative)}")
print(f"EMBEDDING: {len(embedding)}")
if other:
    print(f"OTHER: {len(other)}")

print(f"\n{'='*70}")
print("GENERATIVE MODELS")
print(f"{'='*70}")
for m in generative:
    name = m["name"].split("/")[-1]
    display = m.get("displayName", "")
    desc = m.get("description", "")[:60]
    print(f"  {name}  {display}")
    if desc:
        print(f"    {desc}...")

if embedding:
    print(f"\n{'='*70}")
    print("EMBEDDING MODELS")
    print(f"{'='*70}")
    for m in embedding:
        name = m["name"].split("/")[-1]
        display = m.get("displayName", "")
        print(f"  {name}  {display}")

if other:
    print(f"\n{'='*70}")
    print("OTHER MODELS")
    print(f"{'='*70}")
    for m in other:
        name = m["name"].split("/")[-1]
        display = m.get("displayName", "")
        methods = m.get("supportedGenerationMethods", [])
        print(f"  {name}  {display}  {methods}")
