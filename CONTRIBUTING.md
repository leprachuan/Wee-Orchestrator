# Contributing to Wee-Orchestrator

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing to the project.

## Code of Conduct

This project is committed to providing a welcoming and inclusive environment. Please be respectful and constructive in all interactions.

## Getting Started

### Prerequisites

- Python 3.8+
- Node.js 18+ (for installing CLI tools)
- Git

### Setup Development Environment

1. **Fork the repository** on GitHub

2. **Clone your fork**
   ```bash
   git clone https://github.com/your-username/n8n-copilot-shim.git
   cd n8n-copilot-shim
   ```

3. **Add upstream remote**
   ```bash
   git remote add upstream https://github.com/leprachuan/n8n-copilot-shim.git
   ```

4. **Install AI CLI tools** (choose at least one)

   Claude Code:
   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   ```

   GitHub Copilot:
   ```bash
   npm install -g @github/copilot
   ```

   OpenCode:
   ```bash
   curl -fsSL https://opencode.ai/install | bash
   ```

5. **Create configuration**
   ```bash
   cp agents.example.json agents.json
   # Edit agents.json with your repository paths
   ```

## Development Workflow

### Choose a Branch

Development may happen on any branch, including `main`. Feature branches are
useful when they make review, experimentation, or parallel work clearer, but
they are optional. Maintainers may commit directly to `main` when that is the
most appropriate workflow; a pull request is not a release gate.

```bash
# Optional: create a focused branch
git checkout -b feature/your-feature-name
# Or work on an existing branch, including main
git checkout -b fix/your-bug-fix
```

Branch naming conventions:
- `feature/` - for new features
- `fix/` - for bug fixes
- `docs/` - for documentation updates
- `test/` - for test improvements
- `refactor/` - for code refactoring

### Make Changes

1. Write your code following the project's style
2. Add or update tests for your changes
3. Update documentation if needed

### Run Tests

```bash
# Run all tests
./run_tests.sh

# Run with verbose output
./run_tests.sh -v

# Run specific test class
./run_tests.sh -t tests.test_agent_manager.TestSlashCommands

# Generate coverage report
./run_tests.sh -c
```

Run the relevant tests before publishing a change or creating a release.

### Commit Changes

Write clear, descriptive commit messages:

```bash
git commit -m "Add feature: brief description

More detailed explanation of the changes if needed.
Include references to related issues: Closes #123"
```

### Push and optionally create a Pull Request

Push the branch that contains the completed work. A pull request is optional;
use one when it improves collaboration or review.

```bash
# Push the current branch
git push origin feature/your-feature-name

# Optional: create a PR on GitHub
gh pr create --title "Brief description" --body "Detailed description"
```

## What is released

Branches are development locations, not distribution channels. Only published
GitHub release assets are supported deliverables:

- API releases use `api-vMAJOR.MINOR.PATCH` and are documented in
  [API releases](docs/API_RELEASES.md).
- macOS releases use `macos-vMAJOR.MINOR.PATCH` and are documented in the
  macOS repository's [release guide](https://github.com/leprachuan/wee-orchestrator-macos/blob/main/docs/RELEASING.md).

Create a release only from a tested, committed revision. The release tag and
its artifacts—not whether the source came from a feature branch or `main`—are
what users install.

## Pull Request Guidelines

When choosing to submit a pull request, please ensure:

1. **Tests Pass**: All tests must pass (`./run_tests.sh`)
2. **New Tests**: Add tests for new functionality
3. **Documentation**: Update README or docs if needed
4. **Clear Description**: Explain what and why
5. **Related Issues**: Reference any related issues

### PR Template

PRs should include:
- Description of changes
- Type of change (bug, feature, docs)
- Related issues
- Testing performed
- Checklist completion

The project may use automated checks and review; those checks complement, but
do not replace, the required test and release-artifact validation.

## Code Style

### Python

- Follow PEP 8
- Use meaningful variable names
- Add docstrings to functions
- Keep functions focused and modular

Example:
```python
def my_function(param: str) -> str:
    """
    Brief description of what the function does.

    Args:
        param: Description of param

    Returns:
        Description of return value
    """
    result = param.upper()
    return result
```

### Testing

- Write tests for new functionality
- Use descriptive test names: `test_<feature>_<scenario>`
- Include docstrings explaining what the test verifies
- Aim for high coverage of new code

Example:
```python
def test_agent_set_success(self):
    """Test successfully switching to a different agent."""
    result = self.manager.set_agent("test_session", "agent1")
    self.assertIn("agent1", result)
```

## Testing Guidelines

### Running Tests

```bash
# All tests
python3 -m unittest discover -s tests -p "test_*.py" -v

# Specific test class
python3 -m unittest tests.test_agent_manager.TestSlashCommands -v

# Specific test method
python3 -m unittest tests.test_agent_manager.TestSlashCommands.test_help_command -v
```

### Writing Tests

- Tests should be isolated (use temporary files/directories)
- Use mocking for external dependencies
- Test both success and failure cases
- Keep tests fast (mocking prevents slow I/O)

See [tests/README.md](tests/README.md) for detailed testing documentation.

## Documentation

- Update README.md for user-facing changes
- Update tests/README.md for testing changes
- Include docstrings in code
- Provide examples for new features

## Issues

### Reporting Bugs

When reporting a bug, include:
- Clear description of the issue
- Steps to reproduce
- Expected behavior
- Actual behavior
- Environment (OS, Python version, etc.)

### Requesting Features

When requesting a feature:
- Clear description of the feature
- Why it's needed
- Proposed solution
- Alternative approaches

## Review Process

1. **Automated Checks**: GitHub Actions runs tests
2. **Code Review**: Maintainers review the code
3. **Approval**: PR requires at least one approval
4. **Merge**: Only maintainers can merge

## Recognition

Contributors will be:
- Added to CONTRIBUTORS.md file
- Mentioned in release notes
- Acknowledged in project documentation

## Questions?

- Open an issue for questions
- Check existing issues/discussions first
- Join the community for discussions

## License

By contributing, you agree that your contributions will be licensed under the same license as the project (Apache 2.0).

---

**Thank you for contributing to Wee-Orchestrator!** 🚀
