# Deployment runbook

Target: a small cloud VM running one-shot cycles on cron, 09:45–15:25 ET.

**Nothing in this document asks for your Robinhood username, password, MFA code,
or account number. It never will.** Authorization happens in your browser via
OAuth; the resulting token lands on the VM encrypted. Anyone who asks you to
paste Robinhood credentials into a config file or a chat window is wrong.

---

## 1. Provision the VM

**Recommended: Hetzner CPX11 in Ashburn, VA — ~$5/month, 2 vCPU, 2 GB.**

A cycle peaks at **65 MB RSS** — measured, not estimated: interpreter, the
Anthropic SDK, a 500-decision database, and full context assembly. Any of the
options below clears that comfortably; the choice is about predictability.

> **On GCP's "free" tier:** the `e2-micro` instance is covered, but the external
> IPv4 address it needs for outbound calls is not — that runs ~$0.005/hr, about
> **$3.65/month**. There is no way around it: without an external IP the VM has
> no internet egress, and Cloud NAT costs roughly $32/month. So GCP is ~$3.65,
> not free, which makes Hetzner's extra ~$1.35 buy double the RAM and a
> non-shared core.

| Option | True cost | RAM | Notes |
|---|---|---|---|
| **Hetzner CPX11, Ashburn** | **~$5/mo** | **2 GB** | **Recommended.** IP included, 2 vCPU, predictable billing |
| GCP `e2-micro` + external IP | ~$3.65/mo | 1 GB | Cheapest; `deploy/gcp-create-vm.sh` provisions it correctly |
| DigitalOcean basic | $6/mo | 1 GB | Simplest UX |
| Oracle Cloud always-free ARM | free | 24 GB | Genuinely free, but Oracle reclaims idle instances |

For GCP specifically, `deploy/gcp-create-vm.sh` creates the instance with **no
service account and no API scopes** (the agent never calls GCP, so a compromise
cannot pivot into your project), Shielded VM enabled, and **no inbound firewall
rules at all** — SSH goes through IAP, so port 22 is never exposed. It also
attaches a startup script that runs `bootstrap.sh` on first boot, and stops
deliberately short of authorizing or scheduling anything.

**Do not use a spot or preemptible instance.** Saving $3/month is not worth an
instance vanishing mid-cycle with an order in flight.

Set the OS clock to UTC and let cron handle ET via `CRON_TZ`. Mixing timezone
conversion across cron, the OS, and application code is how a bot ends up
trading an hour late twice a year.

## 2. Install

One command does packages, clone, venv, test run, data directories, the
secrets scaffold, and the cron wrapper:

```bash
curl -fsSL https://raw.githubusercontent.com/kutaygozalan/eng-edu/\
claude/robinhood-trading-bot-research-hrskq6/trading-agent/deploy/bootstrap.sh \
  | bash
```

Prefer to read it first (you should — it is a script that installs a thing that
trades your money):

```bash
git clone -b claude/robinhood-trading-bot-research-hrskq6 \
  https://github.com/kutaygozalan/eng-edu.git /opt/tagent
less /opt/tagent/trading-agent/deploy/bootstrap.sh
bash /opt/tagent/trading-agent/deploy/bootstrap.sh
```

It runs the test suite as part of setup and stops on failure. It **does not**
authorize Robinhood, install cron, or place any order — those stay deliberate.
It is safe to re-run: it will not overwrite an existing config or regenerate a
token key over one already in use (which would make your stored Robinhood token
permanently undecryptable).

## 3. Secrets — environment only, never the config file

```bash
# Token-file encryption key. Losing it means re-running `tagent auth`;
# leaking it plus the token file means someone else can trade your account.
python3 -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"

sudo tee /etc/tagent.env >/dev/null <<'ENV'
ANTHROPIC_API_KEY=sk-ant-...
TAGENT_TOKEN_KEY=<the base64 value printed above>
TAGENT_ALERT_WEBHOOK=https://hooks.slack.com/services/...
# Only needed for the nightly status publish - see section 9.
TAGENT_STATUS_TOKEN=github_pat_...
ENV
sudo chmod 600 /etc/tagent.env
```

