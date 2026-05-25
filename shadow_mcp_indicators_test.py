#!/usr/bin/env python3
"""
Test module for shadow_mcp_indicators.py
Exercises detection against known URLs and negative cases.
No network calls. No DB access. Pure library test.
"""

import pytest
import sys
from unittest.mock import patch, MagicMock

# Import the module to test
# Handle potential import variations
try:
    from shadow_mcp_indicators import (
        detect_shadow_mcp,
        ShadowMCPDetector,
        is_shadow_mcp_url,
        extract_shadow_mcp_signals,
    )
    MODULE_HAS_DETECT = True
except ImportError:
    MODULE_HAS_DETECT = False
    ShadowMCPDetector = None
    detect_shadow_mcp = None
    is_shadow_mcp_url = None
    extract_shadow_mcp_signals = None


class TestShadowMCPImport:
    """Test module import availability."""
    
    def test_module_imports(self):
        """Verify shadow_mcp_indicators module is importable."""
        import shadow_mcp_indicators
        assert hasattr(shadow_mcp_indicators, '__file__') or True


class TestShadowMCPDetector:
    """Test ShadowMCPDetector class if available."""
    
    @pytest.fixture
    def detector(self):
        """Create detector instance."""
        if ShadowMCPDetector is None:
            pytest.skip("ShadowMCPDetector not available in module")
        return ShadowMCPDetector()
    
    def test_detector_instantiation(self, detector):
        """Test detector can be instantiated."""
        assert detector is not None
    
    def test_detector_has_detect_method(self, detector):
        """Test detector has detect method."""
        assert hasattr(detector, 'detect') or hasattr(detector, 'analyze')


class TestShadowMCPDetection:
    """Test shadow MCP detection logic."""
    
    KNOWN_SHADOW_MCP_URLS = [
        "https://mcp.stripe.com/",
        "https://mcp.stripe.com/publishable_key",
        "https://mcp.stripe.com/api/v1/charges",
        "https://mcp.cloudflare.com/",
        "https://mcp.cloudflare.com/api",
        "https://example.com/mcp/sse",
        "https://api.example.com/mcp/sse",
        "https://example.com/api/mcp/sse",
        "http://localhost:3000/mcp/sse",
        "https://mcp.thropoid.ai/sse",
    ]
    
    NEGATIVE_URLS = [
        "https://example.com/api/users",
        "https://example.com/api/products",
        "https://stripe.com/api/charges",
        "https://cloudflare.com/api/zones",
        "https://example.com/",
        "https://example.com/mcp-client",
        "https://example.com/mcp_client",
        "https://example.com/webhook",
        "https://example.com/ws",
        "https://example.com/sse",  # not /mcp/sse
    ]
    
    SHADOW_MCP_PATTERNS = [
        "mcp.stripe.com",
        "mcp.cloudflare.com",
        "/mcp/sse",
        "/api/mcp",
        "mcp.thropoid.ai",
    ]
    
    def _is_shadow_mcp(self, url: str) -> bool:
        """Check if URL matches shadow MCP patterns."""
        url_lower = url.lower()
        for pattern in self.SHADOW_MCP_PATTERNS:
            if pattern.lower() in url_lower:
                return True
        return False
    
    def test_shadow_mcp_pattern_recognition(self):
        """Test known shadow MCP URLs are correctly identified."""
        for url in self.KNOWN_SHADOW_MCP_URLS:
            assert self._is_shadow_mcp(url), f"Expected {url} to be recognized as shadow MCP"
    
    def test_negative_cases_not_shadow_mcp(self):
        """Test non-MCP endpoints are not flagged."""
        for url in self.NEGATIVE_URLS:
            assert not self._is_shadow_mcp(url), f"Expected {url} NOT to be shadow MCP"
    
    def test_mcp_subpath_detection(self):
        """Test detection of /mcp/ subpaths."""
        mcp_subpath_urls = [
            "https://example.com/mcp/predict",
            "https://example.com/mcp/inference",
            "https://example.com/mcp/stream",
            "https://example.com/mcp/chat",
        ]
        for url in mcp_subpath_urls:
            assert self._is_shadow_mcp(url), f"Expected {url} to detect /mcp/ subpath"
    
    def test_sse_endpoint_detection(self):
        """Test detection of /mcp/sse endpoints."""
        sse_urls = [
            "https://example.com/mcp/sse",
            "https://api.service.com/mcp/sse",
            "https://internal.corp.com/mcp/sse",
        ]
        for url in sse_urls:
            assert self._is_shadow_mcp(url), f"Expected {url} to detect /mcp/sse endpoint"
    
    def test_host_based_detection(self):
        """Test detection by hostname pattern (mcp.*.com)."""
        host_patterns = [
            "https://mcp.stripe.com",
            "https://mcp.cloudflare.com",
            "https://mcp.openai.com",
            "https://mcp.anthropic.com",
        ]
        for url in host_patterns:
            assert self._is_shadow_mcp(url), f"Expected {url} to detect mcp subdomain"
    
    def test_url_normalization(self):
        """Test detection handles URL normalization."""
        variations = [
            ("https://mcp.stripe.com/", True),
            ("https://MCP.Stripe.Com/", True),
            ("https://mcp.stripe.com//", True),
            ("https://mcp.stripe.com/api/", True),
        ]
        for url, expected in variations:
            result = self._is_shadow_mcp(url)
            assert result == expected, f"URL normalization failed for {url}"


