import importlib.util
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class AppStartupTests(unittest.TestCase):
    def test_defaults_to_demo_mode_and_port_5000_without_api_key(self):
        env = os.environ.copy()
        env.pop("JEV_MOCK", None)
        env.pop("TYPESAFE_API_KEY", None)
        env["PORT"] = "5000"

        original = {key: os.environ.get(key) for key in ("JEV_MOCK", "TYPESAFE_API_KEY", "PORT")}
        try:
            os.environ.pop("JEV_MOCK", None)
            os.environ.pop("TYPESAFE_API_KEY", None)
            os.environ["PORT"] = "5000"

            spec = importlib.util.spec_from_file_location("spendsense_app", ROOT / "app.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            self.assertTrue(module.MOCK)
            self.assertEqual(module.PORT, 5000)
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
