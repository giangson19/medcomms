# Setting up the MedComms demo

This guide sets up the MedComms research prototype for demos, at an address
such as `https://medcomms.example.org`. Plan about an hour the first time.
Afterwards, updates happen by themselves when code reaches `main`.

**What you will set up**

| Part | Where | Cost |
|---|---|---|
| Speaker page, user accounts, recordings | your Hostinger VM | the VM you already pay for |
| Translation engines (GPU) | [Modal](https://modal.com) | per second while awake; the free plan includes $30 a month (about 37 GPU-hours on an L4) |
| Automatic updates | GitHub Actions | free |

You will need: your laptop with this repository, the VM's IP address and root
password (from Hostinger's hPanel), and a domain or subdomain you can point at
the VM.

> Menu names on Modal, Hostinger and GitHub change from time to time; if a
> name below doesn't match exactly, look for the closest one.

---

## 1. Modal (on your laptop, ~15 minutes)

1. Create an account at [modal.com](https://modal.com).
2. In a terminal, in this repository's folder:
   ```bash
   source .venv/bin/activate      # if not already active
   make install                   # first time only
   modal setup                    # opens the browser to log in
   ```
3. **Your workspace name** is shown at the top left of the Modal dashboard
   (it is also the first part of your Modal web addresses). Note it.
4. **A proxy auth token**, so only your VM can use the engines: Modal dashboard
   → **Settings** → **Proxy Auth Tokens** → **New token**. Note the
   *Token ID* (starts with `wk-`) and the *Token Secret* (starts with `ws-`);
   the secret is shown once.
5. **An API token for GitHub**, so updates reach Modal: **Settings** →
   **API Tokens** → **New token**. Note its *ID* (`ak-…`) and *Secret* (`as-…`).
6. Turn on both directions to check it works:
   ```bash
   medcomms cloud start vi-en
   medcomms cloud start en-vi
   medcomms cloud status
   ```
   The first start builds the engine (about 10 minutes, once).

## 2. Your address (~5 minutes, then up to an hour to take effect)

At the company that manages your domain, add an **A record** pointing your
chosen name (e.g. `medcomms`) to the VM's IP address.

No domain? Use `<ip-with-dashes>.sslip.io` as your address — for the IP
`203.0.113.7` that is `203-0-113-7.sslip.io`. It works with HTTPS and needs
no setup.

## 3. The VM (~10 minutes)

1. In Hostinger's hPanel, if the VPS **firewall** is on, allow ports **80** and
   **443** (TCP).
2. From your laptop, in this repository's folder, copy the setup script to the
   VM and run it (use your VM's IP and your address):
   ```bash
   scp deploy/vm-setup.sh root@203.0.113.7:
   ssh root@203.0.113.7 bash vm-setup.sh medcomms.example.org
   ```
   If the VM already runs other websites, they are left alone: the script
   adds MedComms to the existing Caddy configuration. If port 8000 is taken,
   add a free port at the end, e.g. `... vm-setup.sh medcomms.example.org 8001`.
   At the end it prints the **sign-up code** for new users and three values for GitHub —
   keep that window open for step 4.
3. Give the VM the Modal details from step 1:
   ```bash
   ssh root@203.0.113.7
   nano /etc/medcomms/medcomms.env
   ```
   Fill in `MEDCOMMS_MODAL_WORKSPACE` (workspace name), `MODAL_KEY` (proxy
   token ID, `wk-…`) and `MODAL_SECRET` (proxy token secret, `ws-…`). Save with
   **Ctrl+O**, **Enter**, exit with **Ctrl+X**.

## 4. GitHub (~5 minutes)

In the repository on GitHub: **Settings** → **Secrets and variables** →
**Actions** → **New repository secret**. Add five secrets:

| Name | Value |
|---|---|
| `VM_HOST` | your address, e.g. `medcomms.example.org` |
| `VM_USER` | `medcomms` |
| `VM_SSH_KEY` | the key printed in step 3 (all lines, including `-----BEGIN…` and `-----END…`) |
| `MODAL_TOKEN_ID` | API token ID from step 1.5 (`ak-…`) |
| `MODAL_TOKEN_SECRET` | API token secret from step 1.5 (`as-…`) |

Then install the app on the VM for the first time: **Actions** → **Deploy** →
**Run workflow** → **Run workflow**. A green check (after a few minutes) means
it is live.

Open your address, choose **Create an account**, enter the sign-up code and
pick a username and password, then try a conversation.

---

## Before and after a demo

```bash
medcomms cloud start vi-en       # a few minutes before; first use wakes it up
medcomms cloud start en-vi
medcomms cloud status
# … demo …
medcomms cloud stop all          # optional: the engine also sleeps after 5 idle minutes
```

To try other models, start a direction again with options, e.g.
`medcomms cloud start en-vi --asr <speech model> --mt <translation model>`.
This redeploys the engine (both directions share it), so do it between
conversations. The other direction keeps its models. `medcomms cloud status`
shows which models each direction runs; the choice lives in the Modal
settings, not in this repository.

## Updates and rollback

- **Automatic:** whenever code reaches `main` (a merged pull request or a
  push) and the tests pass, GitHub updates the VM and any engines that are on.
  Watch it under **Actions**. Avoid merging during a live demo: the update
  restarts the front door, which ends conversations in progress.
- **Roll back:** **Actions** → **Deploy** → **Run workflow**, and enter the
  commit or tag that worked (find it under **Commits**).

## Downloading recordings

From your laptop:

```bash
scp -r root@203.0.113.7:/var/lib/medcomms/sessions ./sessions
```

See the [README](../README.md#recordings) for what each file contains.

## Accounts and the sign-up code

Share the sign-up code with new users; each creates an account on the
page. To change the code, or close sign-up once everyone has an account:

```bash
ssh root@203.0.113.7
nano /etc/medcomms/medcomms.env       # edit or empty MEDCOMMS_SIGNUP_CODE
systemctl restart medcomms            # existing accounts keep working
```

Managing accounts (forgotten passwords, removing someone):

```bash
ssh root@203.0.113.7
sudo -u medcomms -i
venv/bin/medcomms users list --data-dir /var/lib/medcomms
venv/bin/medcomms users reset user01 --data-dir /var/lib/medcomms
```

## When something is wrong

Open the page's **Connection Log** (at the bottom of the page) first.

| The log says | Likely cause | What to do |
|---|---|---|
| *… is not available right now* | that direction is off, or the VM's Modal settings are wrong | `medcomms cloud status`; check `/etc/medcomms/medcomms.env` |
| *Waking up the translation server* for minutes | first start after a long break or a model change | wait; the very first start downloads several GB |
| *… is in use* | someone else is using that direction | each direction serves one conversation at a time |
| *please sign in* | signed out (password reset or account disabled) | reload the page and sign in |

Logs:

```bash
ssh root@203.0.113.7 journalctl -u medcomms -f     # front door
modal app logs medcomms-engine                     # the engine
```
