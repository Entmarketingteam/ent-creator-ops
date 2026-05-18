"""
Expo West 2026 Exhibitor Scraper
Uses Swapcard GraphQL API directly — no auth required for public fields.

Outputs: expo_west_2026_exhibitors.csv + expo_west_2026_exhibitors.json
"""

import urllib.request
import json
import csv
import time
import os

# Config
API_URL = "https://api.swapcard.com/graphql"
EVENT_ID = "RXZlbnRfMzAxMDc4Nw=="  # natural-products-expo-west-2026
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
BATCH_SIZE = 100  # Max safe batch size

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Origin": "https://attend.expowest.com",
    "Referer": "https://attend.expowest.com/",
}

EXHIBITOR_QUERY = """
query GetExhibitors($eventId: ID!, $first: Int, $after: String) {
  exhibitors: Core_exhibitors(
    _eventId: $eventId
    first: $first
    after: $after
  ) {
    nodes {
      _id
      name
      description
      websiteUrl
      logoUrl
      categories
      type
      withEvent(eventId: $eventId) {
        booths {
          id
          name
        }
      }
    }
    pageInfo {
      hasNextPage
      nextCursor
    }
  }
}
"""

MEMBERS_QUERY = """
query GetMembers($exhibitorId: ID!, $eventId: ID!) {
  members: Core_exhibitorMembers(
    exhibitorId: $exhibitorId
    eventId: $eventId
    cursor: { first: 50 }
  ) {
    nodes {
      id
      userInfo {
        id
        presenceStatus
      }
    }
    pageInfo { hasNextPage }
  }
}
"""


def gql(query, variables):
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(API_URL, data=payload, headers=HEADERS)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"  HTTP {e.code}: {body[:200]}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None


def scrape_all_exhibitors():
    all_exhibitors = []
    cursor = None
    page = 1

    print("Fetching Expo West 2026 exhibitors...")
    while True:
        print(f"  Page {page} (total so far: {len(all_exhibitors)})...", end=" ", flush=True)
        result = gql(EXHIBITOR_QUERY, {
            "eventId": EVENT_ID,
            "first": BATCH_SIZE,
            "after": cursor,
        })

        if not result or "data" not in result:
            print(f"ERROR: {result}")
            break

        data = result["data"]["exhibitors"]
        nodes = data["nodes"]
        page_info = data["pageInfo"]

        for node in nodes:
            booths = node.get("withEvent", {}).get("booths", []) or []
            booth_numbers = ", ".join(b["name"] for b in booths if b.get("name"))

            all_exhibitors.append({
                "id": node["_id"],
                "name": node.get("name", ""),
                "type": node.get("type", ""),
                "booth_number": booth_numbers,
                "website": node.get("websiteUrl", "") or "",
                "description": (node.get("description", "") or "").replace("\n", " ").strip(),
                "logo_url": node.get("logoUrl", "") or "",
                "categories": ", ".join(node.get("categories", []) or []),
            })

        print(f"got {len(nodes)} records")

        if not page_info["hasNextPage"] or not page_info.get("nextCursor"):
            break

        cursor = page_info["nextCursor"]
        page += 1
        time.sleep(0.3)  # Be polite

    print(f"\nTotal exhibitors scraped: {len(all_exhibitors)}")
    return all_exhibitors


def save_results(exhibitors):
    # Save JSON
    json_path = os.path.join(OUTPUT_DIR, "expo_west_2026_exhibitors.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(exhibitors, f, indent=2, ensure_ascii=False)
    print(f"Saved JSON: {json_path}")

    # Save CSV
    csv_path = os.path.join(OUTPUT_DIR, "expo_west_2026_exhibitors.csv")
    if exhibitors:
        fieldnames = list(exhibitors[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(exhibitors)
    print(f"Saved CSV: {csv_path}")

    # Print summary stats
    types = {}
    for e in exhibitors:
        t = e["type"] or "Standard"
        types[t] = types.get(t, 0) + 1
    print("\nBy tier:")
    for t, c in sorted(types.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}")

    with_website = sum(1 for e in exhibitors if e["website"])
    print(f"\nWith website: {with_website}/{len(exhibitors)}")
    print(f"With booth: {sum(1 for e in exhibitors if e['booth_number'])}/{len(exhibitors)}")
    print(f"With logo: {sum(1 for e in exhibitors if e['logo_url'])}/{len(exhibitors)}")


if __name__ == "__main__":
    exhibitors = scrape_all_exhibitors()
    save_results(exhibitors)
    print("\nDone!")
