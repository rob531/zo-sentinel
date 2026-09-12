"""
ZO-SENTINEL Python SDK Client

A Python SDK for interacting with ZO-SENTINEL enterprise InfoSec platform.
Importable by other tools: from sentinel_sdk import SentinelClient
"""

import requests
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

DEFAULT_BASE_URL = "http://localhost:8787"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8773/query"


@dataclass
class AssessmentResult:
    """Result of an MCP server assessment."""
    server_id: str
    name: str
    verdict: str
    trust_score: float
    risk_tier: str
    attestation_summary: str
    recommended_action: str


class SentinelClient:
    """
    Python SDK client for ZO-SENTINEL.
    
    Provides methods for assessing, searching, and managing MCP servers
    in the enterprise InfoSec platform.
    """
    
    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        """
        Initialize the SentinelClient.
        
        Args:
            base_url: Base URL for the ZO-SENTINEL API gateway.
                     Defaults to http://localhost:8787
        """
        self.base_url = base_url.rstrip('/')
        self._session = requests.Session()
        self._session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'ZO-SENTINEL-SDK/1.0'
        })
    
    def _post(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Internal helper for POST requests."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        response = self._session.post(url, json=data, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Internal helper for GET requests."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        response = self._session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def assess(self, mcp_name_or_url: str) -> AssessmentResult:
        """
        Submit an MCP server for assessment.
        
        Args:
            mcp_name_or_url: Name or URL of the MCP server to assess.
            
        Returns:
            AssessmentResult with server assessment details.
            
        Raises:
            requests.HTTPError: If the assessment request fails.
        """
        # First, try to get server info via search
        search_results = self.search(mcp_name_or_url, limit=5)
        
        # If server exists, get full assessment
        for result in search_results:
            if result.get('name') == mcp_name_or_url or result.get('url') == mcp_name_or_url:
                return self._build_assessment_result(result)
        
        # Server not found - check if it's a URL we can submit
        if self._is_url(mcp_name_or_url):
            job_id = self.submit_for_review(
                name=self._extract_name_from_url(mcp_name_or_url),
                url=mcp_name_or_url,
                description="SDK assessment request",
                requested_by="sentinel_sdk"
            )
            status = self.get_status(job_id)
            return self._build_assessment_result(status)
        
        # Return default not-found result
        return AssessmentResult(
            server_id="",
            name=mcp_name_or_url,
            verdict="UNKNOWN",
            trust_score=0.0,
            risk_tier="UNASSESSED",
            attestation_summary="Server not found in registry",
            recommended_action="Submit server for review using submit_for_review()"
        )
    
    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for MCP servers in the registry.
        
        Args:
            query: Search query (name, URL, or keywords).
            limit: Maximum number of results to return (default 10).
            
        Returns:
            List of matching server dictionaries.
        """
        try:
            result = self._post("api/search", {
                "query": query,
                "limit": limit
            })
            return result.get("results", [])
        except requests.HTTPError:
            # Fallback: try to query registry directly
            return self._search_via_registry(query, limit)
    
    def _search_via_registry(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Fallback search using registry API."""
        try:
            result = self._get("api/registry/search", params={
                "q": query,
                "limit": limit
            })
            return result.get("servers", [])
        except requests.HTTPError:
            return []
    
    def submit_for_review(
        self,
        name: str,
        url: str,
        description: str,
        requested_by: str
    ) -> str:
        """
        Submit an MCP server for review.
        
        Args:
            name: Name of the MCP server.
            url: URL of the MCP server.
            description: Description of the server.
            requested_by: Identifier of who is requesting the review.
            
        Returns:
            Job ID for tracking the review submission.
        """
        submission_data = {
            "name": name,
            "url": url,
            "description": description,
            "requested_by": requested_by
        }
        
        # Use write_service with 'rows' field (not 'row')
        write_payload = {
            "table": "assessment_queue",
            "rows": {
                "name": name,
                "url": url,
                "description": description,
                "requested_by": requested_by,
                "submitted_at": self._get_timestamp(),
                "status": "PENDING"
            }
        }
        
        response = self._session.post(
            WRITE_SERVICE_URL,
            json=write_payload,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            return result.get("id", result.get("job_id", ""))
        
        # Fallback: try direct submission endpoint
        result = self._post("api/submit", submission_data)
        return result.get("job_id", result.get("id", ""))
    
    def get_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get the status of a review job.
        
        Args:
            job_id: The job ID returned from submit_for_review.
            
        Returns:
            Dictionary with job status details.
        """
        try:
            return self._get(f"api/status/{job_id}")
        except requests.HTTPError:
            # Fallback: query assessment_queue table
            return self._get_job_from_queue(job_id)
    
    def _get_job_from_queue(self, job_id: str) -> Dict[str, Any]:
        """Fallback: get job status from queue table."""
        query_payload = {
            "sql": f"SELECT * FROM assessment_queue WHERE id = '{job_id}' OR job_id = '{job_id}' LIMIT 1"
        }
        try:
            response = self._session.post(
                QUERY_URL,
                json=query_payload,
                timeout=30
            )
            if response.status_code == 200:
                data = response.json()
                rows = data.get("rows", data.get("data", []))
                if rows:
                    return rows[0]
        except requests.HTTPError:
            pass
        return {"job_id": job_id, "status": "UNKNOWN", "message": "Job not found"}
    
    def is_trusted(self, mcp_name: str) -> bool:
        """
        Quick check if an MCP server is trusted.
        
        Args:
            mcp_name: Name of the MCP server to check.
            
        Returns:
            True if the server is trusted, False otherwise.
        """
        try:
            result = self._get("api/trust", params={"name": mcp_name})
            return result.get("trusted", False)
        except requests.HTTPError:
            # Fallback: search and check verdict
            search_results = self.search(mcp_name, limit=1)
            for result in search_results:
                if result.get("name") == mcp_name:
                    verdict = result.get("verdict", "").upper()
                    return verdict in ("TRUSTED", "APPROVED", "SAFE")
            return False
    
    def get_risk_tier(self, mcp_name: str) -> str:
        """
        Get the risk tier of an MCP server.
        
        Args:
            mcp_name: Name of the MCP server.
            
        Returns:
            Risk tier string: CRITICAL, HIGH, MEDIUM, LOW, or UNASSESSED.
        """
        try:
            result = self._get("api/risk", params={"name": mcp_name})
            return result.get("tier", "UNASSESSED")
        except requests.HTTPError:
            # Fallback: search and extract risk tier
            search_results = self.search(mcp_name, limit=1)
            for result in search_results:
                if result.get("name") == mcp_name:
                    return result.get("risk_tier", result.get("tier", "UNASSESSED"))
            return "UNASSESSED"
    
    def _is_url(self, value: str) -> bool:
        """Check if a string is a valid URL."""
        try:
            result = urlparse(value)
            return all([result.scheme, result.netloc])
        except Exception:
            return False
    
    def _extract_name_from_url(self, url: str) -> str:
        """Extract a name from a URL."""
        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')
        return path_parts[-1] if path_parts else parsed.netloc
    
    def _build_assessment_result(self, data: Dict[str, Any]) -> AssessmentResult:
        """Build AssessmentResult from server data dictionary."""
        return AssessmentResult(
            server_id=data.get("server_id", data.get("id", "")),
            name=data.get("name", ""),
            verdict=data.get("verdict", "UNKNOWN"),
            trust_score=float(data.get("trust_score", data.get("score", 0.0))),
            risk_tier=data.get("risk_tier", data.get("tier", "UNASSESSED")),
            attestation_summary=data.get("attestation_summary", data.get("attestation", "")),
            recommended_action=data.get(
                "recommended_action",
                data.get("action", self._get_action_for_verdict(data.get("verdict", "")))
            )
        )
    
    def _get_action_for_verdict(self, verdict: str) -> str:
        """Get recommended action based on verdict."""
        verdict_map = {
            "TRUSTED": "Server is approved for use.",
            "APPROVED": "Server is approved for use.",
            "PENDING": "Server is pending review.",
            "REJECTED": "Server is not approved. Do not use.",
            "UNKNOWN": "Server requires assessment."
        }
        return verdict_map.get(verdict.upper(), "Assessment required.")
    
    def _get_timestamp(self) -> str:
        """Get current ISO timestamp."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
    
    def health_check(self) -> bool:
        """
        Check if the ZO-SENTINEL service is healthy.
        
        Returns:
            True if the service is healthy, False otherwise.
        """
        try:
            result = self._get("health")
            return result.get("status") == "healthy" or result.get("healthy", False)
        except requests.HTTPError:
            return False
    
    def close(self):
        """Close the HTTP session."""
        self._session.close()
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False


# Convenience import
__all__ = ['SentinelClient', 'AssessmentResult']


if __name__ == "__main__":
    # Example usage
    import sys
    
    print("ZO-SENTINEL SDK Client")
    print("-" * 40)
    
    # Basic syntax check
    client = SentinelClient()
    print(f"Client initialized: {client.base_url}")
    
    # Verify methods exist
    methods = ['assess', 'search', 'submit_for_review', 'get_status', 'is_trusted', 'get_risk_tier']
    for method in methods:
        if hasattr(client, method):
            print(f"  ✓ {method}()")
        else:
            print(f"  ✗ {method}() - MISSING")
            sys.exit(1)
    
    print("\nSDK client ready for use.")
    print("Example: from sentinel_sdk import SentinelClient")