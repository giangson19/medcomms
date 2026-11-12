#!/usr/bin/env bash
# One-time setup of an Ubuntu VM (e.g. Hostinger) as the MedComms front door.
#
#   On the VM, as root:   bash vm-setup.sh medcomms.example.org [PORT]
#
# Installs Caddy (automatic HTTPS) if needed and a `medcomms` service on
# 127.0.0.1:PORT (default 8000), creates the settings file
# /etc/medcomms/medcomms.env, and prints what to paste into GitHub. The app
# itself arrives with the first deploy from GitHub Actions.
#
# Other sites on the VM are left alone: an existing Caddyfile is extended,
# never replaced. Safe to run again: existing settings are kept.
#
# DEPLOY_PUBKEY="ssh-ed25519 ..." authorizes that key for deploys instead of
# generating a key pair and printing the private key.
set -euo pipefail

DOMAIN="${1:-}"
PORT="${2:-8000}"
if [[ -z "$DOMAIN" || $EUID -ne 0 ]]; then
  echo "Usage (as root): bash vm-setup.sh YOUR-DOMAIN [PORT]" >&2
  exit 1
fi
if ss -tln | grep -q "127.0.0.1:$PORT " && ! systemctl is-active --quiet medcomms; then
  echo "Port $PORT is already used by another program; choose another port." >&2
  exit 1
fi

HOME_DIR=/opt/medcomms
DATA_DIR=/var/lib/medcomms
ENV_FILE=/etc/medcomms/medcomms.env

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv curl gpg debian-keyring \
  debian-archive-keyring apt-transport-https
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q
  apt-get install -y -q caddy
fi

echo "==> Creating the medcomms user"
id -u medcomms >/dev/null 2>&1 \
  || useradd --system --create-home --home-dir "$HOME_DIR" --shell /bin/bash medcomms
install -d -o medcomms -g medcomms "$DATA_DIR"
[[ -d "$HOME_DIR/venv" ]] || sudo -u medcomms python3 -m venv "$HOME_DIR/venv"
# Deploys may restart the service, and nothing else.
echo "medcomms ALL=(root) NOPASSWD: /usr/bin/systemctl restart medcomms" \
  > /etc/sudoers.d/medcomms
chmod 440 /etc/sudoers.d/medcomms
visudo -cf /etc/sudoers.d/medcomms >/dev/null

echo "==> Settings"
install -d -m 755 /etc/medcomms
if [[ ! -f "$ENV_FILE" ]]; then
  SIGNUP_CODE=$(tr -dc 'a-z0-9' </dev/urandom | head -c 8 || true)
  cat > "$ENV_FILE" <<EOF
# MedComms settings. After editing: systemctl restart medcomms
# New users create their accounts with this code. Leave empty to close sign-up.
MEDCOMMS_SIGNUP_CODE=$SIGNUP_CODE
MEDCOMMS_DATA_DIR=$DATA_DIR
PORT=$PORT
# Your Modal workspace name and a Modal proxy auth token (see docs/DEPLOY.md).
MEDCOMMS_MODAL_WORKSPACE=
MODAL_KEY=
MODAL_SECRET=
EOF
  chmod 600 "$ENV_FILE"
fi

cat > /etc/systemd/system/medcomms.service <<EOF
[Unit]
Description=MedComms front door
After=network-online.target
Wants=network-online.target

[Service]
User=medcomms
EnvironmentFile=$ENV_FILE
WorkingDirectory=$DATA_DIR
# The port comes from PORT in the settings file.
ExecStart=$HOME_DIR/venv/bin/medcomms front --host 127.0.0.1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable medcomms >/dev/null

echo "==> HTTPS for $DOMAIN"
CADDYFILE=/etc/caddy/Caddyfile
SITE="$DOMAIN {
	encode zstd gzip
	reverse_proxy 127.0.0.1:$PORT
}"
if grep -q "^$DOMAIN {" "$CADDYFILE" 2>/dev/null; then
  echo "    $CADDYFILE already has a $DOMAIN block; leaving it as is."
  grep -A3 "^$DOMAIN {" "$CADDYFILE" | grep -q "127.0.0.1:$PORT" \
    || echo "    WARNING: that block does not point to 127.0.0.1:$PORT." >&2
else
  if [[ -f "$CADDYFILE" ]]; then
    cp "$CADDYFILE" "$CADDYFILE.before-medcomms.$(date +%Y%m%d%H%M%S)"
  fi
  if grep -q "/usr/share/caddy" "$CADDYFILE" 2>/dev/null; then
    printf '%s\n' "$SITE" > "$CADDYFILE"  # Caddy's placeholder page only
  else
    printf '\n%s\n' "$SITE" >> "$CADDYFILE"
  fi
  caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null
  systemctl reload caddy
fi
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
fi

echo "==> Deploy key for GitHub"
install -d -m 700 -o medcomms -g medcomms "$HOME_DIR/.ssh"
AUTHORIZED="$HOME_DIR/.ssh/authorized_keys"
if [[ -n "${DEPLOY_PUBKEY:-}" ]]; then
  grep -qxF "$DEPLOY_PUBKEY" "$AUTHORIZED" 2>/dev/null \
    || echo "$DEPLOY_PUBKEY" >> "$AUTHORIZED"
  KEY_TEXT="(already stored in GitHub)"
else
  KEY=$(mktemp -u)
  ssh-keygen -q -t ed25519 -N "" -C "github-deploy" -f "$KEY"
  cat "$KEY.pub" >> "$AUTHORIZED"
  KEY_TEXT=$(cat "$KEY")
  rm -f "$KEY" "$KEY.pub"
fi
chown medcomms:medcomms "$AUTHORIZED"
chmod 600 "$AUTHORIZED"

cat <<EOF

=====================================================================
 Done. Add these in GitHub: repository → Settings → Secrets and
 variables → Actions → New repository secret

   VM_HOST      $DOMAIN
   VM_USER      medcomms
   VM_SSH_KEY   (everything between the lines below, including them)

$KEY_TEXT

 Then fill in the Modal lines in $ENV_FILE
 (nano $ENV_FILE), and push to main or run the Deploy workflow.

 Sign-up code for new users: $(grep '^MEDCOMMS_SIGNUP_CODE=' "$ENV_FILE" | cut -d= -f2)
 (change it in $ENV_FILE, then: systemctl restart medcomms)
=====================================================================
EOF
