#!/usr/bin/env bash
# Fleet Commander — Vulnerability Scanner
# Runs pip-audit (dependencies) + bandit (static analysis)
# Usage: bash scripts/vuln-scan.sh
set -euo pipefail

cd "$(dirname "$0")/.."
VENV="/tmp/fleet_vuln_scan"
echo "=== Fleet Commander Vulnerability Scan ==="
echo ""

# ── pip-audit: dependency vulnerabilities ──
echo "--- [1/2] pip-audit (dependency vulnerabilities) ---"
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"
pip install -q pip-audit 2>/dev/null
pip install -q -r requirements.txt 2>/dev/null
pip-audit -r requirements.txt
VULN_EXIT=$?
deactivate

echo ""

# ── bandit: static code analysis ──
echo "--- [2/2] bandit (static code analysis) ---"
if ! command -v bandit &>/dev/null; then
    python3 -m venv /tmp/fleet_bandit
    source /tmp/fleet_bandit/bin/activate
    pip install -q bandit 2>/dev/null
fi
bandit -r app/ agents/ simulator/ 2>&1 | tail -10
BANDIT_EXIT=$?

echo ""
echo "=== Summary ==="
echo "  pip-audit: $( [ $VULN_EXIT -eq 0 ] && echo 'PASS' || echo 'FAIL' )"
echo "  bandit:    $( [ $BANDIT_EXIT -eq 0 ] && echo 'PASS' || echo 'FAIL' )"
echo ""
echo "For CodeQL (deep static analysis):"
echo "  Run CodeQL CLI locally:"
echo "    codeql database create /tmp/fleet-db --language=python"
echo "    codeql database analyze /tmp/fleet-db --format=sarif-latest --output=results.sarif"
echo ""
echo "Or enable GitHub CodeQL scanning in: Settings > Security > Code security > Code scanning"
