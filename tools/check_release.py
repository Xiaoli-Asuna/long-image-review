"""Check publishable source paths and high-confidence accidental secrets, offline."""
import argparse
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", "Data", "ServerData", "TestResults", "Samples", "runtime", "vendor",
            ".venv", "venv", "dist", "build", ".download-cache", "__pycache__"}
FORBIDDEN = {"ServerConfig.json", "startup-error.txt", ".env", "instance.json", "workspace.json"}
SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
MACHINE_PATH = re.compile(r"(?<![A-Za-z])[A-Z]:[\\/](?!Windows\b)", re.I)


def check(paths):
    errors = []
    for path in paths:
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED for part in relative.parts) or path.name in FORBIDDEN or path.suffix.lower() in {".sqlite3", ".db", ".pem", ".key", ".pfx", ".p12", ".bin", ".log", ".pyc"}:
            errors.append(f"Private/generated path: {relative}")
            continue
        try:
            content = path.read_text("utf-8-sig")
        except UnicodeError:
            errors.append(f"Unexpected binary in source: {relative}")
            continue
        if SECRET.search(content):
            errors.append(f"Possible credential: {relative}")
        if MACHINE_PATH.search(content):
            errors.append(f"Machine-specific path or customer identifier: {relative}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracked", action="store_true", help="Check all git-tracked files, including forbidden paths")
    args = parser.parse_args()
    if args.tracked:
        raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        paths = [ROOT / name.decode("utf-8") for name in raw.split(b"\0") if name]
    else:
        paths = [p for p in ROOT.rglob("*") if p.is_file() and not any(part in EXCLUDED for part in p.relative_to(ROOT).parts)]
    errors = check(paths)
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Publishable source check passed: {len(paths)} files. Manual review is still required.")


if __name__ == "__main__":
    main()
