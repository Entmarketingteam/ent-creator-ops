# Apollo Contact Extractor — Production-Grade, Self-Healing

Replicatable contact extraction pipeline for any vertical. Queries Apollo API, validates emails, handles failures automatically, resumes from checkpoints.

## Quick Start

### 1. Verify Doppler secrets

```bash
doppler secrets get APOLLO_API_KEY --project ent-agency-automation --config dev
doppler secrets get MILLION_VERIFIER_API_KEY --project ent-agency-automation --config dev
```

### 2. Run single extraction

```bash
doppler run -- python apollo-contact-extractor.py \
  --job-titles "Influencer Marketing Manager" "Brand Partnership Director" \
  --industries "Supplements" "Beauty" \
  --output contacts.csv
```

### 3. Run batch (multiple personas)

```bash
doppler run -- python apollo-batch-runner.py --all
doppler run -- python apollo-batch-runner.py --personas influencer_marketing brand_partnerships
```

## How It Works

### Self-Healing Features

| Feature | How | Benefit |
|---------|-----|---------|
| **Exponential backoff** | 429/5xx errors → wait 1s, 2s, 4s, 8s... (max 60s) | Survives API rate limits, server hiccups |
| **Checkpointing** | After each page, write `contacts_checkpoint.json` | Resume mid-extraction if interrupted |
| **Email validation** | Million Verifier per contact | Only valid addresses in output |
| **Deduplication** | In-memory + cross-CSV dedup | No duplicate contacts across runs |
| **Logging** | Detailed logs to file + stdout | Debug failures without code changes |
| **Timeout recovery** | 30s per request, retry on timeout | No hangs |

### Architecture

```
apollo-contact-extractor.py
├── RetryConfig: backoff strategy
├── ApolloClient: search + pagination
├── MillionVerifierClient: email validation
├── Checkpoint: resume state
└── main(): orchestrate pipeline

apollo-batch-runner.py
├── loads apollo-personas.yaml
├── runs multiple personas in sequence
├── dedupes results across all runs
└── merges into contacts_merged_deduped.csv

apollo-personas.yaml
└── reusable job title + industry combos per vertical
```

## Usage Patterns

### Pattern 1: Single Vertical (No Babysitting)

```bash
doppler run -- python apollo-contact-extractor.py \
  --job-titles "Influencer Marketing Manager" "Brand Director" \
  --industries "Supplements" "Beauty" \
  --output ~/Desktop/supplements_beauty_contacts.csv
```

**What happens if interrupted:**
- Script exits
- Checkpoint saved at `contacts_checkpoint.json`
- Next run with `--resume` picks up from last page

```bash
doppler run -- python apollo-contact-extractor.py \
  --job-titles "Influencer Marketing Manager" "Brand Director" \
  --industries "Supplements" "Beauty" \
  --output ~/Desktop/supplements_beauty_contacts.csv \
  --resume
```

### Pattern 2: Multi-Persona Batch (No Babysitting)

Edit `apollo-personas.yaml`, add new persona. Then:

```bash
doppler run -- python apollo-batch-runner.py --all
```

Runs all personas sequentially, dedupes across all outputs, merges into `contacts_merged_deduped.csv`.

**If one persona fails:**
- Script logs failure
- Continues with next persona
- You can retry failed one later: `--persona influencer_marketing --resume`

### Pattern 3: New Vertical (Copy-Paste Template)

1. Open `apollo-personas.yaml`
2. Copy `_template` block
3. Define job titles + industries for new vertical
4. Run: `doppler run -- python apollo-batch-runner.py --personas my_new_vertical`

### Pattern 4: Dry Run (Test Without Costs)

```bash
doppler run -- python apollo-contact-extractor.py \
  --job-titles "Test Title" \
  --industries "Test Industry" \
  --dry-run
```

No API calls, logs what *would* happen.

## Output Files

| File | Purpose |
|------|---------|
| `contacts_YYYY.csv` | Raw results per persona/run |
| `contacts_merged_deduped.csv` | Final output (batch runner only) |
| `contacts_checkpoint.json` | Resume state (auto-cleaned on success) |
| `apollo_extraction_YYYY.log` | Detailed logs |

## Handling Failures

### API Key Invalid
```
[ERROR] Auth failure (403). Check APOLLO_API_KEY in Doppler.
```
**Fix:** `doppler secrets get APOLLO_API_KEY` → verify in 1Password / Doppler UI

### Rate Limited (Too Many Requests)
```
[WARNING] Rate limited (429). Backoff 4.3s (attempt 2/5)
```
**Automatic:** Script exponentially backs off, retries up to 5 times. No action needed.

