import unittest
from known_threats import (
    KNOWN_MALICIOUS_PACKAGES,
    KNOWN_MALICIOUS_DOMAINS,
    HIGH_RISK_PATTERNS,
    SUSPICIOUS_PERMISSIONS,
    check_package,
    check_domain,
)


class TestKnownThreats(unittest.TestCase):

    def test_known_malicious_packages_not_empty(self):
        """HIGH_RISK_PATTERNS must be non-empty."""
        self.assertGreater(len(HIGH_RISK_PATTERNS), 0)

    def test_suspicious_permissions_not_empty(self):
        """SUSPICIOUS_PERMISSIONS must be non-empty."""
        self.assertGreater(len(SUSPICIOUS_PERMISSIONS), 0)

    def test_high_risk_patterns_all_non_empty_strings(self):
        """Each pattern in HIGH_RISK_PATTERNS must be a non-empty string."""
        for pattern in HIGH_RISK_PATTERNS:
            self.assertIsInstance(pattern, str)
            self.assertGreater(len(pattern), 0)

    def test_known_malicious_packages_list_non_empty(self):
        """KNOWN_MALICIOUS_PACKAGES must be non-empty."""
        self.assertGreater(len(KNOWN_MALICIOUS_PACKAGES), 0)

    def test_known_malicious_domains_list_non_empty(self):
        """KNOWN_MALICIOUS_DOMAINS must be non-empty."""
        self.assertGreater(len(KNOWN_MALICIOUS_DOMAINS), 0)

    def test_check_package_with_known_malicious_names_returns_true(self):
        """check_package with known malicious names returns True."""
        malicious_packages = [
            "fake-postmark-mcp",
            "mcp-server-postmark-fake",
            "@mcp/server-postmark-clone",
            "mcp-whatsapp-stealer",
            "mcp-server-all",
            "@modelcontextprotocol/server-all",
        ]
        for pkg in malicious_packages:
            with self.subTest(package=pkg):
                result = check_package(pkg)
                self.assertTrue(result, f"Expected True for malicious package: {pkg}")

    def test_check_package_with_clean_names_returns_false(self):
        """check_package with clean names returns False."""
        clean_packages = [
            "safe-mcp-server",
            "hello-world-mcp",
            "filesystem-mcp",
            "@anthropic/mcp-sdk",
            "npx-mcp",
            "my-cool-tool",
        ]
        for pkg in clean_packages:
            with self.subTest(package=pkg):
                result = check_package(pkg)
                self.assertFalse(result, f"Expected False for clean package: {pkg}")

    def test_check_domain_with_suspicious_tlds_returns_true(self):
        """check_domain with suspicious TLDs returns True."""
        suspicious_domains = [
            "evil-mcp.io",
            "mcp-tools.ru",
            "mcpserver.xyz",
        ]
        for domain in suspicious_domains:
            with self.subTest(domain=domain):
                result = check_domain(domain)
                self.assertTrue(result, f"Expected True for suspicious domain: {domain}")

    def test_check_domain_with_clean_domains_returns_false(self):
        """check_domain with clean domains returns False."""
        clean_domains = [
            "github.com",
            "api.openai.com",
            "npmjs.org",
            "pypi.org",
            "registry.npmjs.org",
            "crates.io",
        ]
        for domain in clean_domains:
            with self.subTest(domain=domain):
                result = check_domain(domain)
                self.assertFalse(result, f"Expected False for clean domain: {domain}")


if __name__ == "__main__":
    unittest.main()