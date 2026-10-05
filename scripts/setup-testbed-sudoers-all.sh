#!/usr/bin/env bash
# scripts/setup-testbed-sudoers-all.sh -- run setup-testbed-sudoers.sh on every
# testbed host from the control host.  Each host asks for your sudo password once.
#
#   bash scripts/setup-testbed-sudoers-all.sh [-i inventory] [-n] [host-or-group ...]
#
#   -i FILE  inventory (default: $CLOCKISH_TESTBED_INVENTORY or testbed-inventory.yaml)
#   -n       dry run: list what would be copied and run, touch nothing
#
# Needs a terminal: ssh -t gives sudo a tty to prompt on.  Safe to re-run; the
# rule file is rewritten each time.
set -uo pipefail
cd "$(dirname "$0")/.."

INV="${CLOCKISH_TESTBED_INVENTORY:-testbed-inventory.yaml}"
DRY=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        -i) INV="$2"; shift 2 ;;
        -n) DRY=true; shift ;;
        *)  break ;;
    esac
done
$DRY || [[ -t 0 ]] || { echo "needs a terminal: sudo prompts for a password on each host" >&2; exit 1; }

# One row per host: name|ssh-target|port|service  (reuses testbed.py's inventory rules)
ROWS_TXT="$(python3 - "$INV" "$@" <<'PY'
import sys
sys.path.insert(0, "scripts")
import testbed
hosts, groups = testbed.load_inventory(sys.argv[1])
for n in testbed.select(hosts, groups, sys.argv[2:]):
    v = hosts[n]
    print(f"{n}|{testbed.ssh_target(v, n)}|{v.get('ansible_port', '')}|{v['clockish_service']}")
PY
)" || exit 1
[[ -n "$ROWS_TXT" ]] || { echo "no hosts selected" >&2; exit 1; }

FAILED=()
while IFS='|' read -r NAME TARGET PORT SVC <&3; do
    echo
    echo "=== $NAME ($TARGET)"
    SSH=(ssh -o ConnectTimeout=10);  SCP=(scp -q -o ConnectTimeout=10)
    if [[ -n "$PORT" ]]; then SSH+=(-p "$PORT"); SCP+=(-P "$PORT"); fi
    REMOTE="/tmp/setup-testbed-sudoers.$$.sh"
    if $DRY; then
        echo "  ${SCP[*]} scripts/setup-testbed-sudoers.sh $TARGET:$REMOTE"
        echo "  ${SSH[*]} -t $TARGET bash $REMOTE $SVC"
        continue
    fi
    if "${SCP[@]}" scripts/setup-testbed-sudoers.sh "$TARGET:$REMOTE" \
       && "${SSH[@]}" -t "$TARGET" "bash $REMOTE $SVC; rc=\$?; rm -f $REMOTE; exit \$rc"; then
        # Independent check over a fresh, tty-less session: no cached sudo timestamp, so
        # sudo -n must work on the rule alone.  New file present, old one gone.
        CHECK="[ -e /etc/sudoers.d/099_$SVC ] || { echo 'no /etc/sudoers.d/099_$SVC'; exit 1; }; \
[ ! -e /etc/sudoers.d/$SVC-\$USER ] || { echo 'old /etc/sudoers.d/$SVC-'\$USER' still there'; exit 1; }; \
O=\$(sudo -n systemctl status $SVC 2>&1 >/dev/null); \
case \"\$O\" in *'password is required'*|*'not allowed'*) echo 'sudo -n still needs a password'; exit 1;; esac"
        if OUT="$("${SSH[@]}" -o BatchMode=yes "$TARGET" "$CHECK" 2>&1)"; then
            echo "  verified: 099_$SVC installed, old file gone, sudo -n works"
        else
            echo "  NOT VERIFIED: $OUT"; FAILED+=("$NAME")
        fi
    else
        echo "  FAILED"; FAILED+=("$NAME")
    fi
done 3<<<"$ROWS_TXT"

echo
if [[ ${#FAILED[@]} -gt 0 ]]; then echo "failed: ${FAILED[*]}"; exit 1; fi
echo "all hosts done"
