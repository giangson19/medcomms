# Running a MedComms deployment

How a deployment set up with [DEPLOY.md](DEPLOY.md) is organised, and the
everyday tasks for whoever looks after it. Details specific to a particular
deployment (addresses, accounts, who has access, codes) belong in your
team's private notes, not in this repository.

## The parts

| Part | Runs on | Started by |
|---|---|---|
| Front door (`medcomms front`) | the VM, behind Caddy (HTTPS) | systemd service `medcomms` |
| Engine (`medcomms engine`) | Modal: one app, `medcomms-engine`, running every switched-on direction on one GPU | `medcomms cloud start` |
| Updates | GitHub Actions: `CI`, then `Deploy` | a push or merge to `main` |

## On the VM

| What | Where |
|---|---|
| Service | `medcomms` (systemd), runs as user `medcomms` |
| App | `/opt/medcomms/venv` (installed by the Deploy workflow) |
| Settings | `/etc/medcomms/medcomms.env` (root only): sign-up code, port, Modal workspace and proxy token |
| User accounts | `/var/lib/medcomms/users.json` (password hashes only) and `secret.key` (signs sign-in cookies; deleting it signs everyone out) |
| Recordings | `/var/lib/medcomms/sessions/` (see the [README](../README.md#recordings)) |
| HTTPS | the site's block in `/etc/caddy/Caddyfile`, pointing to `127.0.0.1:<PORT>` |
| Deploy access | `/opt/medcomms/.ssh/authorized_keys` (the GitHub deploy key); the `medcomms` user may only run `systemctl restart medcomms` with sudo |

The VM may host other sites. When editing the shared Caddyfile, change only
the MedComms block, then check and apply:

```bash
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl reload caddy
```

Everyday commands (as root):

```bash
systemctl status medcomms              # is it running?
journalctl -u medcomms -f              # live log (no transcripts are logged)
systemctl restart medcomms             # after editing the settings file
du -sh /var/lib/medcomms/sessions      # space used by recordings
df -h /                                # space left (audio pauses below 2 GB)
```

Accounts and recordings (as the `medcomms` user, so file ownership stays
right):

```bash
sudo -u medcomms -i
venv/bin/medcomms users list --data-dir /var/lib/medcomms
venv/bin/medcomms sessions list --data-dir /var/lib/medcomms --since 2026-11-01
venv/bin/medcomms sessions export /tmp/sessions.zip --data-dir /var/lib/medcomms
```

## On Modal

| What | Name |
|---|---|
| Engine app | `medcomms-engine` |
| Engine address | `https://<workspace>--medcomms-engine.modal.run` (only reachable with the proxy token) |
| Model cache | volume `medcomms-cache` |
| Engine settings (directions, models, machine) | dict `medcomms-settings`, key `engine` (used by `medcomms cloud redeploy`) |

```bash
medcomms cloud status                  # what is on, with which models
modal app logs medcomms-engine         # the engine's log
modal workspace proxy-tokens list      # tokens that can reach the engines
```

## Secrets

Kept in two places; never in the repository or in chat.

| Secret | Kept in | Used for |
|---|---|---|
| Sign-up code | VM settings file | new users creating accounts |
| Modal proxy token (`MODAL_KEY`, `MODAL_SECRET`) | VM settings file | the front door reaching the engines |
| `VM_HOST`, `VM_USER`, `VM_SSH_KEY` | GitHub → Settings → Secrets → Actions | the Deploy workflow updating the VM |
| `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` | GitHub → Settings → Secrets → Actions | the Deploy workflow updating running engines |

To replace a leaked or departed person's token: create a new one, update it
where it is kept, then delete the old one (Modal:
`modal workspace proxy-tokens delete`; VM key: remove its line from
`authorized_keys`).

## Giving someone access

- **Code:** add them to the GitHub repository (or its organisation).
- **VM:** add their SSH *public* key to the server; prefer a personal account
  with sudo over a shared root login.
- **Modal:** invite them to the workspace so they use their own login.
- **Sign-up code and tokens:** share through a password manager.

## Updates, rollback and recordings

See [DEPLOY.md](DEPLOY.md#updates-and-rollback). In short: merging to `main`
deploys after the tests pass (avoid merging during a demo); roll back with
**Actions → Deploy → Run workflow** and an older commit. Recordings contain
participants' voices: download them only to approved storage, and delete them
when the study's data plan says so.
