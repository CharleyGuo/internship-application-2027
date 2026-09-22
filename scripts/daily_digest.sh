#!/usr/bin/env bash
set -euo pipefail

# Internship Application Assistant - Daily 4:00 AM Automated Runner
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

LOG_FILE="${PROJECT_ROOT}/reports/schedule.log"
mkdir -p "${PROJECT_ROOT}/reports"

echo "========================================================" >> "${LOG_FILE}"
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Starting Daily 4:00 AM Internship Automation" >> "${LOG_FILE}"
echo "Project Root: ${PROJECT_ROOT}" >> "${LOG_FILE}"

cd "${PROJECT_ROOT}"

PYTHON="${PROJECT_ROOT}/.venv/bin/python"
if [ ! -f "${PYTHON}" ]; then
    echo "[ERROR] Virtualenv python not found at ${PYTHON}" >> "${LOG_FILE}"
    exit 1
fi

if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
fi

# 1. Source new postings from GitHub lists & ATS APIs
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Step 1: Running sourcing discovery..." >> "${LOG_FILE}"
"${PYTHON}" cli.py source >> "${LOG_FILE}" 2>&1 || true

# 2. Filter & Rank newly discovered postings
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Step 2: Scoring and ranking qualified postings..." >> "${LOG_FILE}"
"${PYTHON}" cli.py rank >> "${LOG_FILE}" 2>&1 || true

# 3. Generate Daily Digest (reports/YYYY-MM-DD.md) & update tracker.xlsx
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Step 3: Generating daily digest and Excel tracker..." >> "${LOG_FILE}"
"${PYTHON}" cli.py digest >> "${LOG_FILE}" 2>&1

# 4. Synchronize with Google Drive & Google Spreadsheet
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Step 4: Syncing with Google Drive folder..." >> "${LOG_FILE}"
"${PYTHON}" cli.py sync --drive >> "${LOG_FILE}" 2>&1 || true

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Daily automation completed successfully." >> "${LOG_FILE}"
echo "========================================================" >> "${LOG_FILE}"
