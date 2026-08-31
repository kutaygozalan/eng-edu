#!/usr/bin/env bash
# Provision the trading-agent VM on GCP. Run this on YOUR machine, with YOUR
# gcloud login. It creates one instance and one firewall rule, nothing else.
#
# COST: the e2-micro itself is covered by the always-free tier in us-west1,
# us-central1 or us-east1, but the external IPv4 it needs for outbound calls is
# NOT — that is ~$0.005/hr, about $3.65/month. There is no way around it:
# without an external IP the VM has no internet egress, and Cloud NAT (the
# alternative) is roughly $32/month.
#
# SECURITY POSTURE
#   --no-service-account --no-scopes : the VM gets ZERO Google API access. It
#       never calls GCP, so a compromise cannot pivot into your project.
#   Shielded VM                       : secure boot, vTPM, integrity monitoring.
#   No inbound rules                  : the agent only makes outbound calls, so
#       nothing needs to reach it. SSH goes through IAP, which means port 22 is
#       never exposed to the internet.
set -Eeuo pipefail

PROJECT="${PROJECT:-}"
ZONE="${ZONE:-us-east1-b}"          # free-tier regions: us-east1, us-central1, us-west1
NAME="${NAME:-tagent}"
REPO="${REPO:-https://github.com/kutaygozalan/eng-edu.git}"
BRANCH="${BRANCH:-claude/robinhood-trading-bot-research-hrskq6}"

command -v gcloud >/dev/null || { echo "gcloud not found: https://cloud.google.com/sdk/docs/install" >&2; exit 1; }
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
[[ -n "$PROJECT" && "$PROJECT" != "(unset)" ]] || {
    echo "No project set. Run: gcloud config set project YOUR_PROJECT" >&2; exit 1; }

cat <<EOF

About to create, in project '$PROJECT':
  instance  $NAME  (e2-micro, debian-12, 30GB pd-standard) in $ZONE
  firewall  allow-iap-ssh-$NAME  (SSH from IAP range only, never 0.0.0.0/0)

The instance will have NO service account and NO API scopes.
Estimated cost: ~\$3.65/month for the external IP; the VM is free-tier.

EOF
read -r -p "Proceed? [y/N] " ok
[[ "$ok" == "y" || "$ok" == "Y" ]] || { echo "aborted"; exit 0; }

# The startup script runs once on first boot. It installs the agent but
# deliberately stops short of authorizing or scheduling anything - those need a
# human, and a VM that could start trading unattended is a bug, not a feature.
STARTUP=$(mktemp)
trap 'rm -f "$STARTUP"' EXIT
cat > "$STARTUP" <<EOS
#!/bin/bash
set -Eeuo pipefail
exec > >(tee -a /var/log/tagent-bootstrap.log) 2>&1
echo "=== tagent bootstrap \$(date -Is) ==="
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip curl
LOGIN_USER=\$(getent passwd 1000 | cut -d: -f1)
[[ -n "\$LOGIN_USER" ]] || LOGIN_USER=root
install -d -o "\$LOGIN_USER" /opt/tagent
sudo -u "\$LOGIN_USER" git clone --branch "$BRANCH" --single-branch "$REPO" /opt/tagent
sudo -u "\$LOGIN_USER" bash /opt/tagent/trading-agent/deploy/bootstrap.sh
echo "=== bootstrap done; awaiting manual 'tagent auth' ==="
EOS

gcloud compute instances create "$NAME" \
    --project="$PROJECT" --zone="$ZONE" \
    --machine-type=e2-micro \
    --image-family=debian-12 --image-project=debian-cloud \
    --boot-disk-size=30GB --boot-disk-type=pd-standard \
    --no-service-account --no-scopes \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --tags="$NAME" \
    --metadata-from-file=startup-script="$STARTUP" \
    --labels=purpose=trading-agent

gcloud compute firewall-rules create "allow-iap-ssh-$NAME" \
    --project="$PROJECT" --direction=INGRESS --action=ALLOW \
    --rules=tcp:22 --source-ranges=35.235.240.0/20 \
    --target-tags="$NAME" \
    --description="SSH via IAP only; the agent needs no other inbound access" \
    2>/dev/null || echo "(firewall rule already exists)"

cat <<EOF

Created. The startup script is installing the agent now (~2 minutes).

  Watch it:   gcloud compute ssh $NAME --zone $ZONE --tunnel-through-iap \\
                --command 'sudo tail -f /var/log/tagent-bootstrap.log'

  Then SSH:   gcloud compute ssh $NAME --zone $ZONE --tunnel-through-iap

Nothing is trading. Finish on the box:
  1. sudo sed -i 's|REPLACE_ME|sk-ant-...|' /etc/tagent.env
  2. set -a && . /etc/tagent.env && set +a
     /opt/tagent/trading-agent/.venv/bin/tagent --config /opt/tagent/config.yaml auth
  3. tagent-run discover     <- READ THIS OUTPUT
  4. tagent-run doctor --equity <balance> && tagent-run health
  5. crontab /opt/tagent/trading-agent/deploy/crontab.example

To delete everything:
  gcloud compute instances delete $NAME --zone $ZONE
  gcloud compute firewall-rules delete allow-iap-ssh-$NAME
EOF