### Server Error (500/502/503)
```
[WARNING] Server error 503. Backoff 8.1s (attempt 3/5)
```
**Automatic:** Script retries with backoff. Apollo servers usually recover in seconds.

### Timeout (Network Hiccup)
```
[WARNING] Timeout/connection error. Backoff 2.1s (attempt 1/5)
```
**Automatic:** Script retries. If repeats, check network / firewall.

### Email Validation Fails
```
[WARNING] Failed to validate {email}, assuming valid and continuing
```
**Behavior:** Script includes email anyway (fail-open). Safe — helps capture leads even if verifier is slow.

## Logging

All logs to `apollo_extraction_YYYY.log`. Key lines:

```
[INFO] Searching page 1: titles=['...'], industries=['...']
[INFO] Page 1: 97 contacts, 4,352 total available
[DEBUG] Email john@acme.com: valid
[INFO] Checkpointed: 97 contacts, page 1
[WARNING] Rate limited (429). Backoff 2.1s (attempt 1/5)
[ERROR] HTTP 401: Check APOLLO_API_KEY in Doppler.
```

Monitor logs while script runs (optional):
```bash
tail -f apollo_extraction_*.log
```

## Customizing

### Change Backoff Strategy

Edit `apollo-contact-extractor.py`, line ~75:
```python
self.retry_config = RetryConfig(max_attempts=5, base_delay=2.0, max_delay=60.0)
```

- `max_attempts`: how many retries before giving up
- `base_delay`: initial wait time (seconds)
- `max_delay`: cap on exponential backoff

### Change Apollo Filters

Edit `apollo-contact-extractor.py`, line ~165:
```python
filters = []
for title in job_titles:
    filters.append({
        "field_name": "current_title",
        "operator": "contains",  # or "equals", "starts_with"
        "value": title
    })
```

Available operators: `contains`, `equals`, `starts_with`, `ends_with`, `in` (for lists).

### Add New API (Findymail, Clay, etc.)

Create new client class (e.g., `FindymailClient`), similar to `ApolloClient`:
```python
class FindymailClient:
    def __init__(self, api_key, logger):
        self.api_key = api_key
        self.logger = logger
    
    def search_contacts(self, domain, ...):
        # Same retry logic pattern
        return api_call_with_retry(url, headers, body, self.retry_config, self.logger)
```

Then in `main()`, instantiate and call it before CSV write.

## Performance

- **Page 1:** ~10s (API call + email validation)
- **Subsequent pages:** ~8s each (cached connections)
- **Total for 10k contacts (~100 pages):** ~15 mins

To speed up:
- Increase `--per-page` in Apollo API call (max 100, default 100)
- Skip email validation: `--skip-validation` (faster but less safe)
- Parallelize: modify to use `ThreadPoolExecutor` for multi-page fetches (not included, safe to add)

## Gotchas

1. **Apollo job titles are fuzzy.** "Influencer Marketing Manager" may also match "Marketing Manager who handles influencers." Use multiple titles to narrow.
2. **Emails are best-guess.** Apollo generates `first.last@company.com` format. Million Verifier catches typos, but real inboxes can vary.
3. **Industries are broad.** "Supplements" includes big pharma, small brands, retailers. Filter by company size if needed.
4. **Rate limits.** Apollo typically 100 req/min per API key. If hitting limits, space runs 30+ minutes apart or use multiple keys (not supported yet — ask).
5. **Checkpoint cleanup.** On success, `contacts_checkpoint.json` is deleted. If you want to keep it, comment out line ~330 in `apollo-contact-extractor.py`.

## Troubleshooting

**Script hangs:**
- Check `tail -f apollo_extraction_*.log` for last message
- If no recent lines, process is stuck in API call (timeout)
- Kill and re-run with `--resume`

**Missing contacts:**
- Apollo may not have data for that job title in that industry
- Try broader titles ("Marketing Manager" instead of "Influencer Marketing Manager")
- Try `--dry-run` first to see what filters are sent

**Duplicate contacts across runs:**
- Batch runner dedupes by email automatically
- If running single persona multiple times, expect dupes (expected — each run is independent)

**Email validation too slow:**
- Skip it: `--skip-validation` (trades speed for accuracy)
- Or, increase timeouts: edit `MillionVerifierClient`, change `timeout=30` to 60

## What's Next

- [ ] Add Findymail as fallback data source (for non-Apollo results)
- [ ] Implement job change detection (LinkedIn activity scrape)
- [ ] Add Clay enrichment (additional fields: company revenue, growth, etc.)
- [ ] Parallelize page fetching (3-5x speedup)
- [ ] CLI dashboard (progress bar, real-time stats)

---

**Questions?** Check logs first. If stuck, add `--dry-run` to see what *would* happen without costs.
