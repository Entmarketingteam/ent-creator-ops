#!/usr/bin/env python3
"""
Apollo Contact Extractor — Replicatable, self-healing, production-grade.
Queries Apollo API for contacts by job title + industry, validates emails, logs everything.

Usage:
  doppler run -- python apollo-contact-extractor.py \
    --job-titles "Influencer Marketing Manager" "Brand Partnership Director" \
    --industries "Supplements" "Beauty" \
    --output contacts.csv \
    --dry-run

Config via:
  - CLI args (override)
  - .env / Doppler secrets (APOLLO_API_KEY, MILLION_VERIFIER_API_KEY)
  - Checkpoint resume (contacts_checkpoint.json)
"""

import json
import time
import csv
import logging
import sys
import argparse
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import urllib.request
import urllib.error

# ============================================================================
# LOGGING SETUP
# ============================================================================

def setup_logging(output_dir: Path) -> logging.Logger:
    log_file = output_dir / f"apollo_extraction_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ]
    )
    logger = logging.getLogger(__name__)
    logger.info(f"Log file: {log_file}")
    return logger

# ============================================================================
# RETRY + BACKOFF LOGIC
# ============================================================================

class RetryConfig:
    def __init__(self, max_attempts: int = 5, base_delay: float = 1.0, max_delay: float = 60.0):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay

    def backoff(self, attempt: int) -> float:
        """Exponential backoff with jitter."""
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        # Add jitter (10% variance)
        jitter = delay * 0.1 * (0.5 if attempt % 2 else -0.5)
        return delay + jitter

def api_call_with_retry(
    url: str,
    headers: Dict[str, str],
    body: Optional[str] = None,
    retry_config: Optional[RetryConfig] = None,
    logger: Optional[logging.Logger] = None,
) -> Optional[Dict[str, Any]]:
    """
    Make API call with exponential backoff, rate limit handling, timeout recovery.
    Returns None on fatal failure (logs + continues).
    """
    retry_config = retry_config or RetryConfig()

    for attempt in range(retry_config.max_attempts):
        try:
            req = urllib.request.Request(
                url,
                data=body.encode() if body else None,
                headers=headers,
                method="POST" if body else "GET"
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode())
                if logger:
                    logger.debug(f"API call OK (attempt {attempt + 1})")
                return data

        except urllib.error.HTTPError as e:
            if e.code == 429:  # Rate limit
                delay = retry_config.backoff(attempt)
                if logger:
                    logger.warning(f"Rate limited (429). Backoff {delay:.1f}s (attempt {attempt + 1}/{retry_config.max_attempts})")
                time.sleep(delay)
            elif e.code in (500, 502, 503, 504):  # Server errors
                delay = retry_config.backoff(attempt)
                if logger:
                    logger.warning(f"Server error {e.code}. Backoff {delay:.1f}s (attempt {attempt + 1}/{retry_config.max_attempts})")
                time.sleep(delay)
            elif e.code == 401 or e.code == 403:  # Auth fail — fatal
                if logger:
                    logger.error(f"Auth failure ({e.code}). Check APOLLO_API_KEY in Doppler.")
                return None
            else:
                if logger:
                    logger.error(f"HTTP {e.code}: {e.read().decode()[:200]}")
                return None

        except (urllib.error.URLError, TimeoutError) as e:
            delay = retry_config.backoff(attempt)
            if logger:
                logger.warning(f"Timeout/connection error. Backoff {delay:.1f}s (attempt {attempt + 1}/{retry_config.max_attempts})")
            time.sleep(delay)

        except json.JSONDecodeError:
            if logger:
                logger.error("Invalid JSON response (body issue or API change?)")
            return None

        except Exception as e:
            if logger:
                logger.error(f"Unexpected error: {e}")
            return None

    if logger:
        logger.error(f"Exhausted retries ({retry_config.max_attempts}) for {url}")
    return None

# ============================================================================
# APOLLO API CLIENT
# ============================================================================

