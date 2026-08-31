#!/usr/bin/env bash
# One-shot VM setup. Run once on a fresh Debian/Ubuntu box, as a normal user
# with sudo.
#
# What this does:   packages, clone, venv, data dirs, env-file scaffold, wrapper
# What it does NOT: authorize Robinhood, install cron, or place a single order.
#
# The OAuth step needs your browser and your decision, so it stays manual by
# design. Nothing here can start trading on its own.
set -Eeuo pipefail

REPO="${REPO:-https://github.com/kutaygozalan/eng-edu.git}"
BRANCH="${BRANCH:-claude/robinhood-trading-bot-research-hrskq6}"
HOME_DIR="${TAGENT_HOME:-/opt/tagent}"
APP_DIR="$HOME_DIR/trading-agent"
ENV_FILE=/etc/tagent.env

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

say "Installing packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git curl

say "Fetching $BRANCH"
sudo mkdir -p "$HOME_DIR"
sudo chown "$USER" "$HOME_DIR"
if [[ -d "$HOME_DIR/.git" ]]; then
    git -C "$HOME_DIR" fetch origin "$BRANCH" && git -C "$HOME_DIR" checkout "$BRANCH"
    git -C "$HOME_DIR" pull --ff-only origin "$BRANCH"
else
    git clone --branch "$BRANCH" --single-branch "$REPO" "$HOME_DIR"
fi

say "Creating virtualenv"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -e "$APP_DIR"

say "Verifying the install"
"$APP_DIR/.venv/bin/pip" install --quiet pytest
( cd "$APP_DIR" && ".venv/bin/python" -m pytest tests/ -q )

say "Creating data directories"
sudo mkdir -p /data /var/log/tagent
sudo chown "$USER" /data /var/log/tagent
chmod 700 /data

if [[ ! -f "$HOME_DIR/config.yaml" ]]; then
    cp "$APP_DIR/config/config.small-account.yaml" "$HOME_DIR/config.yaml"
    say "Config written to $HOME_DIR/config.yaml (dry_run: true)"
else
    say "Keeping existing $HOME_DIR/config.yaml"
fi

# Never regenerate the token key over an existing one: it would render the
# stored Robinhood token permanently undecryptable.
if sudo test -f "$ENV_FILE"; then
    say "Keeping existing $ENV_FILE (not overwriting secrets)"
else
    KEY=$(python3 -c 'import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')
    sudo tee "$ENV_FILE" >/dev/null <<ENV
# Secrets for tagent. Read by the cron wrapper; never committed, never logged.
ANTHROPIC_API_KEY=REPLACE_ME
TAGENT_TOKEN_KEY=$KEY
# Optional: a Slack/Discord webhook for auth-expiry and failure alerts.
TAGENT_ALERT_WEBHOOK=
ENV
    sudo chmod 600 "$ENV_FILE"
    sudo chown "$USER" "$ENV_FILE"
    say "Wrote $ENV_FILE with a fresh token key"
fi

sudo cp "$APP_DIR/deploy/tagent-run" /usr/local/bin/tagent-run
sudo chmod +x /usr/local/bin/tagent-run

cat <<EOF

────────────────────────────────────────────────────────────────────────
Setup complete. Nothing is trading, and nothing will until you finish these.

 1. Add your Anthropic key:
      sudo sed -i 's|ANTHROPIC_API_KEY=REPLACE_ME|ANTHROPIC_API_KEY=sk-ant-...|' $ENV_FILE

 2. Authorize Robinhood (opens a URL you approve in YOUR browser):
      set -a && . $ENV_FILE && set +a
      $APP_DIR/.venv/bin/tagent --config $HOME_DIR/config.yaml auth

 3. Check what Robinhood actually exposes, and READ the output:
      tagent-run discover

 4. Sanity-check limits against your real balance:
      tagent-run doctor --equity <your balance>
      tagent-run health

 5. Schedule it:
      crontab $APP_DIR/deploy/crontab.example

Config is at $HOME_DIR/config.yaml with dry_run: true.
Leave it that way for a week. Review with: tagent-run status
────────────────────────────────────────────────────────────────────────
EOF
