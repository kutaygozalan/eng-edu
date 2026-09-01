#!/usr/bin/env bash
# Publish redacted telemetry to an orphan `agent-status` branch.
#
# The problem this solves: a future session can read this repository but cannot
# ssh to the VM. Without this, "how is the agent actually doing?" is answerable
# only by someone with shell access, which is the same as not answerable.
#
# So the box pushes a small JSON file to a branch that carries nothing else.
# Orphan, because agent status has no shared history with the source: it is a
# different kind of thing, it churns every weeknight, and mixing it into the
# code branch would bury real diffs under status noise.
#
# WHAT IT REFUSES TO DO
#   - Publish financials to a repository that is not declared private. This
#     repository is a fork of google/eng-edu, and GitHub has no private fork of
#     a public repo: the fork is public. TAGENT_STATUS_REPO_IS_PRIVATE=1 is an
#     explicit operator claim, not something this script will infer.
#   - Publish a malformed, truncated or empty file. A half-written telemetry
#     file still parses as "no errors, no open lots, nothing to report" - it
#     reads as all-quiet when the truth is that the agent is broken. That
#     misreading is worse than a gap in the history, so a failed validation
#     leaves yesterday's file standing.
#
# Credentials: TAGENT_STATUS_TOKEN, a fine-grained PAT scoped to contents:write
# on this repository ONLY. It never reaches a command line (visible via /proc)
# or a stored remote URL - git asks for it through GIT_ASKPASS and it lives in
# the environment, same protection as /etc/tagent.env.
set -Eeuo pipefail

TAGENT_HOME="${TAGENT_HOME:-/opt/tagent}"
APP_DIR="$TAGENT_HOME/trading-agent"
VENV="${TAGENT_VENV:-$APP_DIR/.venv}"
CONFIG="${TAGENT_CONFIG:-$TAGENT_HOME/config.yaml}"
STATUS_BRANCH="${TAGENT_STATUS_BRANCH:-agent-status}"
STATUS_REPO="${TAGENT_STATUS_REPO:-https://github.com/kutaygozalan/eng-edu.git}"
INCLUDE_FINANCIALS=0

log() { printf '%s publish-status: %s\n' "$(date -Is)" "$*"; }

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

on_error() {
    local code=$?
    alert "tagent publish-status FAILED (exit $code) on $(hostname) at $(date -Is)"
    exit "$code"
}
trap on_error ERR

while [[ $# -gt 0 ]]; do
    case "$1" in
        --include-financials) INCLUDE_FINANCIALS=1 ;;
        -h|--help)
            sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 64 ;;
    esac
    shift
done

# ------------------------------------------------------------- preflight --

if [[ -z "${TAGENT_STATUS_TOKEN:-}" ]]; then
    echo "TAGENT_STATUS_TOKEN is not set." >&2
    echo "Create a fine-grained PAT with contents:write on THIS REPOSITORY ONLY" >&2
    echo "and add it to /etc/tagent.env. Nothing else needs to be granted." >&2
    exit 78   # EX_CONFIG
fi

if [[ $INCLUDE_FINANCIALS -eq 1 && "${TAGENT_STATUS_REPO_IS_PRIVATE:-0}" != "1" ]]; then
    cat >&2 <<'MSG'
REFUSED: --include-financials without TAGENT_STATUS_REPO_IS_PRIVATE=1.

This repository is a fork of a public repository, and forks of public repos are
public - there is no such thing as a private fork of a public repo on GitHub.
Publishing equity and P&L here publishes them to everyone.

If the destination really is a private repository, set both:
    TAGENT_STATUS_REPO=<the private repo>
    TAGENT_STATUS_REPO_IS_PRIVATE=1
MSG
    exit 77   # EX_NOPERM
fi

[[ -x "$VENV/bin/tagent" ]] || { echo "tagent not installed at $VENV" >&2; exit 78; }

# ------------------------------------------------------------- collect --

STAGE=$(mktemp -d "${TMPDIR:-/tmp}/tagent-status.XXXXXX")
chmod 700 "$STAGE"
WORK="$STAGE/repo"
ASKPASS="$STAGE/askpass"        # outside the work tree, so it can never be committed
PAYLOAD="$STAGE/telemetry.json"
trap 'rm -rf "$STAGE"' EXIT

telemetry_args=()
[[ $INCLUDE_FINANCIALS -eq 1 ]] && telemetry_args+=(--include-financials)

# `|| rc=$?` rather than `set +e`: an ERR trap fires even under `set +e`, so
# the generic handler would pre-empt the specific message below.
gen_rc=0
"$VENV/bin/tagent" --config "$CONFIG" telemetry "${telemetry_args[@]}" \
    > "$PAYLOAD" || gen_rc=$?
