# ⚠️ PRODUCTION SAFETY CONSTRAINT

**This is a LIVE production environment serving real users.**

## Golden Rule
**NEVER edit files directly in this folder.** 

All code and configuration changes MUST follow this workflow:

### Required Deployment Workflow
1. **Develop in `/opt/n8n-copilot-shim-dev`** (dev branch)
2. **Test thoroughly** in dev environment
3. **Create PR** (dev → main branch on GitHub)
4. **Code review & approval**
5. **Merge to main** (on GitHub)
6. **Pull from main** in this folder only:
   ```bash
   cd /opt/n8n-copilot-shim
   git fetch origin
   git checkout main
   git pull origin main
   ```
7. **Restart services** (one by one, with verification)

### What This Means
✅ **DO:**
- Edit dev folder only (`/opt/n8n-copilot-shim-dev`)
- Test changes in dev environment
- Create PRs for code review
- Pull main branch after merge approval

❌ **DO NOT:**
- Edit code files directly
- Modify `.env` files manually (unless it's a temporary override, then document it)
- Commit directly to main from this folder
- Restart services for quick testing
- Make config changes without dev testing first

### Exception for Config Changes
Even environment variable changes (like `MAX_SESSIONS` in `.env`) should be:
1. Made in dev first
2. Tested in dev
3. Committed with code changes to main
4. Pulled and deployed through normal workflow

This ensures production stability and auditability.
