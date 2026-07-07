#!/bin/bash
# Quick validation that API keys and dependencies are set up correctly.
# Usage: doppler run -- bash apollo-validate-setup.sh

set -e

echo "=== Apollo Contact Extractor — Setup Validation ==="
echo ""

# Check Python
echo "✓ Checking Python..."
python3 --version

# Check dependencies
echo "✓ Checking Python packages..."
python3 -c "import yaml; print('  yaml OK')" || echo "  ✗ pyyaml missing: pip install pyyaml"
python3 -c "import urllib; print('  urllib OK')" || echo "  ✗ urllib missing (should be builtin)"

# Check Doppler
echo "✓ Checking Doppler..."
doppler --version

# Check API keys (via Doppler)
echo "✓ Checking API keys..."
if APOLLO_KEY=$(doppler secrets get APOLLO_API_KEY --project ent-agency-automation --config dev --plain 2>/dev/null); then
    if [ -n "$APOLLO_KEY" ]; then
        echo "  ✓ APOLLO_API_KEY set (length: ${#APOLLO_KEY})"
    else
        echo "  ✗ APOLLO_API_KEY is empty"
    fi
else
    echo "  ✗ APOLLO_API_KEY not found in Doppler"
fi

if VERIFIER_KEY=$(doppler secrets get MILLION_VERIFIER_API_KEY --project ent-agency-automation --config dev --plain 2>/dev/null); then
    if [ -n "$VERIFIER_KEY" ]; then
        echo "  ✓ MILLION_VERIFIER_API_KEY set (length: ${#VERIFIER_KEY})"
    else
        echo "  ✗ MILLION_VERIFIER_API_KEY is empty"
    fi
else
    echo "  ⚠ MILLION_VERIFIER_API_KEY not found (optional, email validation will be skipped)"
fi

# Check script files
echo "✓ Checking script files..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/apollo-contact-extractor.py" ] && echo "  ✓ apollo-contact-extractor.py" || echo "  ✗ apollo-contact-extractor.py missing"
[ -f "$SCRIPT_DIR/apollo-batch-runner.py" ] && echo "  ✓ apollo-batch-runner.py" || echo "  ✗ apollo-batch-runner.py missing"
[ -f "$SCRIPT_DIR/apollo-personas.yaml" ] && echo "  ✓ apollo-personas.yaml" || echo "  ✗ apollo-personas.yaml missing"

echo ""
echo "=== Ready? Test with dry-run ==="
echo ""
echo "doppler run -- python3 $SCRIPT_DIR/apollo-contact-extractor.py \\"
echo "  --job-titles 'Influencer Marketing Manager' \\"
echo "  --industries 'Supplements' \\"
echo "  --dry-run"
echo ""
echo "or"
echo ""
echo "doppler run -- python3 $SCRIPT_DIR/apollo-batch-runner.py --all --dry-run"
echo ""