## 4. Authorize (once, headless)

```bash
set -a && . /etc/tagent.env && set +a
.venv/bin/tagent --config /opt/tagent/config.yaml auth
```

It prints a URL. Open that in a browser **on your laptop**, approve the
connection to your Agentic account, and Robinhood redirects to a `localhost`
page that fails to load. **That failure is expected** — nothing is listening
there. Copy the full URL from the address bar and paste it back at the prompt.

The encrypted token is written to `/data/robinhood-tokens.enc`, mode 0600.

> Re-authorization is periodic and manual by design. The 08:00 health check
> exists so you find out at breakfast, not from a day of silence.

## 5. Verify before trusting it

```bash
.venv/bin/tagent --config /opt/tagent/config.yaml discover   # actual tool names
.venv/bin/tagent --config /opt/tagent/config.yaml doctor --equity 2000
.venv/bin/tagent --config /opt/tagent/config.yaml health
```

`discover` prints the MCP tool surface and how each capability resolved. **Read
this output.** If `place_order` bound to something that isn't an equity order
tool, pin the correct names under `broker.tool_overrides` in config before going
further. Robinhood has changed this surface twice since May.

## 6. Schedule

```bash
sudo cp deploy/tagent-run deploy/tagent-run-script /usr/local/bin/
sudo chmod +x /usr/local/bin/tagent-run /usr/local/bin/tagent-run-script
crontab deploy/crontab.example
```

Trading cycles through the session, a review at 17:30, a health check at 08:00,
and after the close the self-maintenance pair: a status publish at 17:45 and a
pull-deploy at 18:30 (section 9).
Half-days are handled in code — cron still fires at 13:05 on those days and the
cycle exits immediately.

Two wrappers, because they must be able to fail independently:

| Wrapper | Runs | Used for |
|---|---|---|
| `tagent-run` | the Python CLI | `cycle`, `review`, `health` |
| `tagent-run-script` | `deploy/<name>.sh` | `publish-status`, `pull-deploy` |

Both load `/etc/tagent.env` and nothing else. The split matters on the day the
Python install is the broken thing: `pull-deploy` still has to run.

## 7. Running costs — read this before choosing a cadence

At a $2,000 account, **the model call, not the VM, is the dominant expense**,
and it is large enough to change the decision:

| Cadence | Calls/yr | Opus 5 API | + free VM | Drag on $2,000 |
|---|---:|---:|---:|---:|
| every 20 min | 4,536 | $272 | $272 | **13.6%** |
| every 30 min | 3,024 | $189 | $189 | 9.5% |
| **hourly (shipped default)** | **1,512** | **$106** | **$106** | **5.3%** |
| twice daily | 504 | $50 | $50 | 2.5% |

The 20-minute cadence costs roughly what the best published LLM trading agents
*return* (~16% annually, live). The agent would have to be world-class just to
break even on its own electricity bill.

Hourly is the shipped default because it cuts that to 5.3% **and** the evidence
on trade frequency says fewer decisions improve retail returns. Cheaper and
probably better is a rare combination; take it.

Two further levers if cost still bites:

- **A pre-check already skips the model** whenever no order could pass the gate
  anyway — flat account at its daily trade cap, blackout window, kill switch,
  no settled cash. It never skips while a position is open, because delaying an
  exit to save money is the wrong trade-off.
- **`output_config.effort`** is the next lever (`high` → `medium` roughly halves
  output tokens). Most cycles correctly conclude "no trade," which does not need
  maximum reasoning depth.

Revisit the cadence as the account grows: at $20,000, hourly Opus is a 0.5%
drag and the calculus changes completely.

## 8. First week

### Before real money: paper vs. dry_run

These test different things, and it is worth being precise about which:

