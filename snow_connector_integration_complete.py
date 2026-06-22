#!/usr/bin/env python3
"""
ServiceNow Connector Integration for ZO-SENTINEL Approval Workflow
Complete wiring as per Section 1 core loop step 5 and Appendix A
Built: 2026-04-16
"""

import hashlib
import hmac
import time
import json
import logging
import threading
import queue
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum
import urllib.request
import urllib.parse
import urllib.error
import ssl

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('snow_connector_integration')

# ============================================================================
# Configuration
# ============================================================================

@dataclass
class ServiceNowConfig:
    """ServiceNow OAuth and endpoint configuration"""
    instance_url: str = "https://your-instance.service-now.com"
    client_id: str = ""
    client_secret: str = ""
    oauth_token_url: str = ""
    webhook_secret: str = ""  # For HMAC signature validation
    scopes: List[str] = field(default_factory=lambda: ["incident.write", "oauth"])
    
    def __post_init__(self):
        if not self.oauth_token_url:
            self.oauth_token_url = f"{self.instance_url}/oauth_token.do"
        if not self.client_id:
            raise ValueError("client_id is required")
        if not self.client_secret:
            raise ValueError("client_secret is required")
        if not self.webhook_secret:
            raise ValueError("webhook_secret is required for signature validation")


@dataclass
class ZOSentinelConfig:
    """ZO-SENTINEL verdict API configuration"""
    api_url: str = "http://localhost:8080/api/v1"
    api_key: str = ""
    verdict_endpoint: str = "/verdicts"
    correlation_endpoint: str = "/correlate"


@dataclass
class WriteServiceConfig:
    """Write service DB configuration (port 8772)"""
    host: str = "localhost"
    port: int = 8772
    timeout: int = 30
    retry_attempts: int = 3


@dataclass
class ApprovalWorkflowConfig:
    """Approval workflow integration points"""
    workflow_api_url: str = "http://localhost:8080/api/v1/approval"
    feedback_endpoint: str = "/webhook/verdict"
    callback_url: str = "http://localhost:8080/api/v1/callback"


# ============================================================================
# Enums and Status
# ============================================================================

class VerdictType(Enum):
    APPROVE = "approve"
    DENY = "deny"
    ESCALATE = "escalate"
    PENDING = "pending"
    REVIEW = "review"


class TicketStatus(Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    DENIED = "denied"
    ESCALATED = "escalated"
    CLOSED = "closed"


class WebhookEventType(Enum):
    INCIDENT_CREATED = "incident.created"
    INCIDENT_UPDATED = "incident.updated"
    INCIDENT_ASSIGNED = "incident.assigned"
    APPROVAL_REQUESTED = "approval.requested"


# ============================================================================
# OAuth Token Management
# ============================================================================

@dataclass
class OAuthToken:
    """OAuth access token with expiration tracking"""
    access_token: str
    token_type: str
    expires_in: int
    refresh_token: Optional[str] = None
    issued_at: float = field(default_factory=time.time)
    scope: str = ""
    
    @property
    def expires_at(self) -> float:
        return self.issued_at + (self.expires_in - 60)  # 60 second buffer
    
    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


class OAuthTokenManager:
    """Manages ServiceNow OAuth token lifecycle with automatic refresh"""
    
    def __init__(self, config: ServiceNowConfig):
        self.config = config
        self._token: Optional[OAuthToken] = None
        self._lock = threading.RLock()
        self._token_file: Optional[str] = None
    
    def get_valid_token(self) -> str:
        """Get a valid OAuth token, refreshing if necessary"""
        with self._lock:
            if self._token is None or self._token.is_expired:
                self._refresh_token()
            return self._token.access_token
    
    def _refresh_token(self) -> None:
        """Refresh the OAuth token using client credentials"""
        logger.info("Refreshing ServiceNow OAuth token")
        
        data = urllib.parse.urlencode({
            'grant_type': 'client_credentials',
            'client_id': self.config.client_id,
            'client_secret': self.config.client_secret,
            'scope': ' '.join(self.config.scopes)
        }).encode('utf-8')
        
        try:
            request = urllib.request.Request(
                self.config.oauth_token_url,
                data=data,
                method='POST',
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Accept': 'application/json'
                }
            )
            
            with urllib.request.urlopen(request, timeout=30) as response:
                token_data = json.loads(response.read().decode('utf-8'))
                
                self._token = OAuthToken(
                    access_token=token_data['access_token'],
                    token_type=token_data.get('token_type', 'Bearer'),
                    expires_in=token_data.get('expires_in', 3600),
                    refresh_token=token_data.get('refresh_token'),
                    scope=token_data.get('scope', ''),
                    issued_at=time.time()
                )
                
                logger.info("OAuth token refreshed successfully")
                
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8') if e.fp else ""
            logger.error(f"OAuth token refresh failed: {e.code} - {error_body}")
            raise AuthenticationError(f"Failed to refresh OAuth token: {e.code}")
    
    def invalidate_token(self) -> None:
        """Force token invalidation (e.g., on 401 response)"""
        with self._lock:
            self._token = None
            logger.info("OAuth token invalidated")


