#!/usr/bin/env bash
# scripts/setup-testbed-sudoers.sh -- install /etc/sudoers.d/099_<service> so members
# of group `users` can run, without a password:
#   systemctl start|status|stop|restart|enable|disable <service>
#   reboot, shutdown -h now, halt
# scripts/testbed.py needs start|stop|restart; the rest is for managing the testbed
# by hand.  Run once per test host, or from the control host for all of them:
# scripts/setup-testbed-sudoers-all.sh.
#
#   bash scripts/setup-testbed-sudoers.sh [service-name]     # default: clockish
#
# Exact commands only, no wildcards (a wildcard would let the user pass extra
# arguments).  systemctl is /bin/systemctl: that is the real path on bookworm and
# resolves on trixie, where /bin is a symlink to usr/bin.
#
# Also removes /etc/sudoers.d/<service>-<user>, written by an earlier version of this
# script.  Idempotent: re-running rewrites the file.
set -euo pipefail
[[ "$EUID" -eq 0 ]] && { echo "run as your normal user, not root" >&2; exit 1; }

SVC="${1:-clockish}"
[[ "$SVC" =~ ^[A-Za-z][A-Za-z0-9_-]*$ ]] || { echo "bad service name: $SVC" >&2; exit 1; }
ALIAS="$(printf '%s' "$SVC" | tr 'a-z-' 'A-Z_')"      # sudoers aliases: A-Z 0-9 _
RULE_FILE="/etc/sudoers.d/099_${SVC}"
OLD_FILE="/etc/sudoers.d/${SVC}-${USER}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# Quoted heredoc keeps the trailing backslashes; placeholders are filled by sed.
sed -e "s/@SVC@/${SVC}/g" -e "s/@ALIAS@/${ALIAS}/g" > "$TMP" <<'RULE'
# Managed by clockish scripts/setup-testbed-sudoers.sh (testbed hosts).
# Let members of group "users" manage the "@SVC@" service and run basic power
# commands, without a password, so scripts/testbed.py can drive this host over SSH.

User_Alias @ALIAS@ = %users

Cmnd_Alias @ALIAS@_POWER = \
  /sbin/reboot, \
  /sbin/shutdown -h now, \
  /sbin/halt

Cmnd_Alias @ALIAS@_ACTIONS = \
  /bin/systemctl start @SVC@, \
  /bin/systemctl status @SVC@, \
  /bin/systemctl stop @SVC@, \
  /bin/systemctl restart @SVC@, \
  /bin/systemctl enable @SVC@, \
  /bin/systemctl disable @SVC@

@ALIAS@ ALL=(root) NOPASSWD: @ALIAS@_POWER, @ALIAS@_ACTIONS
RULE

# Validate before installing; a broken sudoers file can lock out sudo.
sudo visudo -cf "$TMP"
sudo install -m 0440 -o root -g root "$TMP" "$RULE_FILE"
if [[ -e "$OLD_FILE" ]]; then
    sudo rm -f "$OLD_FILE"
    echo "Removed old $OLD_FILE"
fi
sudo visudo -c
echo "Installed $RULE_FILE"

# Prove it: drop ALL cached sudo timestamps (the password just typed would mask a
# missing rule; Debian sets timestamp_type=global, so it is shared across sessions),
# then a passwordless call must not ask for one.  `status` exits non-zero for an
# inactive unit, so judge by the message, not the exit code.  No --no-pager: the rule
# allows exactly `status <service>`, and stdout is /dev/null so no pager starts.
sudo -K
# (capture first: with pipefail a failing sudo would make a `sudo | grep` test always false)
CHECK_OUT="$(sudo -n systemctl status "$SVC" 2>&1 >/dev/null || true)"
if grep -q -E 'password is required|not allowed' <<<"$CHECK_OUT"; then
    echo "NOT VERIFIED: sudo -n systemctl status $SVC still needs a password" >&2
    exit 1
fi
echo "Verified: sudo -n systemctl status $SVC works without a password"
