#!/usr/bin/env bash
# Pull-deploy: the VM updates itself from the deploy branch, unattended.
#
# There is no promotion gate and no human review step. That is a deliberate
# choice, and it is only defensible because the switch is guarded on both sides:
#
#   BEFORE  a throwaway git worktree at the incoming commit gets its own venv,
#           the full test suite, and `tagent doctor` run against the LIVE config.
#           Nothing is switched until all three pass, so a broken commit is
#           caught while the running install is still untouched.
#   AFTER   `tagent health` must pass. If it does not, the previous commit is
#           restored and reinstalled in the same run, so the box is never left
#           overnight on code that cannot reach the broker.
#
# It refuses to run inside weekday market hours, and refuses while the kill
# switch is latched. A deploy is not urgent; a half-swapped install during the
# session is how an open position gets orphaned.
#
# What it will never touch:
#   config.yaml     - risk limits and dry_run live on the box, not in git, so a
#                     limit change stays an operator decision rather than
#                     something the agent can ship to itself.
#   /etc/tagent.env - secrets.
# Both are checksummed before and after, and a change is a hard failure.
set -Eeuo pipefail

# ------------------------------------------------------- run from a copy --
# This script deploys the repository that contains it, so the `git checkout`
# below can rewrite THIS FILE halfway through the run. Bash reads a script
# lazily, by byte offset, and does not notice: it resumes at the old offset in
# the new file. Measured behaviour when that happens is not a crash but a
# silent truncation - bash simply stops early and exits 0. Here that would mean
# switching commits and then skipping the health check, the rollback and the
# config guard while reporting success, which is the exact failure this whole
# script exists to prevent.
#
# So run from a private copy. The copy unlinks itself immediately: bash holds
# the file descriptor open and keeps reading fine, and nothing is left behind
# in /tmp even if the run is killed.
if [[ "${TAGENT_DEPLOY_REEXEC:-}" != "$0" ]]; then
    self=$(mktemp "${TMPDIR:-/tmp}/tagent-pull-deploy.XXXXXX")
    cat "$0" > "$self"
    chmod 700 "$self"
    TAGENT_DEPLOY_REEXEC="$self" exec bash "$self" "$@"
fi
rm -f "$0"   # equal to TAGENT_DEPLOY_REEXEC by the test above: the copy, not the original

TAGENT_HOME="${TAGENT_HOME:-/opt/tagent}"
APP_DIR="$TAGENT_HOME/trading-agent"
VENV="${TAGENT_VENV:-$APP_DIR/.venv}"
CONFIG="${TAGENT_CONFIG:-$TAGENT_HOME/config.yaml}"
ENV_FILE="${TAGENT_ENV_FILE:-/etc/tagent.env}"
BRANCH="${TAGENT_DEPLOY_BRANCH:-claude/robinhood-trading-bot-research-hrskq6}"
QUARANTINE="$TAGENT_HOME/.deploy-quarantine"

log() { printf '%s pull-deploy: %s\n' "$(date -Is)" "$*"; }

alert() {
    local msg="$1"
    echo "$msg" >&2
    [[ -n "${TAGENT_ALERT_WEBHOOK:-}" ]] || return 0
    local payload
    payload=$(printf '%s' "$msg" | python3 -c \
        'import json,sys; print(json.dumps({"text": sys.stdin.read()}))') || return 0
    curl -fsS -m 10 -X POST "$TAGENT_ALERT_WEBHOOK" \
         -H 'Content-Type: application/json' -d "$payload" >/dev/null \
         || echo "alert webhook failed" >&2
}

refuse() { log "REFUSING: $*"; exit 0; }   # not an error; there is just nothing to do

on_error() {
    local code=$?
    alert "tagent pull-deploy FAILED (exit $code) on $(hostname) at $(date -Is)"
    exit "$code"
}
trap on_error ERR

# `|| rc=$?` rather than `set +e` throughout this script: an ERR trap still
# fires under `set +e`, so wrapping a command that is EXPECTED to fail in
# `set +e` would hand control to on_error before we could inspect the code.
# (bash exempts if/while conditions and the non-final parts of &&/|| lists,
# which is exactly what `|| rc=$?` makes this.)

PY="$VENV/bin/python"
[[ -x "$PY" ]] || PY=python3

# ------------------------------------------------------------ refuse to run --

# Market-hours guard. Computed in python via zoneinfo so DST is the tz
# database's problem rather than a hardcoded UTC offset that is wrong for half
# the year. Deliberately stdlib-only and importing nothing from tagent: this
# guard has to keep working on exactly the days the install is broken.
window_rc=0
"$PY" - <<'PY' || window_rc=$?
from datetime import datetime
from zoneinfo import ZoneInfo

