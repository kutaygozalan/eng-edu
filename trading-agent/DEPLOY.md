# Deployment runbook

Target: a small cloud VM running one-shot cycles on cron, 09:45–15:25 ET.

**Nothing in this document asks for your Robinhood username, password, MFA code,
or account number. It never will.** Authorization happens in your browser via
OAuth; the resulting token lands on the VM encrypted. Anyone who asks you to
paste Robinhood credentials into a config file or a chat window is wrong.

---

## 1. Provision the VM

**Recommended: GCP `e2-micro` in `us-east1`, always-free tier. $0/month.**

A cycle peaks at **65 MB RSS** — measured, not estimated: interpreter, the
Anthropic SDK, a 500-decision database, and full context assembly. Against
1 GB that is comfortable with room to spare, and no swap is needed.

> An earlier draft of this file warned that a free `e2-micro` would OOM. That
> came from a report of running **headless Claude Code** (Node plus the whole
> agent harness) on one. `tagent cycle` is a lean one-shot Python process an
> order of magnitude smaller. The warning does not apply here.

The workload is 7 short cycles a day, each spending most of its wall-clock
waiting on an HTTP response. CPU is nearly irrelevant, so a shared-core
instance is not a compromise. `us-east1` is closest to both Robinhood's and
Anthropic's infrastructure.

| Option | Cost | Verdict |
|---|---|---|
| **GCP `e2-micro`, us-east1** | **free** | **Recommended.** Free tier covers 1 instance + 30 GB disk, non-preemptible |
| Hetzner CPX11, Ashburn VA | ~$5/mo | Best paid option: 2 vCPU / 2 GB, US East |
| DigitalOcean basic | $6/mo | Simplest UX; 1 GB at that price is fine here |
| Oracle Cloud always-free ARM | free | 24 GB RAM, wildly overprovisioned; Oracle may reclaim idle instances |
| AWS Lightsail | $5/mo | Fine, no advantage over the above |

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
sudo cp deploy/tagent-run /usr/local/bin/ && sudo chmod +x /usr/local/bin/tagent-run
crontab deploy/crontab.example
```

7 cycles a day (hourly at :45, with the last pulled to 15:15 to clear the closing
blackout), a review at 17:30, a health check at 08:00.
Half-days are handled in code — cron still fires at 13:05 on those days and the
cycle exits immediately.

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

---

## Operating it

| Situation | Command |
|---|---|
| Stop it now | `tagent kill "reason"` — blocks new entries, still allows closes |
| Resume | `tagent resume` — deliberately interactive; nothing in the agent can call it |
| What does it know? | `tagent status` |
| Did auth lapse? | `tagent health` (exit 2 = re-run `tagent auth`) |
| Did limits break? | `tagent doctor --equity <current balance>` |

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
  `tagent status` before resuming.

## What is not built yet

Be clear-eyed about the gap between "runs" and "complete":

- **A paper broker**, for exercising the loop without touching a live account.
- **Options.** Deliberately out of scope until the account can size them
  (`tagent doctor` enforces this).
- **Intraday realized P&L** in the daily-loss check. The gate currently sees
  unrealized P&L plus closed-lot totals; a day of many round trips is measured
  slightly conservatively.
- **The secondary news/politics agent.** The architecture is designed for it
  (the intelligence layer proposes into the same risk gate) but no data feeds
  are wired yet.