| | What runs | What it proves |
|---|---|---|
| `dry_run: true` | reasoning, gate, ledger writes — **no orders** | the agent proposes sane trades and the gate rejects the right ones |
| `broker.kind: paper` | everything, including fills | the **ledger** works: lots open, partial fills complete, closes attribute P&L back to the opening decision, the review has outcomes to learn from |

`dry_run` never produces an `outcomes` row, so it never exercises
reconciliation, expectancy, or calibration — the parts most likely to be
subtly wrong. Paper does.

```bash
cp config/config.example.yaml paper.yaml   # set kind: paper, a separate db_path
tagent --config paper.yaml health          # no OAuth needed
tagent --config paper.yaml cycle
tagent --config paper.yaml review
tagent --config paper.yaml status
```

**Use a separate `db_path`.** Paper prices are synthetic noise, and the agent
writes lessons and per-setup expectancy from whatever it trades. Sharing a
database means carrying beliefs learned from a random number generator into the
account with money in it. `tagent doctor` warns about this, and
`tagent telemetry` reports `broker_kind` so you can tell the two apart.

Two things paper is **not**: a backtest (there is no history, no earnings, no
corporate actions — a P&L number from it means nothing), and a liquidity
simulation (fills cross the spread, but queue priority is not modelled, so
resting limit orders do better here than they would live).

### Then the real thing


Leave `dry_run: true`. The agent reasons, proposes, and records every decision
and every gate rejection, but places no orders.

Each evening:

```bash
.venv/bin/tagent --config /opt/tagent/config.yaml status
```

Look for: proposals that make sense, gate rejections that fire for the right
reasons, and a `reasoning_summary` that isn't inventing conviction. If the agent
proposes nothing for days, that is a legitimate outcome — check the rejection
reasons before assuming it's broken.

**Only then** set `dry_run: false`. That flip is the whole difference between a
simulation and real money; make it deliberately, on a day you can watch.

## 9. The pull-deploy loop

After the close the VM maintains itself: it publishes what it has been doing,
then updates its own code. **There is no promotion gate and no human review
step** — a commit on the deploy branch is deployed that evening if it can prove
itself on the box.

```
17:30  tagent-run review               tonight's lessons and setup statistics
17:45  tagent-run-script publish-status push redacted telemetry to `agent-status`
18:30  tagent-run-script pull-deploy    fetch, prove, switch (or roll back)
```

Full auto is only defensible because the switch is guarded on both sides. The
question to keep asking is not "did a human look at it" but "what does this
script refuse to do".

### 9.1 `pull-deploy.sh`

**It refuses to run at all when:**

| Condition | Why |
|---|---|
| Weekday 09:00–17:00 ET | A half-swapped install during the session is how an open position gets orphaned. Computed in Python via `zoneinfo`, so DST is the tz database's problem rather than a hardcoded offset that is wrong for half the year. |
| The kill switch is engaged | If you have halted trading, you decide what code runs when it resumes. |
| It cannot read the agent's own state | Fails closed. A box too broken to report on itself is not one to start swapping commits underneath — deploy by hand, the failure already deserves a human. |
| The working tree has local modifications | Someone hand-edited the box. Overwriting that silently loses whatever they were doing. |
| The incoming commit is quarantined | See below. |

**Before switching anything**, it stages the incoming commit in a throwaway
`git worktree`, builds a *separate* venv there, and runs:

1. the full test suite, and
2. `tagent doctor` **against the live `config.yaml`**, at the account's real
   equity.

Only if both pass does it switch. The doctor step is the one that is easy to
skip and shouldn't be: it catches "the new code tightened a limit that this
box's config violates" while the running install is still untouched.

**After switching**, `tagent health` must pass:

| Health exit | Meaning | What happens |
|---|---|---|
| 0 | Fine | Done. |
| 2 | Expired OAuth | **No rollback.** This is credentials, not code — the previous commit cannot log in either. The new commit is kept and you get an alert saying to run `tagent auth`. |
| anything else | Broken | Roll back to the previous commit and reinstall, then re-run health on it. |

