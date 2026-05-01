import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_transcript_path_is_gitignored():
    gitignore_path = Path(__file__).parent.parent / ".gitignore"
    with open(gitignore_path, "r") as f:
        content = f.read()
    assert "logs/transcripts/" in content


def test_transcript_write_not_tracked():
    repo_root = Path(__file__).parent.parent
    transcript_dir = repo_root / "logs" / "transcripts" / "test_session_123"
    transcript_dir.mkdir(parents=True, exist_ok=True)

    transcript_file = transcript_dir / "transcript_20260423T034153Z.json"
    test_data = [{"role": "user", "content": "test"}]
    with open(transcript_file, "w") as f:
        json.dump(test_data, f)

    assert transcript_file.exists()

    result = subprocess.run(
        ["git", "check-ignore", "-v", str(transcript_file)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"File should be gitignored: {result.stdout}"

    import shutil

    if transcript_dir.exists():
        shutil.rmtree(transcript_dir)


if __name__ == "__main__":
    test_transcript_path_is_gitignored()
    print("✓ logs/transcripts/ is in .gitignore")
    test_transcript_write_not_tracked()
    print("✓ Transcript files are properly gitignored")
