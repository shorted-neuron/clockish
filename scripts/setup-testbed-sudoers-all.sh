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
        # Verify without side effects: sudo -n -l lists rules with no password when a
        # NOPASSWD entry exists.  (-l <command> is useless: it also says yes for
        # commands allowed only with a password.)
        if "${SSH[@]}" -o BatchMode=yes "$TARGET" "sudo -n -l" 2>/dev/null \
               | grep -q "NOPASSWD:.*systemctl restart $SVC"; then
            echo "  verified: NOPASSWD rule for systemctl restart $SVC is active"
        else
            echo "  NOT VERIFIED: no NOPASSWD rule for systemctl restart $SVC"; FAILED+=("$NAME")
        fi
    else
        echo "  FAILED"; FAILED+=("$NAME")
    fi
done 3<<<"$ROWS_TXT"

echo
if [[ ${#FAILED[@]} -gt 0 ]]; then echo "failed: ${FAILED[*]}"; exit 1; fi
echo "all hosts done"