That last re-run is the discriminator. If the *previous* commit is healthy
(exit 0, or exit 2 — expired OAuth still proves the install works), the fault
is the incoming commit, so its sha is appended to `/opt/tagent/.deploy-quarantine`
and never retried. If the previous commit fails too, the broker or the network
is down; nothing is quarantined, and tomorrow's run tries again.

Without the quarantine, a genuinely bad commit would be deployed, fail, roll
back, and be deployed again every single night.

**Two files it will never touch:** `config.yaml` and `/etc/tagent.env`. Risk
limits, `dry_run` and secrets live on the box and stay out of git — a limit
change must remain an operator decision rather than something the agent can
ship to itself. Both are checksummed before and after, and a change is a hard
failure with an alert.

> **The script runs from a private copy of itself.** It deploys the repository
> that contains it, so `git checkout` rewrites the file mid-run. Bash reads a
> script lazily by byte offset and does not notice: measured behaviour is not a
> crash but a *silent truncation* — bash stops early and exits 0. That would
> mean switching commits and then skipping the health check, the rollback and
> the config guard while reporting success. The copy unlinks itself at once, so
> nothing is left in `/tmp` even if the run is killed.

After a deploy, `HEAD` on the box is **detached** at the deployed commit. That
is intentional — there is no branch pointer to get out of sync. `git log` works
as normal; re-running `bootstrap.sh` puts you back on a branch.

### 9.2 `tagent telemetry` and `publish-status.sh`

The problem: a future session can read this repository but cannot ssh to the
VM. Without telemetry, "how is the agent actually doing?" is answerable only by
someone with shell access, which is the same as not answerable.

So the box pushes a small JSON file to an orphan `agent-status` branch:
`status/latest.json`, plus one file per day under `status/history/`.

**`tagent telemetry` is redacted by default: no dollar amounts, no equity, no
position sizes, no symbols.** This repository is a fork of `google/eng-edu`,
and a fork of a public repo is public — GitHub has no private fork of a public
repo. The default payload carries only operational state:

- git sha, branch and dirty flag — which commit is actually running
- kill switch state and reason, `dry_run`, broker kind, model
- open lot **count**, closed trade **count**, last review timestamp
- events by kind, gate rejections by reason (stable codes, never prose)
- recent errors, lessons, per-setup posterior expectancy **as a percentage**,
  calibration buckets

Redaction is layered, and the layers are not equally strong:

1. **Structural** — financial values are not read from the database at all
   unless `--include-financials` is passed. Absent, not filtered.
2. **Textual** — free text written by the LLM or by an operator (lesson text,
   kill-switch reasons, exception messages) is scrubbed of currency amounts and
   of every symbol the agent has traded. This is the only heuristic layer and
   the one to distrust.
3. **Validation** — the finished payload is re-checked for financial keys
   anywhere in the tree, so a field added carelessly later fails the publish
   rather than leaking quietly.

`publish-status.sh` refuses to publish when:

- `--include-financials` is passed without `TAGENT_STATUS_REPO_IS_PRIVATE=1`.
  That variable is an explicit operator claim; the script will not infer it.
- The telemetry is malformed, empty, or below a size floor. **A truncated file
  still parses as "no errors, no open lots, nothing to report"** — it reads as
  a quiet day when the truth is that the agent is broken. A failed validation
  leaves yesterday's file standing, which is at least honestly stale.

**Credentials:** `TAGENT_STATUS_TOKEN`, a fine-grained PAT with
`contents:write` on this repository only. Nothing else needs granting. It never
reaches a command line (world-readable through `/proc`) or a stored remote URL
— git asks for it through `GIT_ASKPASS`, so it stays in the environment, the
same protection as `/etc/tagent.env`.

If the token is absent the publish exits 78 and nothing else is affected.

