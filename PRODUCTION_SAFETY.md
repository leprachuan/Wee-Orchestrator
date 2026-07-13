# ⚠️ PRODUCTION SAFETY CONSTRAINT

**This is a LIVE production environment serving real users.**

## Golden Rule
**NEVER edit files directly in this folder.** 

All code and configuration changes MUST follow this workflow:

### Required Deployment Workflow
1. **Develop and test in the dev environment**. The source may be on any Git
   branch, including `main`; branch choice is not a deployment policy.
2. **Test thoroughly** in dev environment.
3. **Commit and push** the tested revision. A pull request is optional when it
   is useful for collaboration or review; direct commits to `main` are allowed.
4. **Create and validate a versioned release** for the artifact being
   distributed. Releases, not branch names, are the supported deliverables.
5. **Deploy only the approved release revision** in this folder:
   ```bash
   cd /opt/n8n-copilot-shim
   git fetch origin --tags
   git checkout api-vMAJOR.MINOR.PATCH
   ```
6. **Restart services** (one by one, with verification).

### What This Means
✅ **DO:**
- Edit dev folder only (`/opt/n8n-copilot-shim-dev`)
- Test changes in dev environment
- Use a branch or a direct `main` commit as appropriate
- Create a tested, versioned release before giving software to users
- Pull the approved release revision before deployment

❌ **DO NOT:**
- Edit code files directly
- Modify `.env` files manually (unless it's a temporary override, then document it)
- Restart services for quick testing
- Make config changes without dev testing first

### Exception for Config Changes
Even environment variable changes (like `MAX_SESSIONS` in `.env`) should be:
1. Made in dev first
2. Tested in dev
3. Committed with code changes to main
4. Pulled and deployed through normal workflow

This ensures production stability and auditability.