# ============================================================================
# Signature Validation
# ============================================================================

class SignatureValidator:
    """Validates HMAC signatures on incoming webhooks - NEVER accepts unsigned webhooks"""
    
    def __init__(self, secret: str):
        self.secret = secret.encode('utf-8')
        self.algorithm = 'sha256'
    
    def validate(self, payload: bytes, signature: str, timestamp: Optional[str] = None) -> bool:
        """
        Validate webhook signature using HMAC-SHA256
        
        NEVER returns True for unsigned/invalid webhooks - security is paramount
        """
        if not signature:
            logger.warning("REJECTED: Webhook received without signature")
            return False
        
        if not payload:
            logger.warning("REJECTED: Empty payload received")
            return False
        
        # For ServiceNow webhooks, signature format may vary
        # Common format: "t={timestamp},v1={signature}"
        expected_signature = self._compute_signature(payload, timestamp)
        
        if not expected_signature:
            logger.warning("REJECTED: Could not compute expected signature")
            return False
        
        # Use constant-time comparison to prevent timing attacks
        if not hmac.compare_digest(signature, expected_signature):
            logger.warning("REJECTED: Signature mismatch")
            return False
        
        # Optional: Check timestamp to prevent replay attacks
        if timestamp:
            try:
                webhook_time = int(timestamp)
                current_time = int(time.time())
                if abs(current_time - webhook_time) > 300:  # 5 minute window
                    logger.warning(f"REJECTED: Timestamp outside acceptable window")
                    return False
            except ValueError:
                logger.warning("REJECTED: Invalid timestamp format")
                return False
        
        logger.debug("Signature validated successfully")
        return True
    
    def _compute_signature(self, payload: bytes, timestamp: Optional[str] = None) -> str:
        """Compute HMAC-SHA256 signature"""
        if timestamp:
            signed_payload = f"{timestamp}.".encode('utf-8') + payload
        else:
            signed_payload = payload
        
        signature = hmac.new(
            self.secret,
            signed_payload,
            hashlib.sha256
        ).hexdigest()
        
        return signature
    
    @staticmethod
    def compute_signature_sync(payload: str, secret: str, timestamp: Optional[str] = None) -> str:
        """Static method to compute signature for testing/verification"""
        encoder = SignatureValidator(secret)
        return encoder._compute_signature(payload.encode('utf-8'), timestamp)


# ============================================================================
# Write Service Client (Port 8772)
# ============================================================================

