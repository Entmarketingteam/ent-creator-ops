"""
Enrich Expo West 2026 exhibitors with Claude Haiku classification.
Adds: entity_type, dtc_relevant, affiliate_platform, outreach_priority, tag_notes
Outputs: expo_west_2026_enriched.csv

Runs 10 parallel workers — ~6x faster than sequential.
"""
import json
import csv
import time
import urllib.request
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

INPUT_FILE = "/Users/ethanatchley/Desktop/expo_west_2026_exhibitors.json"
OUTPUT_FILE = "/Users/ethanatchley/Desktop/expo_west_2026_enriched.csv"
BATCH_SIZE = 50
WORKERS = 10

API_KEY = subprocess.check_output(
    ["doppler", "secrets", "get", "ANTHROPIC_API_KEY",
     "--project", "ent-agency-automation", "--config", "dev", "--plain"],
    text=True
).strip()

SYSTEM_PROMPT = """You are classifying trade show exhibitors for an influencer marketing agency.
For each exhibitor, return a JSON array with one object per exhibitor containing:
- "entity_type": one of: Brand | Service/Agency | 3PL/Logistics | Packaging | Tech/Platform | Retailer | Media/Association | Other
- "dtc_relevant": one of: Yes | Maybe | No  (Yes = sells consumer products via DTC/Amazon/retail that creators could promote; Maybe = unclear; No = B2B service, packaging co, logistics, etc.)
- "affiliate_platform": comma-separated list of likely platforms from: LTK | Amazon | ShopMy | Mavely | Impact | ShareASale | None | Unknown  (based on product type/description clues)
- "outreach_priority": one of: High | Medium | Low | Skip  (High = consumer brand selling products perfect for creator collabs; Skip = B2B service/logistics)
- "tag_notes": 1 short sentence max explaining the classification, or empty string

Return ONLY a valid JSON array, no other text."""

print_lock = threading.Lock()


def log(msg):
    with print_lock:
        print(msg, flush=True)


def classify_batch(batch_num, total_batches, exhibitors):
    user_content = json.dumps([
        {
            "name": e["name"],
            "type": e.get("type", ""),
            "categories": e.get("categories", ""),
            "description": (e.get("description", "") or "")[:300],
            "website": e.get("website", ""),
        }
        for e in exhibitors
    ])

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}]
    }).encode()

    for attempt in range(3):
        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "x-api-key": API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                resp = json.loads(r.read())
                text = resp["content"][0]["text"].strip()
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                results = json.loads(text.strip())
                brands = sum(1 for r in results if r.get("dtc_relevant") == "Yes")
                high = sum(1 for r in results if r.get("outreach_priority") == "High")
                log(f"  [{batch_num:02d}/{total_batches}] OK — {brands} brands, {high} high priority")
                return results
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:200]
            log(f"  [{batch_num:02d}/{total_batches}] HTTP {e.code} attempt {attempt+1}: {body}")
            if attempt < 2:
                time.sleep(2 ** attempt)
        except Exception as e:
            log(f"  [{batch_num:02d}/{total_batches}] Error attempt {attempt+1}: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None


def main():
    with open(INPUT_FILE) as f:
        exhibitors = json.load(f)

    total = len(exhibitors)
    batches = [(i, exhibitors[i:i + BATCH_SIZE]) for i in range(0, total, BATCH_SIZE)]
    total_batches = len(batches)

    print(f"Loaded {total} exhibitors → {total_batches} batches × {WORKERS} parallel workers")
    print(f"Estimated time: ~{total_batches // WORKERS + 1} rounds\n")

    # results[batch_index] = list of enriched rows (preserves order)
    results = [None] * total_batches
    failed = []

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(classify_batch, i + 1, total_batches, batch): (i, batch)
            for i, batch in batches
        }
        for future in as_completed(futures):
            i, batch = futures[future]
            classifications = future.result()
            if classifications and len(classifications) == len(batch):
                results[i] = [
                    {**exhibitor, **classification}
                    for exhibitor, classification in zip(batch, classifications)
                ]
            else:
                log(f"  [batch {i+1}] FAILED — marking for retry")
                failed.append(i)
                results[i] = [
                    {**e, "entity_type": "", "dtc_relevant": "", "affiliate_platform": "",
                     "outreach_priority": "", "tag_notes": "classification_failed"}
                    for e in batch
                ]

    # Retry failed batches sequentially
    if failed:
        print(f"\nRetrying {len(failed)} failed batches...")
        for i in failed:
            batch = batches[i][1]
            classifications = classify_batch(i + 1, total_batches, batch)
            if classifications and len(classifications) == len(batch):
                results[i] = [
                    {**exhibitor, **classification}
                    for exhibitor, classification in zip(batch, classifications)
                ]
                print(f"  Batch {i+1} retry OK")
            else:
                print(f"  Batch {i+1} retry FAILED — leaving blank")

    # Flatten ordered results
    enriched = [row for batch_result in results for row in batch_result]

    # Save CSV
    fieldnames = [
        "name", "booth_number", "type", "outreach_priority", "entity_type",
        "dtc_relevant", "affiliate_platform", "tag_notes",
        "website", "categories", "description", "logo_url", "id"
    ]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(enriched)

    # Save JSON
    json_out = OUTPUT_FILE.replace(".csv", ".json")
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"Done! {len(enriched)} rows")
    print(f"\nOutreach Priority:")
    for priority in ["High", "Medium", "Low", "Skip"]:
        count = sum(1 for e in enriched if e.get("outreach_priority") == priority)
        print(f"  {priority}: {count}")
    print(f"\nDTC/Amazon/Retail relevant: {sum(1 for e in enriched if e.get('dtc_relevant') == 'Yes')}")
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Saved: {json_out}")


if __name__ == "__main__":
    main()