### 9.3 Reading the status branch

```bash
git fetch origin agent-status
git show origin/agent-status:status/latest.json | python3 -m json.tool
```

`redacted: true` tells you the payload is the safe one. The signal that matters
most is **`generated_at`**: because the publisher refuses to write a malformed
file, a status file that has stopped advancing means the agent or the VM is
broken — not that it had a quiet day.

### 9.4 Testing changes to these scripts

`tests/test_deploy_scripts.py` covers what holds without a VM: syntax, the
guards that must be present and in the right order, the crontab wiring, and the
dispatcher end to end. The behavioural paths — the test gate, rollback,
quarantine, the health exit-2 case — need a real git remote and a venv, and
were verified against a sandbox VM built like this:

```bash
# a bare "origin", a clone as TAGENT_HOME, a venv, a config.yaml
git clone -b <branch> <origin> /tmp/sandbox/home
python3 -m venv /tmp/sandbox/home/trading-agent/.venv
/tmp/sandbox/home/trading-agent/.venv/bin/pip install -e /tmp/sandbox/home/trading-agent
TAGENT_HOME=/tmp/sandbox/home TAGENT_DEPLOY_BRANCH=<branch> bash deploy/pull-deploy.sh
```

Push a commit with a failing test and confirm it does not switch; push one that
passes tests but fails health and confirm it rolls back and quarantines.

---

## Operating it

| Situation | Command |
|---|---|
| Stop it now | `tagent kill "reason"` — blocks new entries, still allows closes |
| Resume | `tagent resume` — deliberately interactive; nothing in the agent can call it |
| What does it know? | `tagent status` |
| Did auth lapse? | `tagent health` (exit 2 = re-run `tagent auth`) |
| Did limits break? | `tagent doctor --equity <current balance>` |
| What would a remote session see? | `tagent telemetry` — redacted JSON, same as the published file |
| Everything, money included | `tagent telemetry --include-financials` — local only; never publish this to a public repo |

Re-run `doctor` whenever the balance changes materially. Limits that made sense
at $2,000 do not automatically make sense at $8,000, and the failure is silent:
the gate just rejects everything.

## When it breaks

- **`AUTH EXPIRED`** — re-run `tagent auth`. Expected periodically.
- **`RESOLUTION FAILED`** — Robinhood changed tool names. Run `discover`, pin
  names in `broker.tool_overrides`.
- **Cycles run, nothing trades** — usually correct behavior. Check
  `tagent status` for the rejection reasons; if the same reason dominates, that
  is a real signal about the strategy, and the nightly review will write it down.
- **Kill switch latched** — it survives restarts on purpose. Read the reason in
  `tagent status` before resuming. Note that `pull-deploy` refuses to run while
  it is latched, so code updates pause until you resume.
- **`FAILED TESTS` / `FAILED DOCTOR` from pull-deploy** — the box is still on
  the old commit and still trading. Fix it upstream; nothing on the VM needs
  touching.
- **A commit was quarantined** — it failed `health` on the box while the
  previous commit was fine. It will not be retried. Fix it upstream, then
  `rm /opt/tagent/.deploy-quarantine` (or delete just that line).
- **The status branch stopped advancing** — read this as a broken agent, not a
  quiet one. `publish-status` refuses to publish a malformed file, so silence
  is a failure signal. Check `/var/log/tagent/publish.log`; the usual cause is
  an expired `TAGENT_STATUS_TOKEN`.

## What is not built yet

Be clear-eyed about the gap between "runs" and "complete":

- **Options.** Deliberately out of scope until the account can size them
  (`tagent doctor` enforces this).
- **Intraday realized P&L** in the daily-loss check. The gate currently sees
  unrealized P&L plus closed-lot totals; a day of many round trips is measured
  slightly conservatively.
- **The secondary news/politics agent.** The architecture is designed for it
  (the intelligence layer proposes into the same risk gate) but no data feeds
  are wired yet.
