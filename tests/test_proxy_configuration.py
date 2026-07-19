import os
import unittest
from unittest.mock import patch

from yandex_scraper.config import _load_proxy_pool


class ProxyConfigurationTests(unittest.TestCase):
    def test_empty_pool_is_allowed_during_import(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_load_proxy_pool("TEST_PROXY"), [])

    def test_complete_pool_is_loaded_from_environment(self) -> None:
        values = {
            "TEST_PROXY_SERVER": "http://proxy.example.invalid:10000",
            "TEST_PROXY_USERNAME": "demo-user",
            "TEST_PROXY_PASSWORD": "demo-password",
        }
        with patch.dict(os.environ, values, clear=True):
            self.assertEqual(
                _load_proxy_pool("TEST_PROXY"),
                [
                    {
                        "server": values["TEST_PROXY_SERVER"],
                        "username": values["TEST_PROXY_USERNAME"],
                        "password": values["TEST_PROXY_PASSWORD"],
                    }
                ],
            )

    def test_partial_pool_fails_without_echoing_secret_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TEST_PROXY_SERVER": "http://proxy.example.invalid:10000",
                "TEST_PROXY_PASSWORD": "demo-password",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "TEST_PROXY_USERNAME"):
                _load_proxy_pool("TEST_PROXY")


if __name__ == "__main__":
    unittest.main()
