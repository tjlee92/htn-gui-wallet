"""The file PyInstaller builds from: the same start-up as `python -m guiwallet`."""

from guiwallet.app import main

raise SystemExit(main())
