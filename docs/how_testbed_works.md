# Testbed: one branch, N displays

`scripts/testbed.py` drives clockish on several test platforms (different
displays, different configs) from one control host over SSH. Per host it checks
out a git branch, points clockish at a config and display profile, and restarts
the service.

## One-time setup

**Control host.** Key-based SSH to every target (`BatchMode=yes`: a password
prompt is a failure, not a hang). Copy the example inventory and edit:

```bash
cp scripts/testbed-inventory.example.yaml testbed-inventory.yaml   # gitignored
```

**Each target host.**

1. Clone the repo, run `bash install.sh`.
2. Install the service pointing at the stable config path, no config argument:
   ```bash
   ./run-clockish.sh --install-service
   ```
   The unit then runs `~/.config/clockish/clockish-config.yaml`. `deploy` swaps
   what that path points at, so the unit never needs rewriting (no root).
   `deploy` checks this and says so if the unit differs.
3. Passwordless start/stop/restart:
   ```bash
   bash scripts/setup-testbed-sudoers.sh
   ```
   Writes `/etc/sudoers.d/clockish-<user>`, validated with `visudo -cf` first.
   Exact commands only (`systemctl start|stop|restart clockish`), no wildcards. For every
   host at once, from the control host:
   `bash scripts/setup-testbed-sudoers-all.sh [-i inventory] [-n] [host-or-group ...]`.
   It copies the script and runs it with `ssh -t`, so each host prompts for your sudo
   password once; `-n` is a dry run. It re-checks each host with `sudo -n -l` afterwards.
   Reads (`is-active`, `status`, `journalctl`) need no sudo.
   Check: `ssh -T host 'sudo -n systemctl restart clockish && echo ok'`.

## Inventory

Ansible-style YAML. Precedence: `all.vars` < group vars < host vars. Groups nest
via `children`.

```yaml
all:
  vars:
    # ansible_user: bts       # omit if same as local user (or set in ~/.ssh/config)
    clockish_path: ~/clockish
  hosts:
    pi-st7789:
      ansible_host: pi-st7789.local
      clockish_config: configs/tiny.yaml
      clockish_display: configs/display/st7789-240x135.yaml
  children:
    spi:
      hosts:
        pi-st7789:
```

| var                | default      | meaning                                          |
|--------------------|--------------|--------------------------------------------------|
| `ansible_host`     | inventory name | address to SSH to                              |
| `ansible_user`     | (ssh default)  | remote user                                    |
| `ansible_port`     | (ssh default)  | SSH port                                       |
| `clockish_path`    | `~/clockish`   | checkout on the host (`~` = remote `$HOME`)    |
| `clockish_service` | `clockish`     | systemd unit name                              |
| `clockish_config`  | required for `deploy` | repo-relative layout config             |
| `clockish_display` | none           | repo-relative display profile (`configs/display/...`) |

## Usage

```bash
python3 scripts/testbed.py deploy                       # current branch, every host
python3 scripts/testbed.py deploy spi pi-dsi            # hosts and/or groups
python3 scripts/testbed.py deploy -b sync-seconds --pip # explicit branch, reinstall
python3 scripts/testbed.py deploy -r 4896f2f            # a commit or tag, detached
python3 scripts/testbed.py deploy --dry-run             # print remote scripts only
python3 scripts/testbed.py status|start|stop|restart [pattern]
```

`-r/--ref` (exclusive with `-b`) checks out a commit, tag or branch **detached**: it is
resolved to a full SHA on the control host, then `git checkout --detach <sha>` runs on
each host with no merge step. The SHA must be on `origin`, or the host fails with
`not on origin (unpushed?)`. A later `-b <branch>` deploy leaves detached HEAD again.

Options: `-i FILE` (or `CLOCKISH_TESTBED_INVENTORY`), `--no-validate`, `-j N`
parallel jobs (default 8).

`deploy` per host:

1. Fail if the checkout has uncommitted tracked changes.
2. `git fetch --tags origin`, then `git checkout <branch>` + `git merge --ff-only
   origin/<branch>`, or with `--ref`: `git checkout --detach <sha>`.
3. `pip install -e .` if `--pip`.
4. Symlink `~/.config/clockish/clockish-config.yaml` (and `display.yaml`) into
   the checkout. A pre-existing real file is kept once as `*.orig`.
5. `clockish-validate <config>`. Failure skips the restart on that host.
6. `sudo -n systemctl restart`, then `is-active`.
7. Warn if host HEAD differs from the local branch tip.

Hosts fetch from `origin`, not from the control host: **push first.** Exit code
is non-zero if any host failed; the failing hosts are listed at the end.

## Troubleshooting

- `sudo: a password is required` / `a terminal is required to read the password`:
  the NOPASSWD rule is missing, or the command doesn't match it exactly (user,
  service name, extra flags). Not a tty problem unless sudoers has `requiretty`
  (not set on Debian/Raspberry Pi OS; if present add `Defaults:<user> !requiretty`).
- `unit does not run ~/.config/clockish/clockish-config.yaml`: re-run
  `./run-clockish.sh --install-service` with no config argument on that host.
- `dirty working tree on host`: a test host has local edits. Commit or
  `git checkout -- .` there. `--ff-only` also fails on diverged local commits.
- `git fetch` fails on the host (`Permission denied (publickey)`) but works when you
  log in by hand: the host's `origin` is an SSH URL and your key has a passphrase.
  Your login session has an agent holding the unlocked key; the tool's
  non-interactive `ssh host 'bash -s'` (with `BatchMode=yes`) has none. Targets only
  fetch, so point `origin` at the public HTTPS URL:
  `git -C <clockish_path> remote set-url origin https://github.com/shorted-neuron/clockish.git`.
  Alternatives: `ForwardAgent yes` for that host in `~/.ssh/config` (lends your key to
  the host; only for hosts you control), or a read-only deploy key without a passphrase.
  Check what the tool sees: `ssh -o BatchMode=yes host 'cd <clockish_path> && git fetch --tags origin'`.
- Logs: `ssh host journalctl -u clockish -n 50` (user is in `adm`).
