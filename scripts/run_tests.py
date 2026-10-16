"""Run backend tests with an ephemeral encryption key and an isolated database."""

import base64
import os
import unittest
from pathlib import Path


if __name__ == "__main__":
    os.environ["DATA_ENCRYPTION_MASTER_KEY"] = base64.urlsafe_b64encode(os.urandom(32)).decode()
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    root = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(root), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
