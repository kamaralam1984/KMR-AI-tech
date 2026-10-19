#!/usr/bin/env bash
# OPTIONAL hardening for the scanner sandbox. Not run by anything in this repository.
#
# Docker's internal networks still let a container talk to the host's address on that bridge.
# This adds one firewall rule so the scanner browser container cannot start connections to the
# host (replies to connections the host starts, such as the worker talking to the browser, still
# work). It needs root, changes your firewall, and is lost on reboot unless you persist it.
#
#   sudo scripts/scanner-firewall.sh add      # install the rule
#   sudo scripts/scanner-firewall.sh remove   # take it out again
#   scripts/scanner-firewall.sh status        # show whether it is installed
set -euo pipefail

SUBNET="172.31.250.0/24"
GATEWAY="172.31.250.1"
RULE=(INPUT -s "$SUBNET" -d "$GATEWAY" -m conntrack --ctstate NEW -j DROP)

case "${1:-}" in
  add)    iptables -C "${RULE[@]}" 2>/dev/null || iptables -I "${RULE[@]}"; echo "rule installed" ;;
  remove) iptables -D "${RULE[@]}" 2>/dev/null && echo "rule removed" || echo "rule was not installed" ;;
  status) iptables -C "${RULE[@]}" 2>/dev/null && echo "installed" || echo "not installed" ;;
  *)      echo "usage: $0 add|remove|status" >&2; exit 2 ;;
esac
