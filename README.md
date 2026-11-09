# Hoosat GUI Wallet

A desktop interface for the Hoosat command-line tools. It's a set of forms on top of
`htnwallet` and `htnctl`, and contains no wallet logic of its own. Keys, signing and sending
are all done by `htnwallet`, unmodified.

Version 1.0.0. It creates and restores wallets, shows balances and addresses, sends, compounds,
and shows transaction history. Use it at your own risk: see the licence below.

## How it works

```
This app ──> htnwallet ──> wallet daemon ──> HTND node
        └──> htnctl ─────────────────────────> HTND node
```

- The app starts `htnwallet` and `htnctl` hidden, and only with the commands listed in
  [`core/tools.py`](src/guiwallet/core/tools.py).
- `htnctl` is limited to commands that only read information.
- The user enters the node to use. It must have `--utxoindex` turned on and its RPC port open
  to this computer.

## Running it

Put `guiwallet.exe` in the same folder as `htnwallet.exe` and `htnctl.exe`, or choose their
folder in Settings. The app looks only in those two places, never on the system `PATH`.

## Building it

You need Windows and Python 3.13.

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

This creates a private `.venv`, installs the pinned dependencies, runs the tests, and produces
`dist\guiwallet.exe`.

## Developing

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install --require-hashes -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m pytest
$env:PYTHONPATH = "src"; .venv\Scripts\python -m guiwallet
```

To change a dependency, edit `requirements.in` or `requirements-dev.in`, then regenerate the
pinned files with [pip-tools](https://pip-tools.readthedocs.io/):

```powershell
pip-compile --generate-hashes --allow-unsafe requirements.in
pip-compile --generate-hashes --allow-unsafe requirements-dev.in
```

## Layout

| Path | What's there |
|---|---|
| `src/guiwallet/core/` | Logic with no interface code: running tools, checking input, reading wallet output, settings |
| `src/guiwallet/ui/` | The window and screens |
| `tests/` | Automated tests |
| `build.ps1`, `packaging/` | Building the `.exe` |

## Security rules for contributors

- Never log or display command arguments, or the output of `dump-unencrypted-data`. They can
  contain passwords and recovery phrases.
- Never store a password or recovery phrase.
- Start other programs only through `core/tools.py`, and never through a shell.
- Add an `htnctl` command to the allowed list only if it can't change the node.

## Known limitation

The wallet password is passed to `htnwallet` on its command line, so other programs running
under the same Windows account, and any tools that log command lines, may be able to see it
while a send is running.

## Licence

Copyright (C) 2026 the Hoosat GUI Wallet contributors.

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU General Public License, version 3 or (at your option) any later version. It comes with
ABSOLUTELY NO WARRANTY. See [LICENSE](LICENSE) for the full text.

The built `.exe` includes third-party libraries under their own licences; see
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