if [[ $gen_rc -ne 0 ]]; then
    alert "tagent publish-status: telemetry generation failed (exit $gen_rc) on $(hostname); nothing published"
    exit 1
fi

# The gate that matters. Checks JSON validity, a size floor, the required keys,
# and - independently of how the payload was generated - that no financial key
# is present unless disclosure was explicitly allowed. Implemented in Python so
# the rules are unit-tested rather than buried in an untested heredoc here.
validate_args=()
[[ $INCLUDE_FINANCIALS -eq 1 ]] && validate_args+=(--allow-financials)

val_rc=0
"$VENV/bin/python" -m tagent.telemetry --validate "$PAYLOAD" \
    "${validate_args[@]}" || val_rc=$?
if [[ $val_rc -ne 0 ]]; then
    alert "tagent publish-status: telemetry FAILED VALIDATION on $(hostname); nothing published. Yesterday's status file is now stale - do not read it as current."
    log "refusing to publish; see the REFUSED lines above"
    exit 1
fi

sha=$("$VENV/bin/python" -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["git"]["sha"][:12])' "$PAYLOAD")
today=$(date -u +%Y-%m-%d)
log "telemetry validated: $today, commit $sha, $(wc -c < "$PAYLOAD") bytes"

# --------------------------------------------------------------- publish --

printf '#!/bin/sh\nexec printf %%s "$TAGENT_STATUS_TOKEN"\n' > "$ASKPASS"
chmod 700 "$ASKPASS"
export GIT_ASKPASS="$ASKPASS"
export GIT_TERMINAL_PROMPT=0

# The username is not a secret and the password comes from GIT_ASKPASS, so the
# token never lands in a remote URL, in .git/config, or in `ps` output.
push_url="${STATUS_REPO/https:\/\//https://x-access-token@}"

git init --quiet "$WORK"
git -C "$WORK" remote add origin "$push_url"
git -C "$WORK" config user.name  "tagent"
git -C "$WORK" config user.email "tagent@$(hostname)"

fetch_rc=0
git -C "$WORK" fetch --quiet --depth 1 origin "$STATUS_BRANCH" || fetch_rc=$?
if [[ $fetch_rc -eq 0 ]]; then
    git -C "$WORK" checkout --quiet -b "$STATUS_BRANCH" FETCH_HEAD
    log "continuing existing $STATUS_BRANCH"
else
    # A fresh `git init` leaves HEAD unborn, so pointing it at the status
    # branch makes the first commit a root commit - an orphan by construction,
    # with no --orphan special case needed.
    git -C "$WORK" symbolic-ref HEAD "refs/heads/$STATUS_BRANCH"
    log "creating orphan $STATUS_BRANCH (first publish)"
fi

mkdir -p "$WORK/status/history"
cp "$PAYLOAD" "$WORK/status/latest.json"
cp "$PAYLOAD" "$WORK/status/history/$today.json"

cat > "$WORK/README.md" <<MSG
# agent-status

Operational telemetry from the trading agent VM, written by
\`trading-agent/deploy/publish-status.sh\` after the nightly review.

This branch is an **orphan**: it shares no history with the code. Nothing here
is source, and nothing here should be merged anywhere.

- \`status/latest.json\` - most recent run
- \`status/history/YYYY-MM-DD.json\` - one file per publishing day

## Reading it

\`redacted: true\` means the payload carries no dollar amounts, no equity, no
position sizes and no symbols - only operational state: which commit is
running, kill switch, dry_run, open lot count, event and gate-rejection counts,
recent errors, lessons, per-setup expectancy as a percentage, and calibration.

A **missing or stale \`generated_at\`** is the signal that matters. The publisher
refuses to write a malformed or truncated file, so a status file that has
stopped advancing means the agent or the VM is broken - not that it had a quiet
day.
MSG

git -C "$WORK" add -A
if git -C "$WORK" diff --cached --quiet; then
    log "no change since the last publish; nothing to push"
    exit 0
fi

git -C "$WORK" commit --quiet \
    -m "status $today (commit $sha)" \
    -m "Redacted: $([[ $INCLUDE_FINANCIALS -eq 1 ]] && echo no || echo yes). Generated on $(hostname)."

push_rc=0
git -C "$WORK" push --quiet origin "HEAD:refs/heads/$STATUS_BRANCH" || push_rc=$?
if [[ $push_rc -ne 0 ]]; then
    alert "tagent publish-status: push to $STATUS_BRANCH failed (exit $push_rc) on $(hostname). Check that TAGENT_STATUS_TOKEN has contents:write and has not expired."
    exit 1
fi

log "published $today to $STATUS_BRANCH"
exit 0
