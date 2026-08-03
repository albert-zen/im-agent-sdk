from __future__ import annotations

import unittest
from re import escape

from imagent.interaction.channels.adapters.endpoints import validate_http_endpoint


class ChannelEndpointValidationTests(unittest.TestCase):
    def test_accepts_http_and_https_endpoints(self) -> None:
        validate_http_endpoint("http://localhost:8080", key="API base")
        validate_http_endpoint("https://api.example.test/v1", key="API base")

    def test_rejects_whitespace_and_non_http_urls(self) -> None:
        for value, message in (
            (" https://api.example.test", "must not contain whitespace"),
            ("ftp://api.example.test", "must be an HTTP(S) URL"),
            ("https://", "must be an HTTP(S) URL"),
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, escape(message)):
                    validate_http_endpoint(value, key="API base")

    def test_rejects_invalid_ports_and_embedded_credentials(self) -> None:
        for value, message in (
            ("https://api.example.test:not-a-port", "must be a valid HTTP(S) URL"),
            ("https://user:password@api.example.test", "must not contain userinfo"),
            ("https://api.example.test?token=secret", "must not contain query or fragment"),
            ("https://api.example.test#secret", "must not contain query or fragment"),
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, escape(message)):
                    validate_http_endpoint(value, key="API base")


if __name__ == "__main__":
    unittest.main()
