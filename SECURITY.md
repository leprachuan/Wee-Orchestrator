# Security Policy

## Reporting Security Vulnerabilities

If you discover a security vulnerability in n8n-copilot-shim, please **do not** open a public issue.

Instead, please email security concerns to the project maintainer with details about the vulnerability.

### What to Include

When reporting a vulnerability, please include:

1. **Description** - Clear description of the vulnerability
2. **Location** - File and line number if possible
3. **Impact** - What could an attacker do?
4. **Proof of Concept** - Steps to reproduce (if possible)
5. **Suggested Fix** - Any ideas for fixing it

### Response Timeline

- **Initial Response**: Within 48 hours
- **Assessment**: Within 1 week
- **Fix Development**: Depends on severity
- **Public Disclosure**: Coordinated with reporter

## Security Best Practices

### For Users

1. **Keep Dependencies Updated**
   - Update Python regularly
   - Keep installed CLI tools updated
   - Run `git pull` for latest fixes

2. **Protect Configuration Files**
   - Don't commit `agents.json` with sensitive data
   - Use `.gitignore` for local configurations
   - Keep session files secure

3. **Environment Variables**
   - Use environment variables for credentials
   - Never hardcode API keys or tokens
   - Use `GH_TOKEN` for GitHub authentication

4. **Session Management**
   - Clear old sessions if they contain sensitive data
   - Reset sessions between users
   - Use `/session reset` to clear state

### For Developers

1. **Input Validation**
   - Validate all command inputs
   - Check file paths are safe
   - Sanitize output before displaying

2. **No Hardcoded Credentials**
   - Use configuration files
   - Support environment variables
   - Never commit secrets

3. **Dependency Security**
   - Keep dependencies up to date
   - Review new dependencies
   - Check for known vulnerabilities

4. **Code Review**
   - All PRs must be reviewed
   - Security-sensitive code needs extra review
   - Use static analysis tools

## Supported Versions

| Version | Status | Support Until |
|---------|--------|---------------|
| 1.x     | Active | Current       |

## Known Issues

None currently reported.

## Security Considerations

### Session Handling

- Sessions are stored locally in user's home directory
- Session IDs are UUIDs (cryptographically random)
- Sessions can be reset or cleared with `/session reset`

### CLI Tool Security

This project wraps external CLI tools (Copilot, OpenCode, Claude). Security of those tools is the responsibility of their respective maintainers:

- [GitHub Copilot Security](https://github.com/github/copilot-cli/security)
- [OpenCode Security](https://github.com/opencode-ai/opencode/security)
- [Claude Code Security](https://www.anthropic.com/security)

### File Permissions

The script respects system file permissions:
- Session files use user's home directory permissions
- Configuration files require read access
- No world-readable credentials

### Network Security

- All communication with AI services uses their secure protocols
- This tool does not make direct network requests (delegates to CLI tools)
- No data is stored on remote servers by this tool

## Compliance

This project aims to follow:
- OWASP Top 10 security guidelines
- CWE/SANS Top 25 most dangerous weaknesses
- Best practices from security communities

## Updates

Security policy is subject to change. Check back regularly for updates.

---

**Last Updated**: December 2024

If you have security concerns or suggestions, please reach out to the maintainer.
