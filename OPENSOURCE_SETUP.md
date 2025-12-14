# Open Source Setup Instructions

This document explains how to complete the final setup of the n8n-copilot-shim repository for public open source collaboration.

## Current Status

✅ **Completed:**
- Repository codebase is clean and tested
- Comprehensive test suite (31 tests, all passing)
- Full documentation (README, contributing guides, security policy)
- GitHub Actions CI/CD workflow configured
- Issue and PR templates created
- CODEOWNERS file set up for code review
- All sensitive data removed

⏳ **Still Needed:**
- Authenticate with GitHub CLI
- Make repository public
- Enable branch protection
- Configure merge request requirements

## Step-by-Step Setup

### Step 1: Install GitHub CLI (if not already installed)

```bash
# On macOS
brew install gh

# On Linux (Ubuntu/Debian)
sudo apt-get install gh

# On Windows
choco install gh
```

Verify installation:
```bash
gh --version
```

### Step 2: Authenticate with GitHub

Choose one of these methods:

**Method A: Web-based Authentication (Recommended)**
```bash
gh auth login -p https -w --skip-ssh-key
```
This will open your browser for you to authorize GitHub access.

**Method B: Personal Access Token**

1. Go to: https://github.com/settings/tokens
2. Click "Generate new token" → "Generate new token (classic)"
3. Name it: `gh-cli-setup`
4. Select scopes:
   - ✓ `repo` (full control of repositories)
   - ✓ `admin:org_hook` (webhook management)
   - ✓ `admin:repo_hook` (repository webhooks)
5. Generate and copy the token
6. Run:
   ```bash
   echo "your-github-token" | gh auth login --with-token
   ```

Verify authentication:
```bash
gh auth status
```

### Step 3: Run the Setup Script

From the repository root directory:

```bash
./setup-opensource.sh
```

This script will:
- ✅ Make the repository public
- ✅ Enable branch protection on `main`
- ✅ Require PR approval before merging
- ✅ Add CODEOWNERS for code review
- ✅ Create issue templates
- ✅ Create PR template

**Expected Output:**
```
╔════════════════════════════════════════════════════════════════╗
║     Open Source Repository Setup Script                        ║
╚════════════════════════════════════════════════════════════════╝

✓ GitHub CLI authenticated
Repository: leprachuan/n8n-copilot-shim

Step 1/5: Making repository public...
✓ Repository is now public

Step 2/5: Setting up branch protection rules for 'main'...
✓ Branch protection enabled

Step 3/5: Creating CODEOWNERS file...
✓ CODEOWNERS file created

Step 4/5: Creating issue templates...
✓ Issue templates created

Step 5/5: Creating pull request template...
✓ Pull request template created

╔════════════════════════════════════════════════════════════════╗
║               ✅ SETUP COMPLETE                               ║
╚════════════════════════════════════════════════════════════════╝
```

### Step 4: Verify Repository Configuration

Visit your repository settings: https://github.com/leprachuan/n8n-copilot-shim/settings

**Check these settings:**

1. **General**
   - [ ] Repository is set to Public
   - [ ] License is set to Apache 2.0

2. **Branches → Branch protection rules**
   - [ ] `main` branch has protection enabled
   - [ ] "Require a pull request before merging" is enabled
   - [ ] "Require approvals" is set to 1
   - [ ] "Require code owner review" is enabled
   - [ ] "Dismiss stale pull request approvals" is enabled
   - [ ] "Require status checks to pass" is enabled
   - [ ] "Require branches to be up to date before merging" is enabled

3. **Code security and analysis**
   - [ ] Dependabot alerts enabled (optional)
   - [ ] Dependabot security updates enabled (optional)

4. **Visibility**
   - [ ] Repository is Public

## What This Accomplishes

### For Public Users

✅ **Transparency**: Repository is publicly visible and auditable
✅ **Trust**: Security policy and processes documented
✅ **Clarity**: Contributing guidelines explain how to participate
✅ **Tests**: Automated CI/CD ensures code quality
✅ **Quality**: All PRs require approval before merging

### For Contributors

