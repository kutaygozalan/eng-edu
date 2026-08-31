# Deployment runbook

Target: a small cloud VM running one-shot cycles on cron, 09:45–15:25 ET.

**Nothing in this document asks for your Robinhood username, password, MFA code,
or account number. It never will.** Authorization happens in your browser via
OAuth; the resulting token lands on the VM encrypted. Anyone who asks you to
paste Robinhood credentials into a config file or a chat window is wrong.

---

## 1. Provision the VM

Any of GCP `e2-small`, Hetzner CX22, or DigitalOcean's $6 droplet works. Debian
or Ubuntu.

**Give it 2GB of RAM, or add swap.** A free-tier `e2-micro` will OOM mid-cycle
during the model call, and a killed cycle is a missed signal:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Set the clock to UTC and let cron handle ET via `CRON_TZ` — mixing timezone
conversions across cron, the OS, and the code is how a bot ends up trading an
hour late twice a year.

## 2. Install

```bash
sudo apt-get update && sudo apt-get install -y python3-venv git
git clone <your-repo> /opt/tagent && cd /opt/tagent/trading-agent
python3 -m venv .venv && .venv/bin/pip install -e .

sudo mkdir -p /data /var/log/tagent
sudo chown $USER /data /var/log/tagent
cp config/config.small-account.yaml /opt/tagent/config.yaml
```

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

18 cycles a day at 20-minute spacing, a review at 17:30, a health check at 08:00.
Half-days are handled in code — cron still fires at 13:05 on those days and the
cycle exits immediately.

## 7. First week

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

- **Outcome reconciliation.** Orders are recorded and submitted, but nothing yet
  polls fills and writes the `outcomes` rows. Until that exists the learning
  loop has no inputs — expectancy, calibration and lessons all stay empty. This
  is the next thing to build, and the system does not actually learn without it.
- **The review's LLM step.** `tagent review` computes statistics, blocks failing
  setups and retires unsupported lessons today; the journal-writing call using
  `REVIEW_SYSTEM_PROMPT` is not yet wired.
- **A paper broker**, for testing the loop without touching a live account.
- **Options.** Deliberately out of scope until the account can size them.
