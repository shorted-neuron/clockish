#!/usr/bin/env bash
# scripts/pre-pr-check.sh  --  Run every local check GitHub Actions runs, in
# the same order, before raising a PR.
#
# Usage:
#   bash scripts/pre-pr-check.sh
#
# Mirrors .github/workflows/pr-tests.yml:
#   1. pre-commit run --all-files   (file hygiene, yamllint, clockish-validate, mypy)
#   2. pytest --cov=src/clockish    (unit tests + coverage report)
#   3. ruff check .                 (lint -- non-blocking in CI, reported here too)
#   4. mypy src/clockish            (type check -- redundant with pre-commit's mypy
#                                     hook, but run directly too in case the two
#                                     environments ever drift)
#
# Exits non-zero on the first HARD failure (pre-commit, pytest, or direct mypy).
# ruff is reported but does NOT fail the script (matches CI's `|| true`) --
# see AGENTS.md's "Common edits" / lint sections for why some rules are
# intentionally not enforced.

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

_fail=0
_step() {
    echo ""
    echo "=== $1 ==="
}

_step "1/4  pre-commit run --all-files"
if ! pre-commit run --all-files; then
    echo "FAILED: pre-commit" >&2
    _fail=1
fi

_step "2/4  pytest --cov=src/clockish"
if ! pytest --cov=src/clockish --cov-report=term-missing; then
    echo "FAILED: pytest" >&2
    _fail=1
fi

_step "3/4  ruff check .  (non-blocking, informational)"
ruff check . || echo "  (ruff found issues -- non-blocking, see AGENTS.md)"

_step "4/4  mypy src/clockish"
if ! mypy src/clockish; then
    echo "FAILED: mypy" >&2
    _fail=1
fi

echo ""
if [ "$_fail" -ne 0 ]; then
    echo "=== FAILED -- fix the above before raising a PR. ==="
    exit 1
fi
echo "=== All checks passed. Safe to raise a PR. ==="
