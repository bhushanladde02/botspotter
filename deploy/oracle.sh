#!/usr/bin/env bash
# Manual deploy/upgrade from a laptop. CI does the same thing on every push to
# main (.github/workflows/deploy.yml); use this when CI is unavailable or to
# deploy a branch.
#
#   deploy/oracle.sh                 # main, defaults below
#   GIT_REF=my-branch deploy/oracle.sh
#   VM=ubuntu@1.2.3.4 KEY=~/.ssh/other deploy/oracle.sh
set -euo pipefail
VM="${VM:-ubuntu@129.213.153.181}"
KEY="${KEY:-$HOME/.ssh/sharpen_vm}"
GIT_REF="${GIT_REF:-main}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "deploying $GIT_REF to $VM"
ssh -i "$KEY" "$VM" "GIT_REF=$GIT_REF bash -s" < "$HERE/remote.sh"
echo
echo "open https://botspotter.app"
