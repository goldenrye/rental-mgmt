import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from app.config import Settings
from scripts.init_env import initialize_env


class ConfigurationSetupTests(unittest.TestCase):
    def test_setup_generates_private_secret_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.example").write_text(
                "JWT_SECRET_KEY=replace-this-with-a-long-random-secret\n"
                "DATABASE_URL=sqlite:///./data/rental_mgmt.db\n"
            )
            self.assertTrue(initialize_env(root))
            content = (root / ".env").read_text()
            secret = content.splitlines()[0].split("=", 1)[1]
            self.assertRegex(secret, r"^[0-9a-f]{64}$")
            self.assertEqual(stat.S_IMODE((root / ".env").stat().st_mode), 0o600)
            self.assertFalse(initialize_env(root))
            self.assertEqual((root / ".env").read_text(), content)

    def test_existing_configuration_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.example").write_text(
                "JWT_SECRET_KEY=replace-this-with-a-long-random-secret\n"
            )
            (root / ".env").write_text("CUSTOM_SETTING=keep-me\n")
            self.assertFalse(initialize_env(root))
            self.assertEqual((root / ".env").read_text(), "CUSTOM_SETTING=keep-me\n")

    def test_environment_path_is_anchored_to_project(self):
        expected = Path(__file__).resolve().parent.parent / ".env"
        self.assertEqual(Settings.model_config["env_file"], expected)
        self.assertTrue(expected.is_absolute())

    def test_environment_variable_overrides_file(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("JWT_SECRET_KEY=file-secret\n")
            with patch.dict(os.environ, {"JWT_SECRET_KEY": "environment-secret"}):
                self.assertEqual(
                    Settings(_env_file=env_file).jwt_secret_key, "environment-secret"
                )


if __name__ == "__main__":
    unittest.main()
