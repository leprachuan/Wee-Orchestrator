#!/bin/bash
# Test runner script for n8n-copilot-shim
# Usage: ./run_tests.sh [options]

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Parse arguments
VERBOSE=false
COVERAGE=false
SPECIFIC_TEST=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -c|--coverage)
            COVERAGE=true
            shift
            ;;
        -t|--test)
            SPECIFIC_TEST="$2"
            shift 2
            ;;
        -h|--help)
            echo "Test runner for n8n-copilot-shim"
            echo ""
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  -v, --verbose    Show verbose test output"
            echo "  -c, --coverage   Generate coverage report"
            echo "  -t, --test TEST  Run specific test (e.g., tests.test_agent_manager.TestSlashCommands)"
            echo "  -h, --help       Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                           # Run all tests"
            echo "  $0 -v                        # Run with verbose output"
            echo "  $0 -t tests.test_agent_manager.TestSlashCommands  # Run specific test class"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${YELLOW}Running tests for n8n-copilot-shim...${NC}"
echo ""

# Build command
CMD="python3 -m unittest"

if [ -z "$SPECIFIC_TEST" ]; then
    CMD="$CMD discover -s tests -p 'test_*.py'"
else
    CMD="$CMD $SPECIFIC_TEST"
fi

if [ "$VERBOSE" = true ]; then
    CMD="$CMD -v"
fi

# Run tests
if eval "$CMD"; then
    echo ""
    echo -e "${GREEN}✓ All tests passed!${NC}"

    # Run coverage if requested
    if [ "$COVERAGE" = true ]; then
        echo ""
        echo -e "${YELLOW}Generating coverage report...${NC}"
        if command -v coverage &> /dev/null; then
            coverage run -m unittest discover -s tests -p 'test_*.py'
            coverage report
            coverage html
            echo -e "${GREEN}Coverage report generated in htmlcov/index.html${NC}"
        else
            echo -e "${YELLOW}Coverage tool not installed. Install with: pip3 install coverage${NC}"
        fi
    fi

    exit 0
else
    echo ""
    echo -e "${RED}✗ Tests failed!${NC}"
    exit 1
fi
