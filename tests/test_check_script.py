"""scripts/check.py：xlsx 校验按改动路径跳过。"""

from pathlib import Path

from scripts.check import ACTOR_DB_PATHS, ALWAYS, INFO_DB_PATHS, should_run_xlsx_check


def test_xlsx_check_runs_when_git_unavailable():
    assert should_run_xlsx_check(ACTOR_DB_PATHS, None) is True
    assert should_run_xlsx_check(INFO_DB_PATHS, None) is True


def test_xlsx_check_skips_when_unrelated_files_changed():
    changed = {"mdcx/core/scraper.py", "tests/test_base_web.py"}
    assert should_run_xlsx_check(ACTOR_DB_PATHS, changed) is False
    assert should_run_xlsx_check(INFO_DB_PATHS, changed) is False


def test_xlsx_check_runs_when_database_or_script_changed():
    assert should_run_xlsx_check(ACTOR_DB_PATHS, {"resources/userdata/actor_database.xlsx"}) is True
    assert should_run_xlsx_check(ACTOR_DB_PATHS, {"scripts/check_actor_db.py"}) is True
    assert should_run_xlsx_check(INFO_DB_PATHS, {"resources/userdata/info_database.xlsx"}) is True
    assert should_run_xlsx_check(INFO_DB_PATHS, {"scripts/check_info_db.py"}) is True


def test_xlsx_checks_are_independent():
    assert should_run_xlsx_check(ACTOR_DB_PATHS, {"resources/userdata/info_database.xlsx"}) is False
    assert should_run_xlsx_check(INFO_DB_PATHS, {"resources/userdata/actor_database.xlsx"}) is False


def test_always_commands_drop_xlsx_and_ui_layout():
    joined = [" ".join(cmd) for cmd in ALWAYS]
    assert any("check_thread_safety" in item for item in joined)
    assert all("check_actor_db" not in item for item in joined)
    assert all("check_info_db" not in item for item in joined)
    assert all("check_ui_layout" not in item for item in joined)


def test_ci_xlsx_checks_gated_and_ui_layout_removed():
    text = Path(".github/workflows/ci.yaml").read_text(encoding="utf-8")
    assert "actor_db_changed" in text
    assert "info_db_changed" in text
    assert "check_ui_layout" not in text
