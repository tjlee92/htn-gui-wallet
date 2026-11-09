#!/bin/sh
# Adds HTN GUI Wallet to your applications menu, for this user only. Nothing is copied or moved:
# the menu entry points at this folder, so keep the folder where it is (with htnwallet beside it).
# Run it again after moving the folder. To remove the entry: sh install.sh --remove
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
entry="$apps/htn-gui-wallet.desktop"

if [ "${1:-}" = "--remove" ]; then
  rm -f "$entry"
  echo "Removed the menu entry."
  exit 0
fi

mkdir -p "$apps"
cat > "$entry" <<EOF
[Desktop Entry]
Type=Application
Name=HTN GUI Wallet
Comment=Desktop wallet for Hoosat (HTN)
Exec="$here/guiwallet"
Path=$here
Icon=$here/guiwallet.png
Terminal=false
Categories=Finance;
EOF
chmod +x "$entry"
echo "Added HTN GUI Wallet to the applications menu."
