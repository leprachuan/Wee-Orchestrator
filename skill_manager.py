"""
skill_manager.py — Backend for the Skills panel.

Scans installed skill directories, manages origin metadata,
checks for updates against remote origins, and applies updates.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Configuration ─────────────────────────────────────────────────────────────

SKILL_DIRS: List[Dict[str, Any]] = [
    {"path": "/opt/foster-skills", "label": "foster-skills (private)"},
    {"path": "/opt/skills", "label": "skills (public)"},
    {"path": "/opt/.claude/skills", "label": ".claude/skills"},
    {"path": "/opt/.github/skills", "label": ".github/skills"},
    {"path": "/opt/pot-o-skills", "label": "pot-o-skills"},
]

ORIGINS_FILE = os.environ.get(
    "SKILL_ORIGINS_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill_origins.json"),
)


# ── Origin Metadata Persistence ───────────────────────────────────────────────


def _load_origins() -> Dict[str, Any]:
    """Load the central origin metadata store."""
    try:
        with open(ORIGINS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_origins(data: Dict[str, Any]) -> None:
    """Persist origin metadata atomically."""
    tmp = ORIGINS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, ORIGINS_FILE)


def get_origin(skill_key: str) -> Optional[Dict[str, Any]]:
    """Return origin metadata for a skill, or None."""
    return _load_origins().get(skill_key)


def set_origin(skill_key: str, origin: Dict[str, Any]) -> Dict[str, Any]:
    """Set or update origin metadata for a skill."""
    origins = _load_origins()
    existing = origins.get(skill_key, {})
    existing.update(origin)
    if "recorded_at" not in existing:
        existing["recorded_at"] = time.time()
    existing["updated_at"] = time.time()
    origins[skill_key] = existing
    _save_origins(origins)
    return existing


def delete_origin(skill_key: str) -> bool:
    """Remove origin metadata for a skill."""
    origins = _load_origins()
    if skill_key in origins:
        del origins[skill_key]
        _save_origins(origins)
        return True
    return False


# ── Skill Scanning ────────────────────────────────────────────────────────────


def _read_skill_metadata(skill_dir: str) -> Dict[str, Any]:
    """Read skill_metadata.json if present."""
    p = os.path.join(skill_dir, "skill_metadata.json")
    try:
        with open(p) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _read_skill_md(skill_dir: str) -> Dict[str, str]:
    """Parse SKILL.md YAML frontmatter for name/description."""
    p = os.path.join(skill_dir, "SKILL.md")
    try:
        with open(p) as f:
            content = f.read()
    except FileNotFoundError:
        return {}
    # Extract YAML frontmatter between --- markers
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    result = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            result[key.strip()] = val.strip()
    return result


def _dir_checksum(skill_dir: str) -> str:
    """Compute a quick checksum of a skill directory for change detection."""
    h = hashlib.sha256()
    for root, _dirs, files in sorted(os.walk(skill_dir)):
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            try:
                st = os.stat(fp)
                h.update(f"{fp}:{st.st_size}:{st.st_mtime_ns}".encode())
            except OSError:
                pass
    return h.hexdigest()[:16]


def _is_skill_dir(path: str) -> bool:
    """Check if a directory looks like a skill (has SKILL.md or skill_metadata.json or known structure)."""
    if os.path.isfile(os.path.join(path, "skill_metadata.json")):
        return True
    if os.path.isfile(os.path.join(path, "SKILL.md")):
        return True
    # Check for runtime subdirs
    for sub in ("claude", "copilot", "gemini"):
        if os.path.isdir(os.path.join(path, sub)):
            return True
    # Check for README.md with skill-like content
    readme = os.path.join(path, "README.md")
    if os.path.isfile(readme):
        try:
            with open(readme) as f:
                head = f.read(500)
            if "skill" in head.lower():
                return True
        except OSError:
            pass
    return False


def scan_skills() -> List[Dict[str, Any]]:
    """Scan all skill directories and return a list of skill descriptors."""
    origins = _load_origins()
    results = []

    for sd in SKILL_DIRS:
        base_path = sd["path"]
        label = sd["label"]

        if not os.path.isdir(base_path):
            continue

        for entry in sorted(os.listdir(base_path)):
            if ".backup." in entry:
                continue
            full = os.path.join(base_path, entry)
            if not os.path.isdir(full):
                continue
            if entry.startswith(".") or entry == "__pycache__":
                continue

            # Handle nested skill repos (e.g., anthropic-skills/skills/*)
            nested_skills_dir = os.path.join(full, "skills")
            if os.path.isdir(nested_skills_dir):
                for nested_entry in sorted(os.listdir(nested_skills_dir)):
                    if ".backup." in nested_entry:
                        continue
                    nested_full = os.path.join(nested_skills_dir, nested_entry)
                    if os.path.isdir(nested_full) and _is_skill_dir(nested_full):
                        skill = _build_skill_descriptor(
                            nested_full,
                            nested_entry,
                            f"{label}/{entry}",
                            origins,
                        )
                        results.append(skill)
                continue

            if not _is_skill_dir(full):
                continue

            skill = _build_skill_descriptor(full, entry, label, origins)
            results.append(skill)

    return results


def _build_skill_descriptor(
    skill_dir: str, name: str, source_label: str, origins: Dict
) -> Dict[str, Any]:
    """Build a unified skill descriptor dict."""
    meta = _read_skill_metadata(skill_dir)
    skill_md = _read_skill_md(skill_dir)

    # Merge name/description: metadata wins over SKILL.md
    display_name = meta.get("name") or skill_md.get("name") or name
    description = meta.get("description") or skill_md.get("description") or ""
    version = meta.get("version", "")
    author = meta.get("author", "")
    category = meta.get("category", meta.get("type", ""))

    # Derive a unique key for origin tracking
    skill_key = (
        f"{os.path.basename(os.path.dirname(skill_dir))}/{name}"
        if "skills/" in skill_dir
        else f"{source_label.split('/')[0].split(' ')[0]}/{name}"
    )

    origin = origins.get(skill_key, {})

    return {
        "name": display_name,
        "dir_name": name,
        "skill_key": skill_key,
        "path": skill_dir,
        "source_label": source_label,
        "description": description,
        "version": version,
        "author": author,
        "category": category,
        "has_metadata": bool(meta),
        "has_skill_md": os.path.isfile(os.path.join(skill_dir, "SKILL.md")),
        "checksum": _dir_checksum(skill_dir),
        "origin": origin if origin else None,
        "runtimes": (
            (
                list(meta["runtimes"].keys())
                if isinstance(meta.get("runtimes"), dict)
                else list(meta.get("runtimes", []))
            )
            if meta.get("runtimes")
            else [
                d
                for d in ("claude", "copilot", "gemini")
                if os.path.isdir(os.path.join(skill_dir, d))
            ]
        ),
    }


def get_skill(skill_key: str) -> Optional[Dict[str, Any]]:
    """Find a single skill by its key."""
    for s in scan_skills():
        if s["skill_key"] == skill_key:
            return s
    return None


# ── Update Checking ───────────────────────────────────────────────────────────


def check_update_git(skill_key: str) -> Dict[str, Any]:
    """Check if a git-sourced skill has updates available.

    Returns: {available: bool, current_hash: str, remote_hash: str, summary: str}
    """
    origin = get_origin(skill_key)
    if not origin:
        return {"available": False, "error": "No origin metadata recorded"}

    origin_type = origin.get("origin_type", "")
    if origin_type != "git_repo":
        return {
            "available": False,
            "error": f"Origin type '{origin_type}' does not support git update checks",
        }

    origin_url = origin.get("origin_url", "")
    origin_path = origin.get("origin_path", "")
    if not origin_url:
        return {"available": False, "error": "No origin URL"}

    skill = get_skill(skill_key)
    if not skill:
        return {"available": False, "error": "Skill not found locally"}

    local_path = skill["path"]
    local_checksum = skill["checksum"]

    try:
        # Clone the remote repo to a temp dir (shallow)
        with tempfile.TemporaryDirectory(prefix="skill-update-") as tmpdir:
            repo_dir = os.path.join(tmpdir, "repo")
            result = subprocess.run(
                ["git", "clone", "--depth=1", "--single-branch", origin_url, repo_dir],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                return {
                    "available": False,
                    "error": f"Git clone failed: {result.stderr[:200]}",
                }

            # Find the skill folder in the cloned repo
            remote_skill_dir = (
                os.path.join(repo_dir, origin_path) if origin_path else repo_dir
            )
            if not os.path.isdir(remote_skill_dir):
                return {
                    "available": False,
                    "error": f"Origin path '{origin_path}' not found in repo",
                }

            remote_checksum = _dir_checksum(remote_skill_dir)

            available = remote_checksum != local_checksum
            summary = "Updates available" if available else "Up to date"

            # Get file-level diff summary
            diff_files = []
            if available:
                diff_files = _compare_dirs(local_path, remote_skill_dir)

            # Update origin metadata with check results
            set_origin(
                skill_key,
                {
                    "last_checked": time.time(),
                    "update_available": available,
                    "remote_checksum": remote_checksum,
                    "diff_summary": diff_files[:20] if diff_files else [],
                },
            )

            return {
                "available": available,
                "local_checksum": local_checksum,
                "remote_checksum": remote_checksum,
                "summary": summary,
                "diff_files": diff_files[:20],
            }

    except subprocess.TimeoutExpired:
        return {"available": False, "error": "Git clone timed out"}
    except Exception as e:
        return {"available": False, "error": str(e)[:200]}


def _compare_dirs(local_dir: str, remote_dir: str) -> List[str]:
    """Compare two directories and return list of changed files."""
    changes = []
    local_files = set()
    remote_files = set()

    for root, _, files in os.walk(local_dir):
        for fn in files:
            rel = os.path.relpath(os.path.join(root, fn), local_dir)
            local_files.add(rel)

    for root, _, files in os.walk(remote_dir):
        for fn in files:
            rel = os.path.relpath(os.path.join(root, fn), remote_dir)
            remote_files.add(rel)

    for f in sorted(remote_files - local_files):
        changes.append(f"+ {f}")
    for f in sorted(local_files - remote_files):
        changes.append(f"- {f}")
    for f in sorted(local_files & remote_files):
        lp = os.path.join(local_dir, f)
        rp = os.path.join(remote_dir, f)
        try:
            if os.path.getsize(lp) != os.path.getsize(rp):
                changes.append(f"M {f}")
            else:
                with open(lp, "rb") as a, open(rp, "rb") as b:
                    if a.read() != b.read():
                        changes.append(f"M {f}")
        except OSError:
            changes.append(f"? {f}")

    return changes


def apply_update_git(skill_key: str) -> Dict[str, Any]:
    """Pull updates from the git origin and merge into the local skill.

    Returns: {success: bool, message: str, files_changed: list}
    """
    origin = get_origin(skill_key)
    if not origin:
        return {"success": False, "message": "No origin metadata recorded"}

    origin_type = origin.get("origin_type", "")
    if origin_type != "git_repo":
        return {
            "success": False,
            "message": f"Origin type '{origin_type}' not supported for auto-update",
        }

    origin_url = origin.get("origin_url", "")
    origin_path = origin.get("origin_path", "")
    if not origin_url:
        return {"success": False, "message": "No origin URL"}

    skill = get_skill(skill_key)
    if not skill:
        return {"success": False, "message": "Skill not found locally"}

    local_path = skill["path"]

    try:
        with tempfile.TemporaryDirectory(prefix="skill-update-") as tmpdir:
            repo_dir = os.path.join(tmpdir, "repo")
            result = subprocess.run(
                ["git", "clone", "--depth=1", "--single-branch", origin_url, repo_dir],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                return {
                    "success": False,
                    "message": f"Git clone failed: {result.stderr[:300]}",
                }

            remote_skill_dir = (
                os.path.join(repo_dir, origin_path) if origin_path else repo_dir
            )
            if not os.path.isdir(remote_skill_dir):
                return {
                    "success": False,
                    "message": f"Origin path '{origin_path}' not found in repo",
                }

            # Backup local skill
            backup_dir = local_path + f".backup.{int(time.time())}"
            shutil.copytree(local_path, backup_dir)

            # Merge: copy remote files over local, preserving local-only files
            files_changed = []
            for root, _, files in os.walk(remote_skill_dir):
                for fn in files:
                    if fn.startswith(".git"):
                        continue
                    rel = os.path.relpath(os.path.join(root, fn), remote_skill_dir)
                    src = os.path.join(remote_skill_dir, rel)
                    dst = os.path.join(local_path, rel)

                    # Check if file differs
                    needs_copy = True
                    if os.path.isfile(dst):
                        with open(src, "rb") as a, open(dst, "rb") as b:
                            if a.read() == b.read():
                                needs_copy = False

                    if needs_copy:
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                        files_changed.append(rel)

            # Preserve skill_origin.json in the local dir if we had one there
            # (We use central storage, so no action needed)

            # Update origin metadata
            set_origin(
                skill_key,
                {
                    "last_updated": time.time(),
                    "update_available": False,
                    "last_checked": time.time(),
                    "remote_checksum": _dir_checksum(remote_skill_dir),
                },
            )

            return {
                "success": True,
                "message": f"Updated {len(files_changed)} file(s)",
                "files_changed": files_changed,
                "backup": backup_dir,
            }

    except Exception as e:
        return {"success": False, "message": str(e)[:300]}


def check_update_website(skill_key: str) -> Dict[str, Any]:
    """For website-sourced skills, we can only flag that manual check is needed."""
    origin = get_origin(skill_key)
    if not origin:
        return {"available": False, "error": "No origin metadata"}

    return {
        "available": False,
        "summary": "Website-sourced skills require manual update checks",
        "origin_url": origin.get("origin_url", ""),
        "note": "Visit the origin URL to check for updates, then re-copy the skill.",
    }


# ── Unified Dispatch ──────────────────────────────────────────────────────────


def check_update(skill_key: str) -> Dict[str, Any]:
    """Check for updates based on origin type."""
    origin = get_origin(skill_key)
    if not origin:
        return {
            "available": False,
            "error": "No origin metadata recorded for this skill",
        }

    origin_type = origin.get("origin_type", "unknown")
    if origin_type == "git_repo":
        return check_update_git(skill_key)
    elif origin_type == "website":
        return check_update_website(skill_key)
    else:
        return {"available": False, "error": f"Unknown origin type: {origin_type}"}


def apply_update(skill_key: str) -> Dict[str, Any]:
    """Apply updates based on origin type."""
    origin = get_origin(skill_key)
    if not origin:
        return {"success": False, "message": "No origin metadata recorded"}

    origin_type = origin.get("origin_type", "unknown")
    if origin_type == "git_repo":
        return apply_update_git(skill_key)
    elif origin_type == "website":
        return {
            "success": False,
            "message": "Website-sourced skills must be updated manually. "
            f"Visit: {origin.get('origin_url', 'unknown')}",
        }
    else:
        return {"success": False, "message": f"Unknown origin type: {origin_type}"}


def scan_agent_skills(agent_path: str) -> List[Dict[str, Any]]:
    """Scan skills from a specific agent's .github/skills/ and .claude/skills/ dirs.

    Args:
        agent_path: The filesystem path of the agent (e.g., /opt/fosterbot-home).

    Returns:
        List of skill descriptors found under that agent's skills directories.
    """
    origins = _load_origins()
    results = []

    skill_dirs = [
        {
            "path": os.path.join(agent_path, ".github", "skills"),
            "label": f"{os.path.basename(agent_path)}/.github/skills",
        },
        {
            "path": os.path.join(agent_path, ".claude", "skills"),
            "label": f"{os.path.basename(agent_path)}/.claude/skills",
        },
    ]

    for sd in skill_dirs:
        base = sd["path"]
        label = sd["label"]

        if not os.path.isdir(base):
            continue

        for entry in sorted(os.listdir(base)):
            if ".backup." in entry:
                continue
            full = os.path.join(base, entry)
            if not os.path.isdir(full):
                continue
            if entry.startswith(".") or entry == "__pycache__":
                continue

            # Handle nested skill repos
            nested_skills_dir = os.path.join(full, "skills")
            if os.path.isdir(nested_skills_dir):
                for nested_entry in sorted(os.listdir(nested_skills_dir)):
                    if ".backup." in nested_entry:
                        continue
                    nested_full = os.path.join(nested_skills_dir, nested_entry)
                    if os.path.isdir(nested_full) and _is_skill_dir(nested_full):
                        skill = _build_skill_descriptor(
                            nested_full, nested_entry, f"{label}/{entry}", origins
                        )
                        results.append(skill)
                continue

            if not _is_skill_dir(full):
                continue

            skill = _build_skill_descriptor(full, entry, label, origins)
            results.append(skill)

    return results
