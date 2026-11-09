# Third-party notices

The built `guiwallet.exe` includes the following software. Each is under its own licence,
not this project's.

| Component | Licence | Source |
|---|---|---|
| Qt for Python (PySide6, Shiboken6) and the Qt libraries it bundles | LGPL-3.0 | https://code.qt.io/cgit/pyside/pyside-setup.git |
| psutil | BSD-3-Clause | https://github.com/giampaolo/psutil |
| segno | BSD-3-Clause | https://github.com/heuer/segno |
| Python | PSF License | https://www.python.org |
| PyInstaller bootloader | GPL-2.0-or-later with a bootloader exception | https://github.com/pyinstaller/pyinstaller |

## Qt / PySide6 (LGPL-3.0)

Qt for Python is used unmodified. You may replace the bundled Qt and PySide6 libraries with
your own versions: the complete source of this program is published under the GPL-3.0, so you
can rebuild it against any compatible PySide6 with `build.ps1`. The LGPL-3.0 text is at
https://www.gnu.org/licenses/lgpl-3.0.txt.

## Wallet tools

`htnwallet.exe` and `htnctl.exe` are not part of this program and are not bundled with it.
They come from the Hoosat project (HTND, ISC licence): https://github.com/HoosatNetwork/HTND
