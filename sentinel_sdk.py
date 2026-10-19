import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772"
SERVICE_NAME = "sentinel_sdk"


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    response = requests.post(
        QUERY_SERVICE_URL, json=payload, timeout=30
    )
    response.raise_for_status()
    data = response.json()
    return data.get("rows", [])


def ws_write(
    table: str, rows: List[Dict[str, Any]], wait: bool = True
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"table": table, "rows": rows, "wait": wait}
    response = requests.post(
        WRITE_SERVICE_URL, json=payload, timeout=30
    )
    response.raise_for_status()
    return response.json()


def ws_execute(sql: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    response = requests.post(
        EXECUTE_SERVICE_URL, json=payload, timeout=30
    )
    response.raise_for_status()
    return response.json()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ServerSummary:
    server_id: str
    name: str
    url: Optional[str]
    trust_score: float
    verdict: str
    risk_tier: Optional[str]
    registry_source: Optional[str]
    last_seen: Optional[str]


@dataclass
class SignalScore:
    server_id: str
    signal_name: str
    score: float
    evidence: Optional[str]
    computed_at: str


@dataclass
class ThreatAssociation:
    server_id: str
    threat_type: str
    severity: str
    evidence: Optional[str]
    reported_at: str


@dataclass
class RiskRegisterEntry:
    server_id: str
    risk_tier: str
    risk_rank: Optional[int]
    threat_count: int
    computed_at: str


@dataclass
class AttestationRecord:
    server_id: str
    attested_by: str
    attestation_text: Optional[str]
    score: float
    expires_at: Optional[str]
    attested_at: str


@dataclass
class AuditEvent:
    event_id: str
    event_type: str
    actor: Optional[str]
    action: str
    target_server_id: Optional[str]
    details_json: Optional[str]
    outcome: str
    timestamp: str
    immutable: bool


class SentinelSDKError(Exception):
    pass


class SentinelClient:
    """
    Python SDK client for ZO-SENTINEL enterprise InfoSec platform.

    Provides methods for assessing, searching, and managing MCP servers.
    All database operations go through the write_service gateway.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8772",
        api_key: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("SENTINEL_API_KEY", "")
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "ZO-SENTINEL-SDK/1.0",
        })
        if self.api_key:
            self._session.headers["Authorization"] = f"Bearer {self.api_key}"

    def _post(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        response = self._session.post(url, json=data, timeout=30)
        response.raise_for_status()
        return response.json()

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        response = self._session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def list_servers(
        self,
        verdict: Optional[str] = None,
        risk_tier: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ServerSummary]:
        """
        List MCP servers from mcp_server_registry.

        Args:
            verdict: Filter by verdict (TRUSTED, AMBER, UNTRUSTED, UNKNOWN, KNOWN_THREAT)
            risk_tier: Filter by risk tier
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of ServerSummary objects
        """
        conditions = []
        params: Dict[str, Any] = {}
        if verdict:
            conditions.append("verdict = :verdict")
            params["verdict"] = verdict
        if risk_tier:
            conditions.append("risk_tier = :risk_tier")
            params["risk_tier"] = risk_tier

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        sql = f"""
            SELECT server_id, name, url, trust_score, verdict, risk_tier,
                   registry_source, last_seen
            FROM mcp_server_registry
            {where_clause}
            ORDER BY trust_score DESC NULLS LAST
            LIMIT :limit OFFSET :offset
        """
        params["limit"] = limit
        params["offset"] = offset

        try:
            rows = ws_query(sql, params)
            return [
                ServerSummary(
                    server_id=r["server_id"],
                    name=r.get("name") or "",
                    url=r.get("url"),
                    trust_score=float(r["trust_score"]) if r.get("trust_score") is not None else 0.0,
                    verdict=r.get("verdict") or "UNKNOWN",
                    risk_tier=r.get("risk_tier"),
                    registry_source=r.get("registry_source"),
                    last_seen=r.get("last_seen"),
                )
                for r in rows
            ]
        except Exception as e:
            log.error("list_servers failed: %s", e)
            raise SentinelSDKError(f"Failed to list servers: {e}") from e

    def get_server(self, server_id: str) -> Optional[ServerSummary]:
        """
        Get a single server by server_id.

        Args:
            server_id: The server identifier

        Returns:
            ServerSummary or None if not found
        """
        sql = """
            SELECT server_id, name, url, trust_score, verdict, risk_tier,
                   registry_source, last_seen
            FROM mcp_server_registry
            WHERE server_id = :server_id
        """
        try:
            rows = ws_query(sql, {"server_id": server_id})
            if not rows:
                return None
            r = rows[0]
            return ServerSummary(
                server_id=r["server_id"],
                name=r.get("name") or "",
                url=r.get("url"),
                trust_score=float(r["trust_score"]) if r.get("trust_score") is not None else 0.0,
                verdict=r.get("verdict") or "UNKNOWN",
                risk_tier=r.get("risk_tier"),
                registry_source=r.get("registry_source"),
                last_seen=r.get("last_seen"),
            )
        except Exception as e:
            log.error("get_server failed for %s: %s", server_id, e)
            raise SentinelSDKError(f"Failed to get server: {e}") from e

    def get_signal_scores(self, server_id: str) -> List[SignalScore]:
        """
        Get all signal scores for a server from mcp_signal_scores.

        Args:
            server_id: The server identifier

        Returns:
            List of SignalScore objects
        """
        sql = """
            SELECT server_id, signal_name, score, evidence, computed_at
            FROM mcp_signal_scores
            WHERE server_id = :server_id
            ORDER BY signal_name
        """
        try:
            rows = ws_query(sql, {"server_id": server_id})
            return [
                SignalScore(
                    server_id=r["server_id"],
                    signal_name=r["signal_name"],
                    score=float(r["score"]) if r.get("score") is not None else 0.0,
                    evidence=r.get("evidence"),
                    computed_at=r.get("computed_at") or utc_now_iso(),
                )
                for r in rows
            ]
        except Exception as e:
            log.error("get_signal_scores failed for %s: %s", server_id, e)
            raise SentinelSDKError(f"Failed to get signal scores: {e}") from e

    def get_threat_associations(self, server_id: str) -> List[ThreatAssociation]:
        """
        Get threat associations for a server from mcp_threat_associations.

        Args:
            server_id: The server identifier

        Returns:
            List of ThreatAssociation objects
        """
        sql = """
            SELECT server_id, threat_type, severity, evidence, reported_at
            FROM mcp_threat_associations
            WHERE server_id = :server_id
            ORDER BY reported_at DESC
        """
        try:
            rows = ws_query(sql, {"server_id": server_id})
            return [
                ThreatAssociation(
                    server_id=r["server_id"],
                    threat_type=r["threat_type"],
                    severity=r.get("severity") or "UNKNOWN",
                    evidence=r.get("evidence"),
                    reported_at=r.get("reported_at") or utc_now_iso(),
                )
                for r in rows
            ]
        except Exception as e:
            log.error("get_threat_associations failed for %s: %s", server_id, e)
            raise SentinelSDKError(f"Failed to get threat associations: {e}") from e

    def get_risk_register(self, server_id: str) -> Optional[RiskRegisterEntry]:
        """
        Get risk register entry for a server.

        Args:
            server_id: The server identifier

        Returns:
            RiskRegisterEntry or None
        """
        sql = """
            SELECT server_id, risk_tier, risk_rank, threat_count, computed_at
            FROM mcp_risk_register
            WHERE server_id = :server_id
        """
        try:
            rows = ws_query(sql, {"server_id": server_id})
            if not rows:
                return None
            r = rows[0]
            return RiskRegisterEntry(
                server_id=r["server_id"],
                risk_tier=r.get("risk_tier") or "UNKNOWN",
                risk_rank=r.get("risk_rank"),
                threat_count=int(r.get("threat_count") or 0),
                computed_at=r.get("computed_at") or utc_now_iso(),
            )
        except Exception as e:
            log.error("get_risk_register failed for %s: %s", server_id, e)
            raise SentinelSDKError(f"Failed to get risk register: {e}") from e

    def search_servers(
        self, query: str, limit: int = 50
    ) -> List[ServerSummary]:
        """
        Search servers by name or description.

        Args:
            query: Search term
            limit: Maximum results

        Returns:
            List of ServerSummary objects
        """
        sql = """
            SELECT server_id, name, url, trust_score, verdict, risk_tier,
                   registry_source, last_seen
            FROM mcp_server_registry
            WHERE name ILIKE :query OR description ILIKE :query
            ORDER BY trust_score DESC NULLS LAST
            LIMIT :limit
        """
        try:
            rows = ws_query(sql, {"query": f"%{query}%", "limit": limit})
            return [
                ServerSummary(
                    server_id=r["server_id"],
                    name=r.get("name") or "",
                    url=r.get("url"),
                    trust_score=float(r["trust_score"]) if r.get("trust_score") is not None else 0.0,
                    verdict=r.get("verdict") or "UNKNOWN",
                    risk_tier=r.get("risk_tier"),
                    registry_source=r.get("registry_source"),
                    last_seen=r.get("last_seen"),
                )
                for r in rows
            ]
        except Exception as e:
            log.error("search_servers failed: %s", e)
            raise SentinelSDKError(f"Failed to search servers: {e}") from e

    def get_attestations(self, server_id: str) -> List[AttestationRecord]:
        """
        Get attestations for a server from mcp_attestations.

        Args:
            server_id: The server identifier

        Returns:
            List of AttestationRecord objects
        """
        sql = """
            SELECT server_id, attested_by, attestation_text, score, expires_at, attested_at
            FROM mcp_attestations
            WHERE server_id = :server_id
            ORDER BY attested_at DESC
        """
        try:
            rows = ws_query(sql, {"server_id": server_id})
            return [
                AttestationRecord(
                    server_id=r["server_id"],
                    attested_by=r.get("attested_by") or "unknown",
                    attestation_text=r.get("attestation_text"),
                    score=float(r["score"]) if r.get("score") is not None else 0.0,
                    expires_at=r.get("expires_at"),
                    attested_at=r.get("attested_at") or utc_now_iso(),
                )
                for r in rows
            ]
        except Exception as e:
            log.error("get_attestations failed for %s: %s", server_id, e)
            raise SentinelSDKError(f"Failed to get attestations: {e}") from e

    def get_audit_trail(
        self,
        server_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditEvent]:
        """
        Get audit log entries from audit_log table.

        Args:
            server_id: Filter by target server
            event_type: Filter by event type
            limit: Maximum results

        Returns:
            List of AuditEvent objects
        """
        conditions = []
        params: Dict[str, Any] = {"limit": limit}
        if server_id:
            conditions.append("target_server_id = :server_id")
            params["server_id"] = server_id
        if event_type:
            conditions.append("event_type = :event_type")
            params["event_type"] = event_type

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        sql = f"""
            SELECT event_id, event_type, actor, action, target_server_id,
                   details_json, outcome, timestamp, immutable
            FROM audit_log
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT :limit
        """
        try:
            rows = ws_query(sql, params)
            return [
                AuditEvent(
                    event_id=r["event_id"],
                    event_type=r.get("event_type") or "unknown",
                    actor=r.get("actor"),
                    action=r.get("action") or "",
                    target_server_id=r.get("target_server_id"),
                    details_json=r.get("details_json"),
                    outcome=r.get("outcome") or "UNKNOWN",
                    timestamp=r.get("timestamp") or utc_now_iso(),
                    immutable=bool(r.get("immutable", False)),
                )
                for r in rows
            ]
        except Exception as e:
            log.error("get_audit_trail failed: %s", e)
            raise SentinelSDKError(f"Failed to get audit trail: {e}") from e

    def submit_server(
        self,
        name: str,
        url: str,
        description: Optional[str] = None,
        registry_source: str = "sdk",
    ) -> str:
        """
        Submit a new MCP server for assessment.
        Inserts into mcp_server_registry; signal_analyser picks it up asynchronously.

        Args:
            name: Server name
            url: Server URL
            description: Optional description
            registry_source: Source of registration (default: sdk)

        Returns:
            server_id (SHA256 of name+url)
        """
        import hashlib
        content = f"{name}|{url}".encode()
        server_id = hashlib.sha256(content).hexdigest()[:32]
        now = utc_now_iso()

        row = {
            "server_id": server_id,
            "name": name,
            "url": url,
            "description": description or "",
            "registry_source": registry_source,
            "first_seen": now,
            "last_seen": now,
            "trust_score": None,
            "verdict": "UNKNOWN",
        }
        try:
            ws_write("mcp_server_registry", [row])
            log.info("Submitted server %s (%s)", name, server_id)
            return server_id
        except Exception as e:
            log.error("submit_server failed: %s", e)
            raise SentinelSDKError(f"Failed to submit server: {e}") from e

    def record_audit_event(
        self,
        event_type: str,
        action: str,
        outcome: str,
        actor: Optional[str] = None,
        target_server_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Record an event in the audit_log.

        Args:
            event_type: Type of event
            action: Action performed
            outcome: Outcome (SUCCESS, FAILURE, BLOCKED)
            actor: Who performed the action
            target_server_id: Affected server
            details: Additional details as dict

        Returns:
            event_id
        """
        import hashlib
        import json

        now = utc_now_iso()
        content = f"{event_type}|{action}|{actor}|{target_server_id}|{now}".encode()
        event_id = hashlib.sha256(content).hexdigest()[:32]

        import secrets
        row = {
            "event_id": event_id,
            "event_type": event_type,
            "actor": actor,
            "action": action,
            "target_server_id": target_server_id,
            "details_json": json.dumps(details) if details else None,
            "outcome": outcome,
            "timestamp": now,
            "immutable": True,
        }
        try:
            ws_write("audit_log", [row])
            log.info("Recorded audit event %s: %s -> %s", event_id, action, outcome)
            return event_id
        except Exception as e:
            log.error("record_audit_event failed: %s", e)
            raise SentinelSDKError(f"Failed to record audit event: {e}") from e

    def health_check(self) -> Dict[str, Any]:
        """
        Check write_service health.

        Returns:
            Dict with service health info
        """
        try:
            sql = "SELECT service, last_heartbeat FROM service_health WHERE service = 'write_service'"
            rows = ws_query(sql)
            if rows:
                return {
                    "status": "ok",
                    "service": "write_service",
                    "last_heartbeat": rows[0].get("last_heartbeat"),
                }
            return {"status": "unknown", "service": "write_service"}
        except Exception as e:
            log.error("health_check failed: %s", e)
            return {"status": "error", "service": "write_service", "error": str(e)}

    def get_dashboard_summary(self) -> Dict[str, Any]:
        """
        Get a dashboard summary of registry state.

        Returns:
            Dict with counts and distributions
        """
        try:
            verdict_sql = """
                SELECT verdict, COUNT(*) as count
                FROM mcp_server_registry
                GROUP BY verdict
            """
            verdicts = ws_query(verdict_sql)

            risk_sql = """
                SELECT risk_tier, COUNT(*) as count
                FROM mcp_server_registry
                WHERE risk_tier IS NOT NULL
                GROUP BY risk_tier
            """
            risk_tiers = ws_query(risk_sql)

            total_sql = "SELECT COUNT(*) as total FROM mcp_server_registry"
            total_rows = ws_query(total_sql)
            total = total_rows[0]["total"] if total_rows else 0

            scored_sql = """
                SELECT COUNT(DISTINCT server_id) as scored
                FROM mcp_signal_scores
            """
            scored_rows = ws_query(scored_sql)
            scored = scored_rows[0]["scored"] if scored_rows else 0

            return {
                "total_servers": total,
                "scored_servers": scored,
                "verdict_distribution": {r["verdict"]: r["count"] for r in verdicts},
                "risk_tier_distribution": {r["risk_tier"]: r["count"] for r in risk_tiers},
            }
        except Exception as e:
            log.error("get_dashboard_summary failed: %s", e)
            raise SentinelSDKError(f"Failed to get dashboard summary: {e}") from e


def get_client(api_key: Optional[str] = None) -> SentinelClient:
    """
    Factory function to create a SentinelClient.

    Args:
        api_key: Optional API key; falls back to SENTINEL_API_KEY env var

    Returns:
        Configured SentinelClient instance
    """
    return SentinelClient(api_key=api_key)


if __name__ == "__main__":
    client = get_client()
    health = client.health_check()
    print(f"Sentinel SDK health: {health}")
    summary = client.get_dashboard_summary()
    print(f"Dashboard summary: {summary}")
    sys.exit(0)