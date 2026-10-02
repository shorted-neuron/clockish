#!/usr/bin/env python3
"""scripts/testbed.py -- drive clockish on N test platforms over SSH.

Per host: check out a git branch, point clockish at a config (and display
profile), restart the service.  Hosts, groups and per-host attributes come
from an Ansible-style YAML inventory (see scripts/testbed-inventory.example.yaml).

    python3 scripts/testbed.py deploy                 # current branch, all hosts
    python3 scripts/testbed.py deploy st7789 pi-a     # hosts and/or groups
    python3 scripts/testbed.py deploy -b sync-seconds --pip
    python3 scripts/testbed.py deploy -r 4896f2f      # any commit/tag, detached
    python3 scripts/testbed.py status | start | stop | restart [pattern]
    python3 scripts/testbed.py deploy --dry-run       # show the remote script

How the config switch works without root: the systemd unit must run
    clockish ~/.config/clockish/clockish-config.yaml
(what `./run-clockish.sh --install-service` writes when given no config arg).
deploy repoints that path, and ~/.config/clockish/display.yaml, with symlinks
into the checkout.  Restarting uses `sudo -n systemctl`; see
scripts/setup-testbed-sudoers.sh for the passwordless rule.

Host vars (inventory): ansible_host, ansible_user, ansible_port,
clockish_path (default ~/clockish), clockish_service (default clockish),
clockish_config (repo-relative, required for deploy),
clockish_display (repo-relative, optional).
"""
import argparse
import concurrent.futures
import os
import shlex
import subprocess
import sys

import yaml

DEFAULTS = {
    'clockish_path': '~/clockish',
    'clockish_service': 'clockish',
}
SSH_OPTS = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8']
CONFIG_LINK = '.config/clockish/clockish-config.yaml'
DISPLAY_LINK = '.config/clockish/display.yaml'


# --------------------------------------------------------------------------
# inventory
# --------------------------------------------------------------------------
def _walk_group(name, node, parents_vars, hosts, groups):
    """Collect hosts of group `name`; later (inner) vars override outer."""
    node = node or {}
    gvars = {**parents_vars, **(node.get('vars') or {})}
    members = set()
    for hname, hvars in (node.get('hosts') or {}).items():
        hosts.setdefault(hname, {})
        hosts[hname].update(gvars)
        hosts[hname].update(hvars or {})
        members.add(hname)
    for cname, cnode in (node.get('children') or {}).items():
        members |= _walk_group(cname, cnode, gvars, hosts, groups)
    groups[name] = groups.get(name, set()) | members
    return members


def load_inventory(path):
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        sys.exit(f"inventory not found: {path} (copy scripts/testbed-inventory.example.yaml, or pass -i)")
    hosts, groups = {}, {}
    # top-level keys are groups ('all' is the usual root)
    for gname, gnode in data.items():
        _walk_group(gname, gnode, {}, hosts, groups)
    groups['all'] = set(hosts)
    for h in hosts.values():
        for k, v in DEFAULTS.items():
            h.setdefault(k, v)
    return hosts, groups


def select(hosts, groups, patterns):
    if not patterns:
        return sorted(hosts)
    chosen = set()
    for pat in patterns:
        if pat in groups:
            chosen |= groups[pat]
        elif pat in hosts:
            chosen.add(pat)
        else:
            sys.exit(f"no host or group named {pat!r}")
    return sorted(chosen)


# --------------------------------------------------------------------------
# remote scripts
# --------------------------------------------------------------------------
def _path(p):
    """Shell-quote a path, keeping a leading ~/ expandable."""
    if p == '~':
        return '"$HOME"'
    if p.startswith('~/'):
        return '"$HOME"/' + shlex.quote(p[2:])
    return shlex.quote(p)


def deploy_script(v, branch, pip, validate, ref=None):
    """Remote bash for `deploy`.  `ref` (a full SHA) wins over `branch`."""
    repo = _path(v['clockish_path'])
    svc = shlex.quote(v['clockish_service'])
    cfg = v.get('clockish_config')
    if not cfg:
        raise ValueError("clockish_config not set")
    lines = [
        'set -euo pipefail',
        f'cd {repo}',
        'test -z "$(git status --porcelain --untracked-files=no)" '
        '|| { echo "ERROR: dirty working tree on host" >&2; exit 3; }',
        'git fetch --quiet --tags origin',
    ]
    if ref:
        lines.append(f'git checkout --quiet --detach {shlex.quote(ref)} || '
                     f'{{ echo "ERROR: {ref[:12]} not on origin (unpushed?)" >&2; exit 6; }}')
    else:
        lines += [
            f'git checkout --quiet {shlex.quote(branch)}',
            f'git merge --ff-only --quiet origin/{shlex.quote(branch)}',
        ]
    if pip:
        lines.append('.venv/bin/pip install --quiet -e .')
    lines += [
        f'test -f {shlex.quote(cfg)} || {{ echo "ERROR: no {cfg} in this checkout" >&2; exit 4; }}',
        'mkdir -p "$HOME/.config/clockish"',
        # keep a one-time copy of a pre-existing real file, then repoint
        'link() { t="$HOME/$2"; '
        'if [ -e "$t" ] && [ ! -L "$t" ] && [ ! -e "$t.orig" ]; then mv "$t" "$t.orig"; fi; '
        'ln -sfn "$(pwd)/$1" "$t"; }',
        f'link {shlex.quote(cfg)} {CONFIG_LINK}',
    ]
    disp = v.get('clockish_display')
    if disp:
        lines.append(f'test -f {shlex.quote(disp)} || {{ echo "ERROR: no {disp}" >&2; exit 4; }}')
        lines.append(f'link {shlex.quote(disp)} {DISPLAY_LINK}')
    if validate:
        lines.append(f'.venv/bin/clockish-validate {shlex.quote(cfg)}')
    lines += [
        f'systemctl cat {svc} | grep -q "ExecStart=.*{CONFIG_LINK}" || '
        '{ echo "ERROR: unit does not run ~/' + CONFIG_LINK + '; run '
        './run-clockish.sh --install-service once on this host" >&2; exit 5; }',
        f'sudo -n systemctl restart {svc}',
        'sleep 2',
        f'systemctl is-active {svc}',
        'echo "HEAD=$(git rev-parse HEAD)"',
    ]
    return '\n'.join(lines) + '\n'