class WriteServiceClient:
    """Client for write_service at port 8772 for DB operations"""
    
    def __init__(self, config: WriteServiceConfig):
        self.config = config
        self._connection_pool: Dict[str, Any] = {}
    
    def save_ticket_correlation(self, correlation_id: str, ticket_data: Dict) -> bool:
        """Save correlation between ServiceNow ticket and ZO-SENTINEL verdict"""
        return self._execute_write(
            operation="INSERT",
            table="snow_sentinal_ticket_correlation",
            data={
                "correlation_id": correlation_id,
                "snow_ticket_number": ticket_data.get("number"),
                "snow_sys_id": ticket_data.get("sys_id"),
                "snow_incident_id": ticket_data.get("incident_id"),
                "verdict_id": ticket_data.get("verdict_id"),
                "verdict_type": ticket_data.get("verdict_type"),
                "verdict_confidence": ticket_data.get("verdict_confidence"),
                "correlation_timestamp": datetime.utcnow().isoformat(),
                "status": "pending",
                "metadata": json.dumps(ticket_data.get("metadata", {}))
            }
        )
    
    def update_correlation_status(self, correlation_id: str, status: str, 
                                   verdict_feedback: Optional[Dict] = None) -> bool:
        """Update correlation status after verdict is processed"""
        update_data = {
            "status": status,
            "last_updated": datetime.utcnow().isoformat()
        }
        
        if verdict_feedback:
            update_data["verdict_feedback"] = json.dumps(verdict_feedback)
            update_data["feedback_timestamp"] = datetime.utcnow().isoformat()
        
        return self._execute_write(
            operation="UPDATE",
            table="snow_sentinal_ticket_correlation",
            data=update_data,
            where={"correlation_id": correlation_id}
        )
    
    def save_webhook_event(self, event_type: str, payload: Dict, 
                           signature_valid: bool) -> Optional[str]:
        """Log webhook event for audit trail"""
        event_id = f"evt_{int(time.time() * 1000)}"
        
        result = self._execute_write(
            operation="INSERT",
            table="snow_webhook_events",
            data={
                "event_id": event_id,
                "event_type": event_type,
                "raw_payload": json.dumps(payload),
                "signature_valid": signature_valid,
                "received_at": datetime.utcnow().isoformat(),
                "processed": False,
                "error_message": None
            }
        )
        
        return event_id if result else None
    
    def mark_event_processed(self, event_id: str, success: bool, 
                              error_message: Optional[str] = None) -> bool:
        """Mark webhook event as processed"""
        return self._execute_write(
            operation="UPDATE",
            table="snow_webhook_events",
            data={
                "processed": success,
                "processed_at": datetime.utcnow().isoformat(),
                "error_message": error_message
            },
            where={"event_id": event_id}
        )
    
    def _execute_write(self, operation: str, table: str, data: Dict,
                       where: Optional[Dict] = None) -> bool:
        """Execute write operation against write_service"""
        payload = {
            "operation": operation,
            "table": table,
            "data": data,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        if where:
            payload["where"] = where
        
        for attempt in range(self.config.retry_attempts):
            try:
                # Using JSON-RPC or REST API to write_service
                request = urllib.request.Request(
                    f"http://{self.config.host}:{self.config.port}/write",
                    data=json.dumps(payload).encode('utf-8'),
                    method='POST',
                    headers={
                        'Content-Type': 'application/json',
                        'X-Service': 'snow_connector'
                    }
                )
                
                with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    if result.get('success'):
                        logger.debug(f"Write operation successful: {operation} on {table}")
                        return True
                    else:
                        logger.error(f"Write operation failed: {result.get('error')}")
                        return False
                        
            except urllib.error.URLError as e:
                logger.warning(f"Write attempt {attempt + 1} failed: {e}")
                if attempt == self.config.retry_attempts - 1:
                    logger.error(f"All write attempts exhausted for {table}")
                    return False
                time.sleep(0.5 * (attempt + 1))
        
        return False


# ============================================================================
# ZO-SENTINEL Verdict Client
# ============================================================================

class ZOSentinelVerdictClient:
    """Client for interacting with ZO-SENTINEL verdict API"""
    
    def __init__(self, config: ZOSentinelConfig):
        self.config = config
    
    def correlate_ticket_with_verdict(self, ticket_data: Dict) -> Optional[Dict]:
        """
        Correlate ServiceNow ticket with ZO-SENTINEL verdict
        As per Section 1 core loop step 5
        """
        correlation_payload = {
            "ticket_id": ticket_data.get("number"),
            "ticket_sys_id": ticket_data.get("sys_id"),
            "incident_id": ticket_data.get("incident_id"),
            "description": ticket_data.get("short_description", ""),
            "category": ticket_data.get("category"),
            "priority": ticket_data.get("priority"),
            "state": ticket_data.get("state"),
            "assigned_to": ticket_data.get("assigned_to"),
            "caller_id": ticket_data.get("caller_id"),
            "variables": ticket_data.get("variables", {}),
            "correlation_timestamp": datetime.utcnow().isoformat(),
            "source": "servicenow"
        }
        
        try:
            request = urllib.request.Request(
                f"{self.config.api_url}{self.config.correlation_endpoint}",
                data=json.dumps(correlation_payload).encode('utf-8'),
                method='POST',
                headers={
                    'Content-Type': 'application/json',
                    'X-API-Key': self.config.api_key,
                    'X-Correlation-Source': 'snow_connector'
                }
            )
            
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
                logger.info(f"Correlated ticket {ticket_data.get('number')} with verdict")
                return result
                
        except urllib.error.HTTPError as e:
            logger.error(f"Correlation failed: {e.code} - {e.read().decode('utf-8')}")
            return None
        except urllib.error.URLError as e:
            logger.error(f"Correlation request failed: {e}")
            return None
    
    def get_verdict(self, verdict_id: str) -> Optional[Dict]:
        """Retrieve a specific verdict by ID"""
        try:
            request = urllib.request.Request(
                f"{self.config.api_url}{self.config.verdict_endpoint}/{verdict_id}",
                method='GET',
                headers={
                    'X-API-Key': self.config.api_key
                }
            )
            
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
                
        except urllib.error.HTTPError as e:
            logger.error(f"Get verdict failed: {e.code}")
            return None
        except urllib.error.URLError as e:
            logger.error(f"Get verdict request failed: {e}")
            return None
    
    def submit_verdict_feedback(self, verdict_id: str, feedback: Dict) -> bool:
        """Submit verdict feedback to ZO-SENTINEL"""
        try:
            request = urllib.request.Request(
                f"{self.config.api_url}{self.config.verdict_endpoint}/{verdict_id}/feedback",
                data=json.dumps(feedback).encode('utf-8'),
                method='POST',
                headers={
                    'Content-Type': 'application/json',
                    'X-API-Key': self.config.api_key
                }
            )
            
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
                return result.get('success', False)
                
        except Exception as e:
            logger.error(f"Feedback submission failed: {e}")
            return False


# ============================================================================
# Approval Workflow Integration
# ============================================================================

class ApprovalWorkflowIntegrator:
    """Integrates ServiceNow connector with approval workflow system"""
    
    def __init__(self, config: ApprovalWorkflowConfig, 
                 write_service: WriteServiceClient):
        self.config = config
        self.write_service = write_service
    
    def process_approved_verdict(self, correlation_id: str, verdict: Dict,
                                  ticket_data: Dict) -> bool:
        """
        Process approved verdict and feed back to approval workflow
        As per Appendix A integration requirements
        """
        feedback_payload = {
            "correlation_id": correlation_id,
            "ticket_number": ticket_data.get("number"),
            "verdict_id": verdict.get("verdict_id"),
            "verdict_type": VerdictType.APPROVE.value,
            "verdict_confidence": verdict.get("confidence", 0.0),
            "verdict_reasoning": verdict.get("reasoning", ""),
            "risk_score": verdict.get("risk_score", 0),
            "policy_matches": verdict.get("policy_matches", []),
            "action": "approve",
            "timestamp": datetime.utcnow().isoformat(),
            "source": "servicenow_connector"
        }
        
        return self._send_workflow_feedback(feedback_payload)
    
    def process_denied_verdict(self, correlation_id: str, verdict: Dict,
                                ticket_data: Dict) -> bool:
        """Process denied verdict and feed back to approval workflow"""
        feedback_payload = {
            "correlation_id": correlation_id,
            "ticket_number": ticket_data.get("number"),
            "verdict_id": verdict.get("verdict_id"),
            "verdict_type": VerdictType.DENY.value,
            "verdict_confidence": verdict.get("confidence", 0.0),
            "verdict_reasoning": verdict.get("reasoning", ""),
            "risk_score": verdict.get("risk_score", 0),
            "policy_violations": verdict.get("policy_violations", []),
            "denial_reasons": verdict.get("denial_reasons", []),
            "action": "deny",
            "timestamp": datetime.utcnow().isoformat(),
            "source": "servicenow_connector"
        }
        
        return self._send_workflow_feedback(feedback_payload)
    
    def process_escalated_verdict(self, correlation_id: str, verdict: Dict,
                                   ticket_data: Dict) -> bool:
        """Process escalated verdict requiring human review"""
        feedback_payload = {
            "correlation_id": correlation_id,
            "ticket_number": ticket_data.get("number"),
            "verdict_id": verdict.get("verdict_id"),
            "verdict_type": VerdictType.ESCALATE.value,
            "escalation_reason": verdict.get("escalation_reason", "Manual review required"),
            "risk_score": verdict.get("risk_score", 0),
            "uncertainty_factors": verdict.get("uncertainty_factors", []),
            "action": "escalate",
            "timestamp": datetime.utcnow().isoformat(),
            "source": "servicenow_connector"
        }
        
        return self._send_workflow_feedback(feedback_payload)
    
    def _send_workflow_feedback(self, feedback: Dict) -> bool:
        """Send verdict feedback to approval workflow"""
        try:
            request = urllib.request.Request(
                f"{self.config.workflow_api_url}{self.config.feedback_endpoint}",
                data=json.dumps(feedback).encode('utf-8'),
                method='POST',
                headers={
                    'Content-Type': 'application/json',
                    'X-Source': 'snow_connector',
                    'X-Timestamp': datetime.utcnow().isoformat()
                }
            )
            
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
                if result.get('success'):
                    logger.info(f"Workflow feedback sent for {feedback.get('correlation_id')}")
                    return True
                else:
                    logger.error(f"Workflow rejected feedback: {result.get('error')}")
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to send workflow feedback: {e}")
            return False


# ============================================================================
# ServiceNow Connector
# ============================================================================

class ServiceNowConnector:
    """
    Main ServiceNow connector with full integration
    As per Section 1 core loop step 5 and Appendix A
    """
    
    def __init__(self, 
                 snow_config: ServiceNowConfig,
                 sentinel_config: ZOSentinelConfig,
                 write_config: WriteServiceConfig,
                 workflow_config: ApprovalWorkflowConfig):
        
        self.snow_config = snow_config
        self.oauth_manager = OAuthTokenManager(snow_config)
        self.signature_validator = SignatureValidator(snow_config.webhook_secret)
        self.write_service = WriteServiceClient(write_config)
        self.sentinel_client = ZOSentinelVerdictClient(sentinel_config)
        self.workflow_integrator = ApprovalWorkflowIntegrator(workflow_config, self.write_service)
        
        self._event_queue: queue.Queue = queue.Queue()
        self._running = False
        self._event_processor_thread: Optional[threading.Thread] = None
        
        # Register webhook handlers
        self._webhook_handlers: Dict[str, Callable] = {
            WebhookEventType.INCIDENT_CREATED.value: self._handle_incident_created,
            WebhookEventType.INCIDENT_UPDATED.value: self._handle_incident_updated,
            WebhookEventType.INCIDENT_ASSIGNED.value: self._handle_incident_assigned,
            WebhookEventType.APPROVAL_REQUESTED.value: self._handle_approval_requested
        }
    
    # ------------------------------------------------------------------------
    # Webhook Processing
    # ------------------------------------------------------------------------
    
    def process_webhook(self, payload: bytes, headers: Dict) -> Dict:
        """
        Main webhook entry point - validates signature BEFORE any processing
        NEVER accepts unsigned webhooks
        """
        event_type = headers.get('X-Webhook-Event', 'unknown')
        signature = headers.get('X-Signature', '')
        timestamp = headers.get('X-Timestamp', '')
        
        # CRITICAL: Validate signature FIRST before any processing
        if not self.signature_validator.validate(payload, signature, timestamp):
            logger.error(f"REJECTED UNSIGNED/INVALID WEBHOOK: event_type={event_type}")
            raise SecurityError("Invalid or missing webhook signature - rejected")
        
        # Parse payload after validation
        try:
            data = json.loads(payload.decode('utf-8'))
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse webhook payload: {e}")
            raise ValidationError(f"Invalid JSON payload: {e}")
        
        # Log event for audit (signature already validated)
        event_id = self.write_service.save_webhook_event(
            event_type=event_type,
            payload=data,
            signature_valid=True
        )
        
        # Process webhook asynchronously
        self._event_queue.put({
            'event_id': event_id,
            'event_type': event_type,
            'payload': data
        })
        
        return {
            'status': 'accepted',
            'event_id': event_id,
            'message': 'Webhook received and queued for processing'
        }
    
    def _process_event_queue(self) -> None:
        """Background thread to process webhook events"""
        while self._running:
            try:
                event = self._event_queue.get(timeout=1.0)
                
                try:
                    handler = self._webhook_handlers.get(event['event_type'])
                    if handler:
                        handler(event['payload'])
                        self.write_service.mark_event_processed(event['event_id'], True)
                    else:
                        logger.warning(f"No handler for event type: {event['event_type']}")
                        self.write_service.mark_event_processed(
                            event['event_id'], 
                            True,
                            f"No handler for event type: {event['event_type']}"
                        )
                except Exception as e:
                    logger.error(f"Event processing failed: {e}")
                    self.write_service.mark_event_processed(
                        event['event_id'],
                        False,
                        str(e)
                    )
                    
            except queue.Empty:
                continue
    
    # ------------------------------------------------------------------------
    # Event Handlers
    # ------------------------------------------------------------------------
    
    def _handle_incident_created(self, payload: Dict) -> None:
        """Handle new ServiceNow incident creation"""
        logger.info(f"Processing incident created: {payload.get('number')}")
        
        ticket_data = self._extract_ticket_data(payload)
        
        # Correlate with ZO-SENTINEL (Section 1 core loop step 5)
        correlation = self.sentinel_client.correlate_ticket_with_verdict(ticket_data)
        
        if correlation:
            # Save correlation to DB
            correlation_id = f"corr_{ticket_data.get('number')}_{int(time.time())}"
            self.write_service.save_ticket_correlation(correlation_id, {
                **ticket_data,
                'verdict_id': correlation.get('verdict_id'),
                'verdict_type': correlation.get('verdict_type'),
                'verdict_confidence': correlation.get('confidence', 0.0),
                'metadata': correlation
            })
            
            # Process verdict and feed back to workflow
            self._process_verdict(correlation_id, correlation, ticket_data)
        else:
            logger.warning(f"No correlation found for ticket {ticket_data.get('number')}")
    
    def _handle_incident_updated(self, payload: Dict) -> None:
        """Handle ServiceNow incident update"""
        logger.info(f"Processing incident updated: {payload.get('number')}")
        
        ticket_data = self._extract_ticket_data(payload)
        
        # Re-correlate for updated ticket
        correlation = self.sentinel_client.correlate_ticket_with_verdict(ticket_data)
        
        if correlation and correlation.get('verdict_changed'):
            correlation_id = f"corr_{ticket_data.get('number')}_update_{int(time.time())}"
            self.write_service.save_ticket_correlation(correlation_id, {
                **ticket_data,
                'verdict_id': correlation.get('verdict_id'),
                'verdict_type': correlation.get('verdict_type'),
                'verdict_confidence': correlation.get('confidence', 0.0),
                'metadata': correlation
            })
            
            self._process_verdict(correlation_id, correlation, ticket_data)
    
    def _handle_incident_assigned(self, payload: Dict) -> None:
        """Handle incident assignment events"""
        logger.info(f"Processing incident assigned: {payload.get('number')}")
        # Implementation as needed
        pass
    
    def _handle_approval_requested(self, payload: Dict) -> None:
        """Handle explicit approval requests from ServiceNow"""
        logger.info(f"Processing approval request: {payload.get('number')}")
        
        ticket_data = self._extract_ticket_data(payload)
        
        # Get fresh verdict for approval request
        correlation = self.sentinel_client.correlate_ticket_with_verdict(ticket_data)
        
        if correlation:
            correlation_id = f"corr_{ticket_data.get('number')}_approval_{int(time.time())}"
            self.write_service.save_ticket_correlation(correlation_id, {
                **ticket_data,
                'verdict_id': correlation.get('verdict_id'),
                'verdict_type': correlation.get('verdict_type'),
                'verdict_confidence': correlation.get('confidence', 0.0),
                'metadata': correlation
            })
            
            self._process_verdict(correlation_id, correlation, ticket_data)
    
    def _process_verdict(self, correlation_id: str, verdict: Dict, 
                         ticket_data: Dict) -> None:
        """
        Process verdict and feed back to approval workflow
        As per Appendix A
        """
        verdict_type = verdict.get('verdict_type', 'unknown')
        
        if verdict_type == VerdictType.APPROVE.value:
            self.workflow_integrator.process_approved_verdict(
                correlation_id, verdict, ticket_data
            )
            self.write_service.update_correlation_status(
                correlation_id, TicketStatus.APPROVED.value,
                {'action': 'approved', 'verdict': verdict}
            )
            
        elif verdict_type == VerdictType.DENY.value:
            self.workflow_integrator.process_denied_verdict(
                correlation_id, verdict, ticket_data
            )
            self.write_service.update_correlation_status(
                correlation_id, TicketStatus.DENIED.value,
                {'action': 'denied', 'verdict': verdict}
            )
            
        elif verdict_type == VerdictType.ESCALATE.value:
            self.workflow_integrator.process_escalated_verdict(
                correlation_id, verdict, ticket_data
            )
            self.write_service.update_correlation_status(
                correlation_id, TicketStatus.ESCALATED.value,
                {'action': 'escalated', 'verdict': verdict}
            )
    
    @staticmethod
    def _extract_ticket_data(payload: Dict) -> Dict:
        """Extract relevant ticket data from webhook payload"""
        return {
            'number': payload.get('ticket_number') or payload.get('number'),
            'sys_id': payload.get('sys_id'),
            'incident_id': payload.get('incident_id'),
            'short_description': payload.get('short_description'),
            'description': payload.get('description'),
            'category': payload.get('category'),
            'priority': payload.get('priority'),
            'state': payload.get('state'),
            'assigned_to': payload.get('assigned_to'),
            'caller_id': payload.get('caller_id'),
            'variables': payload.get('variables', {}),
            'raw_payload': payload
        }
    
    # ------------------------------------------------------------------------
    # Lifecycle Management
    # ------------------------------------------------------------------------
    
    def start(self) -> None:
        """Start the connector and background processing"""
        if self._running:
            logger.warning("Connector already running")
            return
        
        self._running = True
        self._event_processor_thread = threading.Thread(
            target=self._process_event_queue,
            daemon=True,
            name="snow_connector_event_processor"
        )
        self._event_processor_thread.start()
        logger.info("ServiceNow connector started")
    
    def stop(self) -> None:
        """Stop the connector gracefully"""
        self._running = False
        if self._event_processor_thread:
            self._event_processor_thread.join(timeout=5.0)
        logger.info("ServiceNow connector stopped")
    
    # ------------------------------------------------------------------------
    # ServiceNow API Operations (using OAuth)
    # ------------------------------------------------------------------------
    
    def get_incident(self, ticket_number: str) -> Optional[Dict]:
        """Get incident details using OAuth authentication"""
        token = self.oauth_manager.get_valid_token()
        
        try:
            request = urllib.request.Request(
                f"{self.snow_config.instance_url}/api/now/table/incident",
                method='GET',
                headers={
                    'Authorization': f'Bearer {token}',
                    'Accept': 'application/json'
                }
            )
            
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
                
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self.oauth_manager.invalidate_token()
            logger.error(f"Failed to get incident: {e.code}")
            return None
    
    def update_incident(self, sys_id: str, update_data: Dict) -> bool:
        """Update incident using OAuth authentication"""
        token = self.oauth_manager.get_valid_token()
        
        try:
            request = urllib.request.Request(
                f"{self.snow_config.instance_url}/api/now/table/incident/{sys_id}",
                data=json.dumps(update_data).encode('utf-8'),
                method='PATCH',
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                }
            )
            
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status in (200, 201)
                
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self.oauth_manager.invalidate_token()
            logger.error(f"Failed to update incident: {e.code}")
            return False
    
    def add_work_note(self, sys_id: str, note: str) -> bool:
        """Add work note to incident"""
        return self.update_incident(sys_id, {
            'work_notes': note
        })


