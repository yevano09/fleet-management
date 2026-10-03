#!/usr/bin/env bash
# pip-audit wrapper with documented ignores for unfixable transitive CVEs.
#
# chromadb 1.1.1 (transitive via crewai==1.15.23, which pins chromadb~=1.1.0)
# has 5 CVEs with no patched release available as of 2026-10-03. These are in
# crewai's knowledge/vector-search code paths that Fleet Commander never
# exercises. When a fix ships, remove the --ignore-vuln flags and bump crewai.
#
# Usage: bash scripts/audit.sh
set -euo pipefail
pip-audit \
  --requirement requirements.txt \
  --ignore-vuln PYSEC-2026-311 \
  --ignore-vuln PYSEC-2026-3813 \
  --ignore-vuln PYSEC-2026-3814 \
  --ignore-vuln PYSEC-2026-3815