class ApolloClient:
    def __init__(self, api_key: str, logger: logging.Logger, dry_run: bool = False):
        self.api_key = api_key
        self.logger = logger
        self.dry_run = dry_run
        self.base_url = "https://api.apollo.io/api/v1"
        self.retry_config = RetryConfig(max_attempts=5, base_delay=2.0)

    def search_contacts(
        self,
        job_titles: List[str],
        industries: List[str],
        company_size: Optional[List[str]] = None,
        limit: int = 10000,
        page: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """
        Search Apollo for contacts by job title + industry.
        Returns paginated results + pagination metadata.
        """
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would search: titles={job_titles}, industries={industries}, page={page}")
            return {"contacts": [], "pagination": {"page": page, "per_page": 100, "total_entries": 0}}

        url = f"{self.base_url}/contacts/search"

        # Apollo filters: each job title is OR'd, industries are AND'd
        filters = []
        for title in job_titles:
            filters.append({
                "field_name": "current_title",
                "operator": "contains",
                "value": title
            })

        for industry in industries:
            filters.append({
                "field_name": "industry",
                "operator": "contains",
                "value": industry
            })

        if company_size:
            filters.append({
                "field_name": "company_size",
                "operator": "in",
                "value": company_size
            })

        body = json.dumps({
            "filters": filters,
            "pagination": {
                "page": page,
                "per_page": 100  # Max per page
            },
            "sort_by": "relevance"
        })

        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": self.api_key,
        }

        self.logger.info(f"Searching page {page}: titles={job_titles[:2]}..., industries={industries[:2]}...")
        return api_call_with_retry(url, headers, body, self.retry_config, self.logger)

# ============================================================================
# EMAIL VALIDATION (Million Verifier)
# ============================================================================

