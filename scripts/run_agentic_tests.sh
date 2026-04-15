#!/usr/bin/env bash
# run_agentic_tests.sh — Run the Wee Native Runtime agentic test suite.
#
# Usage:
#   ./scripts/run_agentic_tests.sh              # All tests (unit + live)
#   ./scripts/run_agentic_tests.sh --unit        # Unit/mock tests only (fast)
#   ./scripts/run_agentic_tests.sh --ollama      # Ollama live tests only
#   ./scripts/run_agentic_tests.sh --openrouter   # OpenRouter live tests only
#   ./scripts/run_agentic_tests.sh --report       # Generate HTML report
#
set -euo pipefail
cd "$(dirname "$0")/.."

PYTEST_ARGS=(-v --tb=short)
TEST_FILE="tests/test_wee_runtime_agentic.py"

case "${1:-all}" in
  --unit)
    echo "▸ Running unit/mock tests only (no live API calls)..."
    PYTEST_ARGS+=(-k "not Live")
    ;;
  --ollama)
    echo "▸ Running Ollama live tests..."
    PYTEST_ARGS+=(-k "OllamaLive")
    ;;
  --openrouter)
    echo "▸ Running OpenRouter live tests..."
    PYTEST_ARGS+=(-k "OpenRouterLive")
    ;;
  --live)
    echo "▸ Running all live tests (Ollama + OpenRouter)..."
    PYTEST_ARGS+=(-k "Live")
    ;;
  --report)
    echo "▸ Running all tests with HTML report..."
    PYTEST_ARGS+=(--html=reports/agentic_test_report.html --self-contained-html 2>/dev/null || true)
    mkdir -p reports
    ;;
  all|"")
    echo "▸ Running full agentic test suite..."
    ;;
  *)
    echo "Usage: $0 [--unit|--ollama|--openrouter|--live|--report]"
    exit 1
    ;;
esac

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Wee Native Runtime — Agentic Test Suite"
echo "  $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check providers
if curl -s -o /dev/null -w "%{http_code}" http://192.168.1.101:11434/api/tags 2>/dev/null | grep -q 200; then
  echo "  ✅ Ollama: reachable"
else
  echo "  ⚠️  Ollama: not reachable (live tests will be skipped)"
fi

if [ -n "${OPENROUTER_API_KEY:-}" ]; then
  echo "  ✅ OpenRouter: API key set"
else
  echo "  ⚠️  OpenRouter: no API key (live tests will be skipped)"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

python3 -m pytest "${PYTEST_ARGS[@]}" "$TEST_FILE" 2>&1

EXIT_CODE=$?
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ $EXIT_CODE -eq 0 ]; then
  echo "  ✅ ALL TESTS PASSED"
else
  echo "  ❌ SOME TESTS FAILED (exit code: $EXIT_CODE)"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
exit $EXIT_CODE
