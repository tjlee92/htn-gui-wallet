"""Reads the code packed inside a built exe, without running it, to see what it really contains."""

import sys
import types

from PyInstaller.archive.readers import CArchiveReader

archive = CArchiveReader(sys.argv[1])
pyz = archive.open_embedded_archive("PYZ.pyz") if "PYZ.pyz" in archive.toc else None
if pyz is None:
    name = next(n for n in archive.toc if n.lower().startswith("pyz"))
    pyz = archive.open_embedded_archive(name)


def constants(code):
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            yield from constants(value)
        else:
            yield value


def module(name):
    return pyz.extract(name)


blocked = [v for v in constants(module("guiwallet.core.tools")) if isinstance(v, frozenset) and "create" in v]
print("blocked commands in the exe:", [sorted(v) for v in blocked])
send = list(constants(module("guiwallet.core.send")))
print("limits in the exe (delay, to broadcast, reply):", [v for v in send if v in (60, 5) and isinstance(v, int)])
print("has send window:", "guiwallet.ui.send_dialog" in pyz.toc)