# ============================================================================
# Custom Exceptions
# ============================================================================

class AuthenticationError(Exception):
    """OAuth authentication error"""
    pass


class SecurityError(Exception):
    """Security validation error (e.g., invalid signature)"""
    pass


class ValidationError(Exception):
    """Payload validation error"""
    pass


# ============================================================================
# Flask/HTTP Handler Integration
# ============================================================================

class SnowWebhookHandler:
    """HTTP handler for webhook endpoints"""
    
    def __init__(self, connector: ServiceNowConnector):
        self.connector = connector
    
    def handle_webhook(self, request) -> tuple:
        """
        Flask-compatible webhook handler
        Usage: 
            from flask import Flask, request, jsonify
            app = Flask(__name__)
            handler = SnowWebhookHandler(connector)
            app.add_url_rule('/webhook/servicenow', 'webhook', 
                           view_func=lambda: handler.handle_webhook(request))
        """
        try:
            payload = request.get_data()
            headers = {
                'X-Webhook-Event': request.headers.get('X-Webhook-Event', ''),
                'X-Signature': request.headers.get('X-Signature', ''),
                'X-Timestamp': request.headers.get('X-Timestamp', '')
            }
            
            result = self.connector.process_webhook(payload, headers)
            return json.dumps(result), 202
            
        except SecurityError as e:
            logger.error(f"Security violation: {e}")
            return json.dumps({'status': 'rejected', 'error': str(e)}), 401
            
        except ValidationError as e:
            logger.error(f"Validation error: {e}")
            return json.dumps({'status': 'rejected', 'error': str(e)}), 400
            
        except Exception as e:
            logger.error(f"Unexpected error processing webhook: {e}")
            return json.dumps({'status': 'error', 'error': str(e)}), 500


