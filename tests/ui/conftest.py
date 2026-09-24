"""pytest-qt configuration for UI tests.

Forces the offscreen Qt platform so widget tests run on headless CI and dev
machines alike. Set *before* pytest-qt constructs the singleton
``QApplication`` (conftest import time).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