class MillionVerifierClient:
    def __init__(self, api_key: str, logger: logging.Logger, dry_run: bool = False):
        self.api_key = api_key
        self.logger = logger
        self.dry_run = dry_run
        self.base_url = "https://api.millionverifier.com/api/v4"
        self.retry_config = RetryConfig(max_attempts=3, base_delay=1.0)

    def verify_email(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Verify single email. Returns {email, result, is_valid}.
        result in: "valid" | "invalid" | "disposable" | "catch_all" | "unknown"
        """
        if self.dry_run:
            self.logger.debug(f"[DRY RUN] Would verify: {email}")
            return {"email": email, "result": "valid", "is_valid": True}

        url = f"{self.base_url}/verification/single"
        body = json.dumps({"email": email})
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        result = api_call_with_retry(url, headers, body, self.retry_config, self.logger)
        if result:
            is_valid = result.get("data", {}).get("result") == "valid"
            return {
                "email": email,
                "result": result.get("data", {}).get("result", "unknown"),
                "is_valid": is_valid
            }
        return None

# ============================================================================
# CHECKPOINT MANAGEMENT
# ============================================================================

class Checkpoint:
    def __init__(self, output_dir: Path):
        self.path = output_dir / "contacts_checkpoint.json"

    def load(self) -> Dict[str, Any]:
        if self.path.exists():
            with open(self.path) as f:
                return json.load(f)
        return {"page": 1, "contacts": [], "failed_emails": []}

    def save(self, state: Dict[str, Any]):
        with open(self.path, "w") as f:
            json.dump(state, f, indent=2)

    def mark_complete(self):
        if self.path.exists():
            self.path.unlink()

# ============================================================================
# MAIN PIPELINE
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Apollo contact extractor with self-healing retry logic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  doppler run -- python apollo-contact-extractor.py \\
    --job-titles "Influencer Marketing Manager" "Brand Director" \\
    --industries "Supplements" "Beauty" \\
    --output contacts.csv

  doppler run -- python apollo-contact-extractor.py \\
    --job-titles "Social Media Manager" \\
    --industries "DTC" \\
    --company-size "1-10" "11-50" \\
    --dry-run
        """
    )

    parser.add_argument("--job-titles", nargs="+", required=True, help="Job titles to search")
    parser.add_argument("--industries", nargs="+", required=True, help="Industries to target")
    parser.add_argument("--company-size", nargs="*", help="Company sizes (e.g., '1-10', '11-50')")
    parser.add_argument("--output", default="contacts.csv", help="Output CSV file")
    parser.add_argument("--output-dir", default=".", help="Output directory (for logs + checkpoint)")
    parser.add_argument("--skip-validation", action="store_true", help="Skip email validation")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (no API calls)")
    parser.add_argument("--limit", type=int, default=10000, help="Max contacts to extract")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = setup_logging(output_dir)
    logger.info(f"Starting Apollo extraction: titles={args.job_titles}, industries={args.industries}")

    # Load API keys from Doppler
    try:
        apollo_key = subprocess.check_output(
            ["doppler", "secrets", "get", "APOLLO_API_KEY",
             "--project", "ent-agency-automation", "--config", "dev", "--plain"],
            text=True
        ).strip()
    except subprocess.CalledProcessError:
        apollo_key = os.getenv("APOLLO_API_KEY", "")

    if not apollo_key:
        logger.error("APOLLO_API_KEY not found in Doppler or environment.")
        sys.exit(1)

    # Million Verifier is optional
    try:
        million_verifier_key = subprocess.check_output(
            ["doppler", "secrets", "get", "MILLION_VERIFIER_API_KEY",
             "--project", "ent-agency-automation", "--config", "dev", "--plain"],
            text=True
        ).strip()
    except subprocess.CalledProcessError:
        million_verifier_key = os.getenv("MILLION_VERIFIER_API_KEY", "")

    if not million_verifier_key and not args.skip_validation:
        logger.warning("MILLION_VERIFIER_API_KEY not set. Email validation disabled.")
        args.skip_validation = True

    # Initialize clients
    apollo = ApolloClient(apollo_key, logger, dry_run=args.dry_run)
    verifier = MillionVerifierClient(million_verifier_key or "", logger, dry_run=args.dry_run) if not args.skip_validation else None
    checkpoint = Checkpoint(output_dir)

    # Resume or start fresh
    state = checkpoint.load() if args.resume else {"page": 1, "contacts": [], "failed_emails": []}
    seen_emails = set(c["email"] for c in state["contacts"] if c.get("email"))

    logger.info(f"Starting from page {state['page']}, {len(state['contacts'])} contacts already extracted")

    # Pagination loop
    while len(state["contacts"]) < args.limit:
        result = apollo.search_contacts(
            job_titles=args.job_titles,
            industries=args.industries,
            company_size=args.company_size,
            limit=args.limit,
            page=state["page"]
        )

        if not result or not result.get("contacts"):
            logger.info("No more contacts found. Finished.")
            break

        contacts = result.get("contacts", [])
        pagination = result.get("pagination", {})

        logger.info(f"Page {state['page']}: {len(contacts)} contacts, {pagination.get('total_entries', '?')} total available")

        # Process each contact
        for contact in contacts:
            if len(state["contacts"]) >= args.limit:
                break

            email = (contact.get("email") or "").strip()

            # Generate email if missing: first.last@domain or first@domain
            if not email:
                first = (contact.get("first_name", "").lower() or "").replace(" ", "")
                last = (contact.get("last_name", "").lower() or "").replace(" ", "")
                company = contact.get("company", "").lower().replace(" ", "")

                if first and (last or company):
                    if last:
                        email = f"{first}.{last}@{company or 'example'}.com" if company else ""
                    else:
                        email = f"{first}@{company}.com" if company else ""

                if email:
                    logger.debug(f"Generated email: {email}")

            if not email or email in seen_emails:
                logger.debug(f"Skipped (no email or duplicate): {contact.get('first_name')} {contact.get('last_name')}")
                continue

            # Validate email (optional)
            is_valid = True
            if verifier:
                verification = verifier.verify_email(email)
                if verification:
                    is_valid = verification.get("is_valid", False)
                    validation_result = verification.get("result", "unknown")
                    logger.debug(f"Email {email}: {validation_result}")
                else:
                    logger.warning(f"Failed to validate {email}, assuming valid and continuing")
                    is_valid = True  # Fail open

            if is_valid:
                record = {
                    "first_name": contact.get("first_name", ""),
                    "last_name": contact.get("last_name", ""),
                    "email": email,
                    "title": contact.get("title", ""),
                    "company": contact.get("company", ""),
                    "industry": contact.get("industry", ""),
                    "phone": contact.get("phone_number", ""),
                    "linkedin_url": contact.get("linkedin_url", ""),
                    "source": "apollo"
                }
                state["contacts"].append(record)
                seen_emails.add(email)
                logger.debug(f"Added: {email} ({contact.get('company')})")
            else:
                state["failed_emails"].append(email)

        # Checkpoint after page
        checkpoint.save(state)
        logger.info(f"Checkpointed: {len(state['contacts'])} contacts, page {state['page']}")

        # Next page
        total = pagination.get("total_entries", 0)
        if len(state["contacts"]) >= total or len(state["contacts"]) >= args.limit:
            logger.info("Reached target or end of results.")
            break

        state["page"] += 1
        time.sleep(1)  # Rate limit politeness

    # Write CSV
    output_file = output_dir / args.output
    fieldnames = ["first_name", "last_name", "email", "title", "company", "industry", "phone", "linkedin_url", "source"]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(state["contacts"])

    logger.info(f"Extracted {len(state['contacts'])} valid contacts → {output_file}")
    logger.info(f"Failed validations: {len(state['failed_emails'])}")

    # Cleanup checkpoint on success
    checkpoint.mark_complete()
    logger.info("Complete. Checkpoint removed.")

if __name__ == "__main__":
    main()
