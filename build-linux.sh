#!/bin/sh
# Builds the Linux version, dist/htnwalletgui-amd64.tar.gz, inside Ubuntu 22.04 with Docker.
# Ubuntu 22.04 is old on purpose: the result then runs on Ubuntu 22.04, Debian 12, Mint 21, Fedora 36
# and newer. It can't go older: PySide6 6.12 needs glibc 2.34.
#
#   sh build-linux.sh
#
# Runs the tests first, the same as build.ps1. Needs Docker. Works from Linux or from Windows
# (Git Bash or WSL) with Docker Desktop.
set -eu
cd "$(dirname "$0")"
here="$(pwd -W 2>/dev/null || pwd)"  # Git Bash on Windows needs the Windows form of the path.
export MSYS_NO_PATHCONV=1

docker run --rm -v "$here:/src" -w /build ubuntu:22.04 sh -euc '
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends software-properties-common gpg-agent >/dev/null
  add-apt-repository -y ppa:deadsnakes/ppa >/dev/null
  apt-get install -y -qq --no-install-recommends python3.13 python3.13-venv libpython3.13 binutils     libgl1 libegl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libglib2.0-0 >/dev/null
  python3.13 -m venv /venv
  export PATH="/venv/bin:$PATH"
  cp -r /src/src /src/tests /src/packaging /src/requirements.txt /src/requirements-dev.txt \
    /src/pyproject.toml /src/LICENSE /src/THIRD-PARTY-NOTICES.md /src/README.md .
  python -m pip install -q --require-hashes -r requirements.txt -r requirements-dev.txt
  QT_QPA_PLATFORM=offscreen python -m pytest -q
  python -m PyInstaller --noconfirm --clean --distpath out --workpath work packaging/guiwallet.spec >/dev/null
  mkdir -p pack/htnwalletgui
  cp out/htnwalletgui LICENSE THIRD-PARTY-NOTICES.md pack/htnwalletgui/
  cp src/guiwallet/assets/icon-256.png pack/htnwalletgui/htnwalletgui.png
  cp packaging/linux/install.sh pack/htnwalletgui/
  chmod +x pack/htnwalletgui/htnwalletgui pack/htnwalletgui/install.sh
  mkdir -p /src/dist
  tar -C pack -czf /src/dist/htnwalletgui-amd64.tar.gz htnwalletgui
  chown "$(stat -c %u:%g /src)" /src/dist /src/dist/htnwalletgui-amd64.tar.gz
'
echo "Built dist/htnwalletgui-amd64.tar.gz"
