#!/usr/bin/env bash
# scripts/setup-testbed-sudoers.sh -- let the current user run
# `sudo systemctl start|stop|restart clockish` without a password, so
# scripts/testbed.py can drive this host over SSH.  Run once per test host
# (or from the control host for all of them: scripts/setup-testbed-sudoers-all.sh).
#
#   bash scripts/setup-testbed-sudoers.sh [service-name]
#
# Exact commands only, no wildcards: a wildcard in sudoers would let the user
# pass extra arguments.  status/is-active/journalctl need no sudo at all (members
# of adm can read the journal), so they are not in the rule.
set -euo pipefail
[[ "$EUID" -eq 0 ]] && { echo "run as your normal user, not root" >&2; exit 1; }

SVC="${1:-clockish}"
RULE_FILE="/etc/sudoers.d/${SVC}-${USER}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

cat > "$TMP" <<RULE
# Managed by clockish scripts/setup-testbed-sudoers.sh
${USER} ALL=(root) NOPASSWD: /usr/bin/systemctl start ${SVC}, /usr/bin/systemctl stop ${SVC}, /usr/bin/systemctl restart ${SVC}
RULE

# Validate before installing; a broken sudoers file can lock out sudo.
sudo visudo -cf "$TMP"
sudo install -m 0440 -o root -g root "$TMP" "$RULE_FILE"
sudo visudo -c
echo "Installed $RULE_FILE"
echo "Check: sudo -n systemctl restart $SVC && echo ok"