def service_script(v, action):
    svc = shlex.quote(v['clockish_service'])
    if action == 'status':
        return (f'cd {_path(v["clockish_path"])}\n'
                'echo "branch=$(git rev-parse --abbrev-ref HEAD) '
                'HEAD=$(git rev-parse --short HEAD)"\n'
                f'echo "config=$(readlink "$HOME/{CONFIG_LINK}" || echo "$HOME/{CONFIG_LINK}")"\n'
                f'echo "display=$(readlink "$HOME/{DISPLAY_LINK}" || echo "$HOME/{DISPLAY_LINK}")"\n'
                f'systemctl is-active {svc}\n')
    return f'set -e\nsudo -n systemctl {action} {svc}\nsystemctl is-active {svc} || true\n'


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------
def ssh_target(v, name):
    host = v.get('ansible_host', name)
    user = v.get('ansible_user')
    return f'{user}@{host}' if user else host


def run_host(name, v, script):
    cmd = ['ssh', *SSH_OPTS]
    if v.get('ansible_port'):
        cmd += ['-p', str(v['ansible_port'])]
    cmd += [ssh_target(v, name), 'bash -s']
    try:
        p = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return name, 124, 'timed out'
    return name, p.returncode, (p.stdout + p.stderr).strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('action', choices=['deploy', 'status', 'start', 'stop', 'restart'])
    ap.add_argument('patterns', nargs='*', help='host and/or group names (default: all)')
    ap.add_argument('-i', '--inventory',
                    default=os.environ.get('CLOCKISH_TESTBED_INVENTORY', 'testbed-inventory.yaml'))
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument('-b', '--branch', help='default: branch checked out here')
    grp.add_argument('-r', '--ref',
                     help='commit, tag or branch to check out detached (no merge); '
                          'resolved to a SHA here, so it must be on origin')
    ap.add_argument('--pip', action='store_true', help='pip install -e . after checkout')
    ap.add_argument('--no-validate', action='store_true', help='skip clockish-validate')
    ap.add_argument('-j', '--jobs', type=int, default=8)
    ap.add_argument('--dry-run', action='store_true', help='print remote scripts, run nothing')
    args = ap.parse_args()

    hosts, groups = load_inventory(args.inventory)
    names = select(hosts, groups, args.patterns)

    branch = args.branch
    ref_sha = None
    if args.action == 'deploy' and args.ref:
        try:
            ref_sha = subprocess.check_output(
                ['git', 'rev-parse', '--verify', '--quiet', f'{args.ref}^{{commit}}'],
                text=True).strip()
        except subprocess.CalledProcessError:
            sys.exit(f"cannot resolve ref {args.ref!r} here")
        branch = args.ref
    elif args.action == 'deploy' and not branch:
        branch = subprocess.check_output(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'], text=True).strip()
        if branch == 'HEAD':
            sys.exit("detached HEAD here; pass -b BRANCH")
    local_head = None
    if args.action == 'deploy':
        local_head = ref_sha or subprocess.check_output(
            ['git', 'rev-parse', branch], text=True).strip()
        print(f"deploying {branch} ({local_head[:8]}"
              f"{', detached' if ref_sha else ''}) to {len(names)} host(s)")

    jobs = {}
    for n in names:
        try:
            jobs[n] = (deploy_script(hosts[n], branch, args.pip, not args.no_validate, ref_sha)
                       if args.action == 'deploy' else service_script(hosts[n], args.action))
        except ValueError as e:
            print(f"[{n}] SKIP: {e}")
    if args.dry_run:
        for n, s in jobs.items():
            print(f"--- {n} ({ssh_target(hosts[n], n)}) ---\n{s}")
        return 0

    failed = [n for n in names if n not in jobs]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = [ex.submit(run_host, n, hosts[n], s) for n, s in jobs.items()]
        for f in concurrent.futures.as_completed(futs):
            n, rc, out = f.result()
            tag = 'ok' if rc == 0 else f'FAIL rc={rc}'
            print(f"[{n}] {tag}")
            for line in out.splitlines():
                print(f"    {line}")
            if rc != 0:
                failed.append(n)
            elif local_head and f'HEAD={local_head}' not in out:
                print(f"    WARN: host HEAD differs from local {branch} -- unpushed commits?")
    if failed:
        print(f"failed: {', '.join(sorted(failed))}")
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