# ============================================================================
# CLI and Testing Utilities
# ============================================================================

def create_test_connector() -> ServiceNowConnector:
    """Create connector with test configuration"""
    snow_config = ServiceNowConfig(
        instance_url="https://test-instance.service-now.com",
        client_id="test_client_id",
        client_secret="test_client_secret",
        webhook_secret="test_webhook_secret_12345"
    )
    
    sentinel_config = ZOSentinelConfig(
        api_url="http://localhost:8080/api/v1",
        api_key="test_api_key"
    )
    
    write_config = WriteServiceConfig(
        host="localhost",
        port=8772
    )
    
    workflow_config = ApprovalWorkflowConfig()
    
    return ServiceNowConnector(
        snow_config=snow_config,
        sentinel_config=sentinel_config,
        write_config=write_config,
        workflow_config=workflow_config
    )


def test_signature_validation():
    """Test signature validation"""
    secret = "test_webhook_secret"
    payload = b'{"test": "data"}'
    timestamp = str(int(time.time()))
    
    # Compute valid signature
    valid_sig = SignatureValidator.compute_signature_sync(
        payload.decode('utf-8'), secret, timestamp
    )
    
    validator = SignatureValidator(secret)
    
    # Test valid signature
    assert validator.validate(payload, valid_sig, timestamp), "Valid signature should pass"
    
    # Test invalid signature
    assert not validator.validate(payload, "invalid_signature", timestamp), \
        "Invalid signature should fail"
    
    # Test missing signature - MUST NEVER ACCEPT
    assert not validator.validate(payload, "", timestamp), \
        "Missing signature MUST be rejected"
    
    # Test None signature - MUST NEVER ACCEPT
    assert not validator.validate(payload, None, timestamp), \
        "None signature MUST be rejected"
    
    print("✓ All signature validation tests passed")


def test_webhook_processing():
    """Test webhook end-to-end processing"""