now = datetime.now(ZoneInfo("America/New_York"))
blocked = now.weekday() < 5 and 9 <= now.hour < 17
print(now.strftime("%Y-%m-%d %H:%M %Z"), "->", "market hours" if blocked else "clear")
raise SystemExit(1 if blocked else 0)
PY
if [[ $window_rc -ne 0 ]]; then
    refuse "inside weekday 09:00-17:00 ET; deploys wait for the close"
fi

# Kill switch. If the operator has halted trading, they get to decide what code
# runs when it resumes.
#
# Reading it through `tagent telemetry` means the check fails closed: if the
# current install is broken enough that it cannot report its own state, this
# script will not start swapping commits underneath it. Deploy by hand at that
# point - the failure is already worth a human's attention.
state_rc=0
state_json=$("$VENV/bin/tagent" --config "$CONFIG" telemetry --include-financials \
             2>/dev/null) || state_rc=$?
if [[ $state_rc -ne 0 ]]; then
    alert "tagent pull-deploy: cannot read agent state on $(hostname); not deploying. Run '$VENV/bin/tagent --config $CONFIG telemetry' to see why."
    # Non-zero, unlike the other refusals: "there was nothing to do" and "I could
    # not work out whether it was safe to do anything" are different answers.
    log "could not read agent state (tagent telemetry exited $state_rc); not deploying"
    exit 1
fi

killed=$(printf '%s' "$state_json" | "$PY" -c \
    'import json,sys; print("1" if json.load(sys.stdin)["kill_switch"]["engaged"] else "0")')
if [[ "$killed" == "1" ]]; then
    refuse "kill switch is engaged; resume trading before deploying"
fi

# Doctor is only meaningful against the real balance: limits that are coherent
# at $2,000 are not automatically coherent at $8,000, and checking the incoming
# commit against a made-up number checks nothing.
equity=$(printf '%s' "$state_json" | "$PY" -c \
    'import json,sys; print("%.2f" % json.load(sys.stdin)["financials"]["peak_equity"])')
if [[ -z "$equity" || "$equity" == "0.00" ]]; then
    equity="${TAGENT_DOCTOR_EQUITY:-2000}"
    log "no recorded equity yet; running doctor at \$$equity"
fi

# ------------------------------------------------------------------- fetch --

git -C "$TAGENT_HOME" fetch --quiet origin "$BRANCH"
CURRENT=$(git -C "$TAGENT_HOME" rev-parse HEAD)
INCOMING=$(git -C "$TAGENT_HOME" rev-parse "origin/$BRANCH")

if [[ "$CURRENT" == "$INCOMING" ]]; then
    log "already at ${CURRENT:0:12}; nothing to deploy"
    exit 0
fi

if [[ -f "$QUARANTINE" ]] && grep -qx "$INCOMING" "$QUARANTINE"; then
    refuse "${INCOMING:0:12} is quarantined (it failed health on this box). Fix it upstream, or clear $QUARANTINE."
fi

# Tracked-file modifications only: config.yaml and .venv live inside the repo
# directory as untracked files and are expected to be there.
dirty=$(git -C "$TAGENT_HOME" status --porcelain --untracked-files=no)
if [[ -n "$dirty" ]]; then
    alert "tagent pull-deploy: $TAGENT_HOME has local modifications on $(hostname); not deploying."
    refuse "working tree has local modifications:"$'\n'"$dirty"
fi

log "deploying ${CURRENT:0:12} -> ${INCOMING:0:12} on $BRANCH"

# ------------------------------------------------- verify BEFORE switching --

STAGE=$(mktemp -d "${TMPDIR:-/tmp}/tagent-deploy.XXXXXX")
WORKTREE="$STAGE/src"
cleanup() {
    git -C "$TAGENT_HOME" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
    rm -rf "$STAGE"
    # Removing the directory is not enough on its own: git keeps per-worktree
    # metadata under .git/worktrees that accumulates one stale entry per run.
    git -C "$TAGENT_HOME" worktree prune >/dev/null 2>&1 || true
}
trap 'cleanup' EXIT

git -C "$TAGENT_HOME" worktree add --quiet --detach "$WORKTREE" "$INCOMING"
log "staged ${INCOMING:0:12} at $WORKTREE"

# A separate venv, not the live one. Installing the incoming commit's
# dependencies into the running venv would already be the switch.
"$PY" -m venv "$STAGE/venv"
"$STAGE/venv/bin/pip" install --quiet --upgrade pip
"$STAGE/venv/bin/pip" install --quiet -e "$WORKTREE/trading-agent"
"$STAGE/venv/bin/pip" install --quiet pytest

log "running the test suite against ${INCOMING:0:12}"
test_rc=0
( cd "$WORKTREE/trading-agent" && "$STAGE/venv/bin/python" -m pytest -q ) || test_rc=$?
if [[ $test_rc -ne 0 ]]; then
    alert "tagent pull-deploy: ${INCOMING:0:12} FAILED TESTS on $(hostname); staying on ${CURRENT:0:12}"
    log "tests failed (exit $test_rc); not switching"
    exit 1
