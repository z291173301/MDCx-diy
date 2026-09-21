import subprocess
import sys

try:
    from scripts.install_git_hooks import install_hooks
except ModuleNotFoundError:
    from install_git_hooks import install_hooks

ALWAYS = [
    ["ruff", "format", "--check"],
    ["ruff", "check"],
    ["mypy", "mdcx/"],
    [
        sys.executable,
        "-m",
        "pytest",
        "tests/",
        "--tb=short",
        "-m",
        "not network",
        "-x",
    ],
    [sys.executable, "-m", "scripts.check_thread_safety"],
]

ACTOR_DB_PATHS = (
    "resources/userdata/actor_database.xlsx",
    "scripts/check_actor_db.py",
)
INFO_DB_PATHS = (
    "resources/userdata/info_database.xlsx",
    "scripts/check_info_db.py",
)


def _run_git(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode, result.stdout


def _merge_name_only(args: list[str], files: set[str]) -> bool:
    code, out = _run_git(args)
    if code != 0:
        return False
    files.update(line.strip().replace("\\", "/") for line in out.splitlines() if line.strip())
    return True


def collect_changed_paths() -> set[str] | None:
    """工作区 + 未推送提交的改动路径；无法判定时返回 None（xlsx 检查照跑）。"""
    files: set[str] = set()
    if not _merge_name_only(["diff", "--name-only", "HEAD"], files):
        return None

    code, out = _run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    base = out.strip() if code == 0 else ""
    if not base:
        code, out = _run_git(["rev-parse", "--verify", "origin/main"])
        base = "origin/main" if code == 0 else ""
    if base:
        _merge_name_only(["diff", "--name-only", f"{base}...HEAD"], files)
    elif files:
        return files
    else:
        return None
    return files


def should_run_xlsx_check(triggers: tuple[str, ...], changed: set[str] | None) -> bool:
    if changed is None:
        return True
    return any(path in changed for path in triggers)


def main() -> int:
    if "--skip-hook-install" not in sys.argv:
        install_hooks()

    commands = list(ALWAYS)
    changed = collect_changed_paths()

    if should_run_xlsx_check(ACTOR_DB_PATHS, changed):
        commands.append([sys.executable, "-m", "scripts.check_actor_db"])
        print("[check] 出厂演员库或校验脚本有改动，将运行 check_actor_db")
    else:
        print("[check] 跳过 check_actor_db（出厂演员库未改）")

    if should_run_xlsx_check(INFO_DB_PATHS, changed):
        commands.append([sys.executable, "-m", "scripts.check_info_db"])
        print("[check] 出厂信息库或校验脚本有改动，将运行 check_info_db")
    else:
        print("[check] 跳过 check_info_db（出厂信息库未改）")

    for command in commands:
        print(f"[check] running: {' '.join(command)}")
        result = subprocess.run(command)
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
