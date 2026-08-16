#!/usr/bin/env bash
# scripts/verify_task5.sh
#
# Runs exactly the verification sequence agreed for Foundation Task 5,
# in order, and stops at the first failure so you know precisely which
# layer broke. Run this from the repo root, inside your real venv
# (fastapi/sqlalchemy/sentry-sdk/etc. installed).
#
# Usage:
#   bash scripts/verify_task5.sh
#   DATABASE_URL=postgresql://... bash scripts/verify_task5.sh   # to also run @pytest.mark.postgres
#
# Every step's raw pytest output is saved under ./verification_logs/
# so you can paste the relevant log back for a second pass if
# something fails.

set -uo pipefail

LOG_DIR="verification_logs"
mkdir -p "$LOG_DIR"
TS="$(date +%Y%m%d_%H%M%S)"
SUMMARY="$LOG_DIR/task5_summary_${TS}.txt"
FAILED=0

echo "FOUNDATION TASK 5 — verification run started $(date)" | tee "$SUMMARY"
echo "==============================================================" | tee -a "$SUMMARY"

run_step () {
    local name="$1"
    local cmd="$2"
    local logfile="$LOG_DIR/${name}_${TS}.log"

    echo "" | tee -a "$SUMMARY"
    echo "--- $name ---" | tee -a "$SUMMARY"
    echo "\$ $cmd" | tee -a "$SUMMARY"

    if eval "$cmd" > "$logfile" 2>&1; then
        echo "PASS  (log: $logfile)" | tee -a "$SUMMARY"
    else
        echo "FAIL  (log: $logfile)" | tee -a "$SUMMARY"
        echo "----- last 40 lines -----" | tee -a "$SUMMARY"
        tail -n 40 "$logfile" | tee -a "$SUMMARY"
        FAILED=1
    fi
}

# 1. This task's new tests, in isolation first — easiest to read if
#    something's wrong specifically with Task 5's changes.
run_step "task5_error_handling" "pytest tests/test_error_handling.py -v"

# 2. The two suites most likely to interact with Task 5's changes
#    (auth.py's 401/403 fix touches both tenant isolation and RBAC paths).
run_step "tenant_isolation" "pytest tests/test_tenant_isolation.py -m security -v"
run_step "rbac" "pytest tests/test_rbac.py -m security -v"

# 3. Full suite.
run_step "full_suite" "pytest tests/ -v"

# 4. PostgreSQL-only tests — only meaningful if DATABASE_URL points at
#    a real Postgres instance; otherwise slowapi/conftest auto-skips them.
if [[ "${DATABASE_URL:-}" == postgresql* ]]; then
    run_step "postgres_only" "pytest tests/ -m postgres -v"
else
    echo "" | tee -a "$SUMMARY"
    echo "--- postgres_only ---" | tee -a "$SUMMARY"
    echo "SKIPPED — DATABASE_URL is not set to a postgresql:// URL." | tee -a "$SUMMARY"
    echo "Re-run with: DATABASE_URL=postgresql://... bash scripts/verify_task5.sh" | tee -a "$SUMMARY"
fi

echo "" | tee -a "$SUMMARY"
echo "==============================================================" | tee -a "$SUMMARY"
if [[ "$FAILED" -eq 0 ]]; then
    echo "ALL STEPS PASSED. Summary: $SUMMARY" | tee -a "$SUMMARY"
    echo "Next: run PRODUCTION_VERIFICATION_CHECKLIST.md against staging/prod." | tee -a "$SUMMARY"
else
    echo "AT LEAST ONE STEP FAILED. See logs in $LOG_DIR/. Summary: $SUMMARY" | tee -a "$SUMMARY"
fi

exit $FAILED
