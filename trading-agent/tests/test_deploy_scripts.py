"""Tests for the deploy scripts.

Shell is where this project's most expensive mistakes can hide: these scripts
swap the running code and push to a public branch, with nobody watching. The
checks here are the ones that stay true without a VM - syntax, the guards that
must be present, and the dispatcher, which is small enough to run for real.

The behavioural paths that need a git remote and a venv (test gate, rollback,
quarantine, health exit 2) are exercised against a sandbox VM rather than here;
see DEPLOY.md.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
SCRIPTS = ["pull-deploy.sh", "publish-status.sh"]
EXECUTABLES = SCRIPTS + ["tagent-run", "tagent-run-script", "bootstrap.sh"]


@pytest.mark.parametrize("name", EXECUTABLES)
def test_script_parses(name):
    assert subprocess.run(["bash", "-n", DEPLOY / name]).returncode == 0


@pytest.mark.parametrize("name", EXECUTABLES)
def test_script_is_executable(name):
    assert (DEPLOY / name).stat().st_mode & stat.S_IXUSR


def code_lines(name):
    """Script lines with whole-line comments dropped.

    These scripts explain the `set +e` trap subtlety in prose, so a raw string
    search would match the explanation rather than the code.
    """
    return [
        l for l in (DEPLOY / name).read_text().splitlines()
        if not l.lstrip().startswith("#")
    ]


@pytest.mark.parametrize("name", EXECUTABLES)
def test_scripts_never_suppress_errors_with_set_plus_e(name):
    """`set +e` does NOT stop an ERR trap firing, so it cannot be used to run a
    command that is expected to fail. Every such call here uses `|| rc=$?`."""
    offenders = [l for l in code_lines(name) if "set +e" in l]
    assert offenders == []


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_fail_loudly(name):
    body = (DEPLOY / name).read_text()
    assert "set -Eeuo pipefail" in body
    assert "trap on_error ERR" in body


# ------------------------------------------------------------ pull-deploy ---

def test_pull_deploy_refuses_inside_market_hours():
    body = (DEPLOY / "pull-deploy.sh").read_text()
    # zoneinfo, not a hardcoded offset: the rule has to survive DST.
    assert 'ZoneInfo("America/New_York")' in body
    assert "now.weekday() < 5 and 9 <= now.hour < 17" in body


def test_pull_deploy_checks_the_kill_switch_before_switching():
    body = (DEPLOY / "pull-deploy.sh").read_text()
    kill = body.index("kill switch is engaged")
    switch = body.index('switch_to "$INCOMING"')
    assert kill < switch


def test_pull_deploy_runs_tests_and_doctor_before_switching():
    body = (DEPLOY / "pull-deploy.sh").read_text()
    switch = body.index('switch_to "$INCOMING"')
    assert body.index("pytest -q") < switch
    assert body.index('doctor --equity "$equity"') < switch


def test_pull_deploy_treats_health_exit_2_as_credentials_not_code():
    body = (DEPLOY / "pull-deploy.sh").read_text()
    two = body.index("health_rc -eq 2")
    rollback = body.index('rolling back to')
    assert two < rollback, "the OAuth case must be handled before the rollback"
    assert "Keeping ${INCOMING:0:12}" in body


def test_pull_deploy_never_writes_the_config_or_the_env_file():
    """The two files that must stay on the box and out of git."""
    body = (DEPLOY / "pull-deploy.sh").read_text()
    for line in body.splitlines():
        code = line.split("#", 1)[0]
        for target in ('"$CONFIG"', '"$ENV_FILE"'):
            assert f"> {target}" not in code, line
            assert f">> {target}" not in code, line
            assert f"tee {target}" not in code, line
            assert f"rm {target}" not in code, line
    # And it proves it afterwards rather than merely intending it.
    assert 'config_before=$(sha256sum "$CONFIG"' in body
    assert 'env_before=$(sha256sum "$ENV_FILE"' in body


def test_pull_deploy_runs_from_a_private_copy_of_itself():
    """A `git checkout` rewrites this file mid-run. Bash reads scripts by byte
    offset and silently truncates when that happens - it would switch commits
    and then skip the health check while exiting 0."""
    body = (DEPLOY / "pull-deploy.sh").read_text()
    assert 'TAGENT_DEPLOY_REEXEC="$self" exec bash "$self"' in body
    reexec = body.index("TAGENT_DEPLOY_REEXEC")
    assert reexec < body.index("git -C \"$TAGENT_HOME\" fetch")


def test_pull_deploy_quarantines_so_a_bad_commit_is_not_retried_nightly():
    body = (DEPLOY / "pull-deploy.sh").read_text()
    assert 'echo "$INCOMING" >> "$QUARANTINE"' in body
    assert 'grep -qx "$INCOMING" "$QUARANTINE"' in body
    # ...but only once the previous commit has proved the box itself is fine.
    assert "prev_rc -eq 0 || $prev_rc -eq 2" in body


# --------------------------------------------------------- publish-status ---

def test_publish_status_refuses_financials_on_a_public_repo():
    body = (DEPLOY / "publish-status.sh").read_text()
    assert 'INCLUDE_FINANCIALS -eq 1 && "${TAGENT_STATUS_REPO_IS_PRIVATE:-0}" != "1"' in body


def test_publish_status_validates_before_it_pushes():
    body = (DEPLOY / "publish-status.sh").read_text()
    assert body.index("-m tagent.telemetry --validate") < body.index("git -C \"$WORK\" push")


def test_publish_status_keeps_the_token_off_the_command_line():
    """A command line is world-readable through /proc; an env var is not."""
    body = (DEPLOY / "publish-status.sh").read_text()
    assert "GIT_ASKPASS" in body
    assert "x-access-token:" not in body, "no token in the remote URL"
    assert "$TAGENT_STATUS_TOKEN@" not in body


def test_publish_status_writes_askpass_outside_the_work_tree():
    """Anything inside $WORK gets committed by `git add -A`."""
    body = (DEPLOY / "publish-status.sh").read_text()
    assert 'ASKPASS="$STAGE/askpass"' in body
    assert 'WORK="$STAGE/repo"' in body


# ----------------------------------------------------------------- crontab ---

def test_crontab_schedules_the_loop_after_the_close():
    lines = (DEPLOY / "crontab.example").read_text().splitlines()
    entries = [l for l in lines if l and not l.startswith("#")]
    assert "CRON_TZ=America/New_York" in entries, "ET, so DST is cron's problem"

    def entry(script):
        found = [l for l in entries if script in l]
        assert len(found) == 1, f"expected exactly one {script} line, got {found}"
        return found[0].split()

    publish = entry("publish-status")
    assert publish[:5] == ["45", "17", "*", "*", "1-5"]
    deploy = entry("pull-deploy")
    assert deploy[:5] == ["30", "18", "*", "*", "1-5"]

    # Both go through the dispatcher, and both after the 17:30 review.
    for fields in (publish, deploy):
        assert fields[5].endswith("tagent-run-script")
    review = entry("tagent-run review")
    assert (int(review[1]), int(review[0])) < (int(publish[1]), int(publish[0]))
    assert (int(publish[1]), int(publish[0])) < (int(deploy[1]), int(deploy[0]))


def test_deploy_runs_outside_the_hours_pull_deploy_itself_refuses():
    """The cron time and the script's own guard have to agree, or the job
    silently no-ops every night."""
    deploy = [
        l.split() for l in (DEPLOY / "crontab.example").read_text().splitlines()
        if l and not l.startswith("#") and "pull-deploy" in l
    ][0]
    minute, hour = int(deploy[0]), int(deploy[1])
    assert not (9 <= hour < 17), f"cron fires at {hour}:{minute:02d} ET, inside the refusal window"


def test_bootstrap_installs_the_dispatcher():
    body = (DEPLOY / "bootstrap.sh").read_text()
    assert "cp \"$APP_DIR/deploy/tagent-run-script\" /usr/local/bin/tagent-run-script" in body


# -------------------------------------------------------------- dispatcher ---

def dispatch(tmp_path, *args, env_file=True, scripts=("demo",)):
    home = tmp_path / "opt"
    deploy_dir = home / "trading-agent" / "deploy"
    deploy_dir.mkdir(parents=True)
    for name in scripts:
        target = deploy_dir / f"{name}.sh"
        target.write_text('#!/usr/bin/env bash\necho "ran $0 secret=$DEMO_SECRET $*"\n')
        target.chmod(0o755)

    env_path = tmp_path / "tagent.env"
    if env_file:
        env_path.write_text("DEMO_SECRET=from-env-file\n")

    return subprocess.run(
        ["bash", str(DEPLOY / "tagent-run-script"), *args],
        capture_output=True, text=True,
        env={**os.environ, "TAGENT_HOME": str(home),
             "TAGENT_ENV_FILE": str(env_path)},
    )


def test_dispatcher_loads_the_env_file_and_execs_the_script(tmp_path):
    r = dispatch(tmp_path, "demo", "extra-arg")
    assert r.returncode == 0
    assert "secret=from-env-file" in r.stdout
    assert "extra-arg" in r.stdout


def test_dispatcher_refuses_a_path_instead_of_a_name(tmp_path):
    r = dispatch(tmp_path, "../../etc/passwd")
    assert r.returncode == 64
    assert "invalid script name" in r.stderr


@pytest.mark.parametrize("name", ["demo/../demo", "Demo", "demo;id", "-x"])
def test_dispatcher_rejects_anything_that_is_not_a_plain_name(tmp_path, name):
    assert dispatch(tmp_path, name).returncode == 64


def test_dispatcher_reports_an_unknown_script(tmp_path):
    r = dispatch(tmp_path, "nosuch")
    assert r.returncode == 78
    assert "no executable script" in r.stderr


def test_dispatcher_refuses_to_run_without_secrets(tmp_path):
    """Running the deploy jobs without /etc/tagent.env would half-work, which
    is worse than not running: no alert webhook, no status token."""
    r = dispatch(tmp_path, "demo", env_file=False)
    assert r.returncode == 78
    assert "missing or unreadable" in r.stderr


def test_dispatcher_lists_what_it_can_run(tmp_path):
    r = dispatch(tmp_path, scripts=("pull-deploy", "publish-status"))
    assert r.returncode == 64
    assert "pull-deploy" in r.stderr and "publish-status" in r.stderr
