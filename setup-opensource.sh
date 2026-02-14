#!/bin/bash
# Setup script for open source repository configuration
# This script makes the repository public and sets up proper merge request controls

set -e

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║     Open Source Repository Setup Script                        ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Check if gh is installed
if ! command -v gh &> /dev/null; then
    echo "❌ GitHub CLI (gh) is not installed."
    echo "Install it with: brew install gh"
    exit 1
fi

# Check authentication
if ! gh auth status > /dev/null 2>&1; then
    echo "❌ Not authenticated with GitHub."
    echo ""
    echo "To authenticate, run:"
    echo "  gh auth login -p https -w --skip-ssh-key"
    echo ""
    echo "Or with a token:"
    echo "  echo 'your-token' | gh auth login --with-token"
    exit 1
fi

echo "✓ GitHub CLI authenticated"
echo ""

# Get repository info
REPO=$(git config --get remote.origin.url | sed 's/.*://;s/.git$//')
if [ -z "$REPO" ]; then
    echo "❌ Could not determine repository. Ensure you're in a git repository."
    exit 1
fi

echo "Repository: $REPO"
echo ""

# Step 1: Make repository public
echo "Step 1/5: Making repository public..."
gh repo edit "$REPO" --visibility public --accept-visibility-change-consequences && echo "✓ Repository is now public" || echo "⚠ Could not modify visibility (may already be public)"
echo ""

# Step 2: Set up branch protection for main
echo "Step 2/5: Setting up branch protection rules for 'main'..."
gh api repos/"$REPO"/branches/main/protection \
  -X PUT \
  -F 'required_status_checks={"strict":true,"contexts":[]}' \
  -F 'enforce_admins=true' \
  -F 'required_pull_request_reviews={"dismissal_restrictions":{},"dismiss_stale_reviews":true,"require_code_owner_reviews":true,"required_approving_review_count":1}' \
  -F 'allow_force_pushes=false' \
  -F 'allow_deletions=false' && echo "✓ Branch protection enabled" || echo "⚠ Could not set branch protection"
echo ""

# Step 3: Create CODEOWNERS file
echo "Step 3/5: Creating CODEOWNERS file..."
cat > CODEOWNERS << 'CODEOWNERS_EOF'
# Default owners for entire repository
* @leprachuan

# Test files
/tests/ @leprachuan

# Documentation
/*.md @leprachuan
CODEOWNERS_EOF
git add CODEOWNERS
git commit -m "Add CODEOWNERS file for code review management" || echo "⚠ CODEOWNERS already exists"
echo "✓ CODEOWNERS file created"
echo ""

# Step 4: Create issue templates
echo "Step 4/5: Creating issue templates..."
mkdir -p .github/ISSUE_TEMPLATE

cat > .github/ISSUE_TEMPLATE/bug_report.md << 'BUG_EOF'
---
name: Bug Report
about: Report a bug to help us improve
title: "[BUG] "
labels: bug
assignees: leprachuan
---

## Description
A clear and concise description of what the bug is.

## Steps to Reproduce
1. Step one
2. Step two
3. ...

## Expected Behavior
What should happen

## Actual Behavior
What actually happens

## Environment
- OS: [e.g., macOS 12.0]
- Python version: [e.g., 3.9]
- Agent Manager version: [e.g., 1.0.0]

## Additional Context
Add any other context about the problem here.
BUG_EOF

cat > .github/ISSUE_TEMPLATE/feature_request.md << 'FEATURE_EOF'
---
name: Feature Request
about: Suggest an idea for this project
title: "[FEATURE] "
labels: enhancement
assignees: leprachuan
---

## Description
A clear and concise description of what you want to happen.

## Motivation
Why is this feature needed? What problem does it solve?

## Proposed Solution
Describe how you think this should work.

## Alternatives
Any alternative approaches you've considered.

## Additional Context
Add any other context or screenshots here.
FEATURE_EOF

cat > .github/ISSUE_TEMPLATE/config.yml << 'CONFIG_EOF'
blank_issues_enabled: false
contact_links:
  - name: Discord/Slack Community
    url: https://github.com/leprachuan
    about: Join our community for discussions
CONFIG_EOF

git add .github/ISSUE_TEMPLATE/
git commit -m "Add issue templates for bug reports and feature requests" || echo "⚠ Issue templates already exist"
echo "✓ Issue templates created"
echo ""

# Step 5: Create pull request template
echo "Step 5/5: Creating pull request template..."
mkdir -p .github

cat > .github/pull_request_template.md << 'PR_EOF'
## Description
Brief description of the changes in this PR.

## Type of Change
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to change)
- [ ] Documentation update

## Related Issues
Closes #(issue number)

## Testing
Describe the tests you ran and how to reproduce them.

## Checklist
- [ ] Tests pass locally (`./run_tests.sh`)
- [ ] I have added tests for new functionality
- [ ] Documentation has been updated
- [ ] No new warnings generated
- [ ] Changes follow the project's code style

## Screenshots (if applicable)
Add screenshots for UI changes.
PR_EOF

git add .github/pull_request_template.md
git commit -m "Add pull request template for consistent PR formatting" || echo "⚠ PR template already exists"
echo "✓ Pull request template created"
echo ""

# Push all new commits
echo "Pushing changes to GitHub..."
git push origin main && echo "✓ Changes pushed" || echo "⚠ No new commits to push"
echo ""

# Final summary
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║               ✅ SETUP COMPLETE                               ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "Your repository is now configured for open source:"
echo ""
echo "✓ Repository is PUBLIC"
echo "✓ Branch 'main' is PROTECTED"
echo "✓ Pull requests REQUIRE APPROVAL before merge"
echo "✓ Code owners must review changes"
echo "✓ Issue templates configured"
echo "✓ Pull request template configured"
echo ""
echo "Access repository settings: https://github.com/$REPO/settings"
echo ""
echo "Next steps:"
echo "1. Review branch protection settings"
echo "2. Add collaborators if needed"
echo "3. Update repository description/topics"
echo "4. Consider adding CI/CD workflows in .github/workflows/"
echo ""