✅ **Clear Process**: Contributing.md explains the workflow
✅ **Guidelines**: Code style and testing requirements defined
✅ **Security**: Security.md explains how to report issues safely
✅ **Community**: Issue/PR templates provide structure
✅ **Approval**: All changes require owner approval

### For the Project

✅ **Control**: Owner maintains decision authority via approvals
✅ **Quality**: All changes tested and reviewed
✅ **Documentation**: CI/CD pipeline configured
✅ **Governance**: CODEOWNERS file manages reviews
✅ **Safety**: Security policy in place

## Continuous Integration

The GitHub Actions workflow (`.github/workflows/tests.yml`) will automatically:

- Run tests on every push to `main` and PRs
- Test against Python 3.8, 3.9, 3.10, 3.11, 3.12
- Block merging if tests fail
- Provide feedback to PR authors

## Managing Pull Requests

### Workflow for Contributors

1. Fork the repository
2. Create a branch (`git checkout -b feature/name`)
3. Make changes and commit
4. Push to their fork
5. Open a Pull Request
6. **PR requires your approval to merge** ✓

### Workflow for You (Owner)

1. Review PR code changes
2. Check that tests pass (automatic)
3. Review related issues
4. Add comments/suggestions
5. Approve the PR (`gh pr review --approve`)
6. Merge when ready (`gh pr merge <number> --squash`)

### Quick Commands for PR Management

```bash
# List open PRs
gh pr list

# Review a PR
gh pr review <number> --approve
gh pr review <number> --request-changes

# Merge a PR
gh pr merge <number> --squash

# Check PR status
gh pr status
```

## Updating Repository Description

Update your repository description on GitHub:

1. Go to: https://github.com/leprachuan/n8n-copilot-shim
2. Click the settings gear icon (top right)
3. Update:
   - **Description**: "A unified AI agent manager that bridges N8N workflows with multiple AI CLI tools"
   - **Website**: (optional)
   - **Topics**: Add relevant topics like:
     - `n8n`
     - `ai`
     - `cli`
     - `github-copilot`
     - `opencode`
     - `claude`
     - `open-source`

## Optional Enhancements

These are optional but recommended for growing projects:

1. **Add Badge to README**
   ```markdown
   ![Tests](https://github.com/leprachuan/n8n-copilot-shim/actions/workflows/tests.yml/badge.svg)
   ```

2. **Set up Code of Conduct**
   ```bash
   # Create from GitHub's template
   gh repo create-codeofconduct --template contributor-covenant
   ```

3. **Enable Discussions**
   - Repository Settings → Discussions → Enable

4. **Create Release Tags**
   ```bash
   git tag -a v1.0.0 -m "Initial release"
   git push origin v1.0.0
   ```

5. **Set up Codecov Integration**
   - Visit: https://codecov.io
   - Connect your GitHub repository
   - Add coverage badge to README

## Troubleshooting

### Script fails with "Not authenticated"

```bash
# Re-authenticate
gh auth login -p https -w --skip-ssh-key
```

### "Permission denied" when running script

```bash
# Make script executable
chmod +x setup-opensource.sh
./setup-opensource.sh
```

### CODEOWNERS already exists error

This is fine - it means the file was already created. The script will not overwrite it.

### Branch protection won't enable

- You may need admin access to the repository
- Check GitHub settings for any organization policies
- Ensure `main` branch exists and has commits

## Getting Help

- **Repository Issues**: https://github.com/leprachuan/n8n-copilot-shim/issues
- **GitHub Docs**: https://docs.github.com/
- **GitHub CLI Help**: `gh help <command>`

## Success Checklist

After completing setup, verify:

- [ ] Repository is PUBLIC
- [ ] Branch `main` is PROTECTED
- [ ] Pull requests REQUIRE APPROVAL
- [ ] CI/CD tests run automatically
- [ ] Issue templates are available
- [ ] PR template is available
- [ ] CONTRIBUTING.md is in place
- [ ] SECURITY.md is in place
- [ ] All tests pass
- [ ] README is comprehensive

---

**You're all set!** Your repository is now properly configured for open source collaboration.

For questions or issues with the setup, refer to the [GitHub documentation](https://docs.github.com/) or open an issue in the repository.
