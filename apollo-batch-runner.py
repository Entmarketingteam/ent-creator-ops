#!/usr/bin/env python3
"""
Batch runner for apollo-contact-extractor.
Runs multiple personas in sequence, dedupes results, handles failures.

Usage:
  doppler run -- python apollo-batch-runner.py --personas influencer_marketing brand_partnerships
  doppler run -- python apollo-batch-runner.py --all --dry-run
  doppler run -- python apollo-batch-runner.py --persona influencer_marketing --resume
"""

import subprocess
import json
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any
import yaml
import csv

# ============================================================================
# SETUP
# ============================================================================

def setup_logging(output_dir: Path) -> logging.Logger:
    log_file = output_dir / f"apollo_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ]
    )
    return logging.getLogger(__name__)

def load_personas(personas_file: Path) -> Dict[str, Dict[str, Any]]:
    with open(personas_file) as f:
        config = yaml.safe_load(f)
    return {k: v for k, v in config.get("personas", {}).items() if not k.startswith("_")}

# ============================================================================
# BATCH RUNNER
# ============================================================================

class ApolloBackRunner:
    def __init__(self, output_dir: Path, logger: logging.Logger, dry_run: bool = False):
        self.output_dir = output_dir
        self.logger = logger
        self.dry_run = dry_run
        self.personas_file = Path(__file__).parent / "apollo-personas.yaml"

    def run_persona(self, name: str, config: Dict[str, Any], resume: bool = False) -> bool:
        """Run single persona extraction. Returns True if successful."""
        output_file = self.output_dir / config.get("output", f"contacts_{name}.csv")

        cmd = [
            "python", str(Path(__file__).parent / "apollo-contact-extractor.py"),
            "--job-titles", *config.get("job_titles", []),
            "--industries", *config.get("industries", []),
            "--output", str(output_file),
            "--output-dir", str(self.output_dir),
        ]

        if config.get("company_size"):
            cmd.extend(["--company-size", *config["company_size"]])

        if resume:
            cmd.append("--resume")

        if self.dry_run:
            cmd.append("--dry-run")

        # Wrap in doppler if not already running
        full_cmd = ["doppler", "run", "--"] + cmd

        self.logger.info(f"Running persona '{name}': {' '.join(cmd[3:6])}...")
        try:
            result = subprocess.run(
                full_cmd,
                capture_output=False,
                timeout=3600  # 1hr timeout per persona
            )
            if result.returncode == 0:
                self.logger.info(f"✓ Persona '{name}' completed successfully")
                return True
            else:
                self.logger.error(f"✗ Persona '{name}' failed with code {result.returncode}")
                return False
        except subprocess.TimeoutExpired:
            self.logger.error(f"✗ Persona '{name}' timed out (1hr)")
            return False
        except Exception as e:
            self.logger.error(f"✗ Persona '{name}' error: {e}")
            return False

    def dedupe_results(self, output_files: List[Path]) -> Path:
        """Merge multiple CSVs, dedupe by email, return merged file."""
        seen_emails = set()
        merged = []

        for file in output_files:
            if not file.exists():
                self.logger.warning(f"Missing file: {file}")
                continue

            with open(file) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    email = row.get("email", "").strip()
                    if email and email not in seen_emails:
                        merged.append(row)
                        seen_emails.add(email)
                    elif email in seen_emails:
                        self.logger.debug(f"Dedupe: {email}")

        merged_file = self.output_dir / "contacts_merged_deduped.csv"
        fieldnames = ["first_name", "last_name", "email", "title", "company", "industry", "phone", "linkedin_url", "source"]
        with open(merged_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(merged)

        self.logger.info(f"Merged {len(merged)} unique contacts → {merged_file}")
        return merged_file

    def run_batch(self, persona_names: List[str], resume: bool = False) -> bool:
        """Run multiple personas, dedupe results. Returns True if all successful."""
        personas = load_personas(self.personas_file)
        requested = {k: personas[k] for k in persona_names if k in personas}

        if not requested:
            self.logger.error(f"No valid personas found. Available: {list(personas.keys())}")
            return False

        self.logger.info(f"Running {len(requested)} personas: {', '.join(requested.keys())}")

        output_files = []
        failed = []

        for name, config in requested.items():
            success = self.run_persona(name, config, resume=resume)
            if success:
                output_files.append(self.output_dir / config.get("output", f"contacts_{name}.csv"))
            else:
                failed.append(name)

        if failed:
            self.logger.warning(f"Failed personas: {', '.join(failed)}")

        if output_files:
            self.dedupe_results(output_files)
            return len(failed) == 0
        else:
            self.logger.error("No personas completed successfully")
            return False

# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Batch runner for Apollo persona extractions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  doppler run -- python apollo-batch-runner.py --personas influencer_marketing brand_partnerships
  doppler run -- python apollo-batch-runner.py --all --dry-run
  doppler run -- python apollo-batch-runner.py --persona influencer_marketing --resume
        """
    )

    parser.add_argument(
        "--personas",
        nargs="+",
        help="Persona names to run (space-separated)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all personas from apollo-personas.yaml"
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Output directory for all results"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run (no API calls)"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoints (if interrupted)"
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(output_dir)

    runner = ApolloBackRunner(output_dir, logger, dry_run=args.dry_run)
    personas = load_personas(runner.personas_file)

    if args.all:
        persona_names = list(personas.keys())
    elif args.personas:
        persona_names = args.personas
    else:
        logger.error("Specify --personas or --all")
        parser.print_help()
        sys.exit(1)

    success = runner.run_batch(persona_names, resume=args.resume)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