fi

# Against the LIVE config, read-only. This is the check that catches "the new
# code tightened a limit the box's config violates" before it is running.
log "running doctor against $CONFIG at \$$equity"
doctor_rc=0
"$STAGE/venv/bin/tagent" --config "$CONFIG" doctor --equity "$equity" || doctor_rc=$?
if [[ $doctor_rc -ne 0 ]]; then
    alert "tagent pull-deploy: ${INCOMING:0:12} FAILED DOCTOR against the live config on $(hostname); staying on ${CURRENT:0:12}"
    log "doctor failed (exit $doctor_rc); not switching"
    exit 1
fi

# ------------------------------------------------------------------ switch --

# Fingerprints of the two files this script must never modify. Checked again at
# the end: a git operation that somehow rewrote either of them is a hard stop,
# not something to discover weeks later from a limit that quietly changed.
config_before=$(sha256sum "$CONFIG" | cut -d' ' -f1)
env_before=""
[[ -r "$ENV_FILE" ]] && env_before=$(sha256sum "$ENV_FILE" | cut -d' ' -f1)

switch_to() {
    local sha="$1"
    git -C "$TAGENT_HOME" checkout --quiet --detach "$sha"
    # The live install is editable, so the checkout alone already changes the
    # running code. The reinstall is for everything editable mode does NOT
    # pick up: new dependencies, changed entry points, package metadata.
    "$VENV/bin/pip" install --quiet -e "$APP_DIR"
}

switch_to "$INCOMING"
log "switched to ${INCOMING:0:12}"

# --------------------------------------------------------- verify and roll --

health_rc=0
"$VENV/bin/tagent" --config "$CONFIG" health || health_rc=$?

if [[ $health_rc -eq 2 ]]; then
    # Exit 2 is expired OAuth. That is a credentials problem and a rollback
    # would not fix it - the previous commit cannot log in either. Keep the new
    # code, and say plainly that this needs a human with a browser.
    alert "tagent: Robinhood OAuth has EXPIRED on $(hostname). Deploy of ${INCOMING:0:12} succeeded and was KEPT; trading stays halted until you run: tagent auth"
    log "health returned 2 (expired OAuth): credentials, not code. Keeping ${INCOMING:0:12}."
    exit 0
fi

if [[ $health_rc -ne 0 ]]; then
    log "health failed (exit $health_rc); rolling back to ${CURRENT:0:12}"
    switch_to "$CURRENT"

    # Was it the code, or the world? Re-run health on the commit that was
    # working an hour ago. If that fails too, the broker or the network is
    # down and quarantining a probably-fine commit would just block tomorrow's
    # deploy for no reason.
    #
    # Exit 2 from the previous commit counts as working: expired OAuth means the
    # install got far enough to authenticate and be turned away, so the code is
    # fine and the difference really is the incoming commit. (The incoming
    # commit cannot itself be a 2 here - that was handled above.)
    prev_rc=0
    "$VENV/bin/tagent" --config "$CONFIG" health || prev_rc=$?
    if [[ $prev_rc -eq 0 || $prev_rc -eq 2 ]]; then
        echo "$INCOMING" >> "$QUARANTINE"
        prev_note="healthy"
        [[ $prev_rc -eq 2 ]] && prev_note="running (its only complaint is expired OAuth)"
        alert "tagent pull-deploy: ${INCOMING:0:12} FAILED HEALTH (exit $health_rc) on $(hostname). Rolled back to ${CURRENT:0:12}, which is $prev_note - so the fault is the incoming commit. It is quarantined and will not be retried."
        log "rolled back; ${INCOMING:0:12} quarantined in $QUARANTINE"
    else
        alert "tagent pull-deploy: health is failing on $(hostname) on BOTH ${INCOMING:0:12} and ${CURRENT:0:12} (exit $prev_rc). Rolled back; this looks environmental, not a bad commit. Not quarantined."
        log "rolled back; previous commit also unhealthy (exit $prev_rc), so not quarantining"
    fi
    exit 1
fi

# ------------------------------------------------------------- final guard --

config_after=$(sha256sum "$CONFIG" | cut -d' ' -f1)
if [[ "$config_before" != "$config_after" ]]; then
    alert "tagent pull-deploy: $CONFIG CHANGED during deploy on $(hostname). This must never happen - inspect the box before the next open."
    exit 1
fi
if [[ -n "$env_before" ]]; then
    env_after=$(sha256sum "$ENV_FILE" | cut -d' ' -f1)
    if [[ "$env_before" != "$env_after" ]]; then
        alert "tagent pull-deploy: $ENV_FILE CHANGED during deploy on $(hostname). This must never happen - inspect the box before the next open."
        exit 1
    fi
fi

log "deployed ${INCOMING:0:12}: tests, doctor and health all pass"
exit 0
