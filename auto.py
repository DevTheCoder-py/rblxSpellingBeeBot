#!/usr/bin/env python3
import json
import sys
import os

WORDS_FILE = "wordlist.txt"
JSON_FILE  = "words.json"

if not os.path.exists(WORDS_FILE):
    print(f"Error: {WORDS_FILE} not found.")
    sys.exit(1)

if not os.path.exists(JSON_FILE):
    print(f"Error: {JSON_FILE} not found.")
    sys.exit(1)

with open(WORDS_FILE) as f:
    new_words = [line.strip().lower() for line in f if line.strip()]

with open(JSON_FILE) as f:
    data = json.load(f)

existing = set(data.get("targets", []))
added = [w for w in new_words if w not in existing]

data.setdefault("targets", []).extend(added)
data["targets"] = sorted(set(data["targets"]))

with open(JSON_FILE, "w") as f:
    json.dump(data, f, indent=4)

print(f"Added {len(added)} new word(s). Total targets: {len(data['targets'])}.")
if added:
    print("New words:", ", ".join(added))