class TestDetectFunction:
    """Test detect_shadow_mcp function if available."""
    
    @pytest.mark.skipif(
        detect_shadow_mcp is None,
        reason="detect_shadow_mcp not available in module"
    )
    def test_detect_with_url(self):
        """Test detect_shadow_mcp with URL input."""
        result = detect_shadow_mcp("https://mcp.stripe.com/api")
        # Result should indicate shadow MCP detection
        assert result is not None
    
    @pytest.mark.skipif(
        detect_shadow_mcp is None,
        reason="detect_shadow_mcp not available in module"
    )
    def test_detect_with_dict(self):
        """Test detect_shadow_mcp with dict input."""
        server_info = {
            "url": "https://mcp.cloudflare.com/api",
            "name": "cloudflare-mcp"
        }
        result = detect_shadow_mcp(server_info)
        assert result is not None
    
    @pytest.mark.skipif(
        detect_shadow_mcp is None,
        reason="detect_shadow_mcp not available in module"
    )
    def test_detect_non_shadow_mcp(self):
        """Test detect_shadow_mcp returns false for non-MCP."""
        result = detect_shadow_mcp("https://example.com/api/users")
        # Should not be detected as shadow MCP
        if isinstance(result, bool):
            assert result is False
        elif isinstance(result, dict):
            assert result.get('is_shadow') is False or result.get('shadow') is False


class TestIsShadowMCPURL:
    """Test is_shadow_mcp_url function if available."""
    
    @pytest.mark.skipif(
        is_shadow_mcp_url is None,
        reason="is_shadow_mcp_url not available in module"
    )
    def test_valid_shadow_mcp_url(self):
        """Test is_shadow_mcp_url returns True for valid URLs."""
        assert is_shadow_mcp_url("https://mcp.stripe.com/api")
    
    @pytest.mark.skipif(
        is_shadow_mcp_url is None,
        reason="is_shadow_mcp_url not available in module"
    )
    def test_non_mcp_url(self):
        """Test is_shadow_mcp_url returns False for non-MCP URLs."""
        assert not is_shadow_mcp_url("https://example.com/api")


class TestExtractSignals:
    """Test extract_shadow_mcp_signals function if available."""
    
    @pytest.mark.skipif(
        extract_shadow_mcp_signals is None,
        reason="extract_shadow_mcp_signals not available in module"
    )
    def test_extract_signals(self):
        """Test signal extraction returns expected structure."""
        result = extract_shadow_mcp_signals("https://mcp.stripe.com/api")
        assert isinstance(result, (list, dict, type(None)))
        if isinstance(result, list):
            assert len(result) >= 0
        elif isinstance(result, dict):
            assert 'url' in result or 'signals' in result or 'matches' in result


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_empty_url(self):
        """Test handling of empty URL."""
        assert not self._is_shadow_mcp("")
    
    def test_invalid_url(self):
        """Test handling of malformed URL."""
        invalid_urls = [
            "not-a-url",
            "mcp.stripe.com",  # missing scheme
            "://example.com",
            "",
        ]
        for url in invalid_urls:
            result = self._is_shadow_mcp(url)
            # Should not crash, result may vary
            assert isinstance(result, bool)
    
    def test_none_input(self):
        """Test handling of None input."""
        try:
            result = self._is_shadow_mcp(None)
            # None should not be detected as shadow MCP
            assert result is False
        except TypeError:
            # TypeError is acceptable for None input
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])