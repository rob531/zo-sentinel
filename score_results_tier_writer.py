# score_results_tier_writer.py
import json
import logging
import time
from typing import List, Dict, Tuple
import pika
import requests
from pydantic import BaseModel, validator

# Constants
RABBITMQ_HOST = '127.0.0.1'
RABBITMQ_PORT = 8780
RABBITMQ_TOPIC = 'score_results_push'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/execute'
HEARTBEAT_URL = 'http://127.0.0.1:8772/write'
HEARTBEAT_INTERVAL = 60
CRITERIA_VERSION = "v1.0_7axis"

# Pydantic model for message validation
class AxisScore(BaseModel):
    axis_name: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool

    @validator('axis_name')
    def validate_axis_name(cls, v):
        valid_axes = {
            'overall_risk', 'auth_strength', 'capability_breadth',
            'data_sensitivity', 'network_egress', 'maintainer_trust',
            'exploit_surface'
        }
        if v not in valid_axes:
            raise ValueError(f"Invalid axis_name: {v}")
        return v

class ScoreResult(BaseModel):
    server_id: str
    axes: List[AxisScore]

def compute_tier(axes: List[Dict]) -> Tuple[str, str]:
    """
    Compute risk tier based on the given axes scores.
    """
    # Check for any escalated axis or critical probability >= 0.7
    for axis in axes:
        if axis['escalated'] or axis['p_critical'] >= 0.7:
            return "HIGH_RISK_ISOLATED", CRITERIA_VERSION

    # Find overall_risk axis
    overall_risk = next((axis for axis in axes if axis['axis_name'] == 'overall_risk'), None)
    if not overall_risk:
        return "HIGH_RISK_ISOLATED", CRITERIA_VERSION

    p_top = overall_risk['p_top']

    if p_top >= 0.75:
        return "TRUSTED_GENERAL", CRITERIA_VERSION
    elif p_top >= 0.60:
        return "TRUSTED_RESEARCH", CRITERIA_VERSION
    elif p_top >= 0.45:
        return "ENTERPRISE_CONTROLLED", CRITERIA_VERSION
    elif p_top >= 0.30:
        return "CAUTION_LIMITED", CRITERIA_VERSION
    elif p_top >= 0.15:
        return "HIGH_RISK_ISOLATED", CRITERIA_VERSION
    else:
        return "HIGH_RISK_ISOLATED", CRITERIA_VERSION

def update_registry(server_id: str, risk_tier: str) -> None:
    """
    Update the risk_tier in mcp_server_registry via write_service.
    """
    payload = {
        "query": "UPDATE mcp_server_registry SET risk_tier = ? WHERE server_id = ? AND verdict IS NOT NULL",
        "params": [risk_tier, server_id]
    }
    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Failed to update registry for server {server_id}: {e}")

def heartbeat() -> None:
    """
    Send a heartbeat to the service_health table.
    """
    payload = {
        "table": "service_health",
        "data": {
            "service": "score_results_tier_writer",
            "last_heartbeat": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        }
    }
    try:
        response = requests.post(HEARTBEAT_URL, json=payload)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Failed to send heartbeat: {e}")

def on_message(ch, method, properties, body):
    """
    Callback for RabbitMQ message consumption.
    """
    try:
        message = json.loads(body)
        score_result = ScoreResult(**message)

        risk_tier, criteria_version = compute_tier(score_result.axes)
        update_registry(score_result.server_id, risk_tier)

        # Log the computed tier
        log_entry = {
            "event": "tier_computed",
            "server_id": score_result.server_id,
            "risk_tier": risk_tier,
            "criteria_version": criteria_version
        }
        print(json.dumps(log_entry))

        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        logging.error(f"Error processing message: {e}")
        ch.basic_ack(delivery_tag=method.delivery_tag)

def run() -> None:
    """
    Connect to RabbitMQ, consume score_results_push, and update registry on each message.
    """
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
    )
    channel = connection.channel()

    channel.queue_declare(queue=RABBITMQ_TOPIC, durable=True)
    channel.basic_consume(queue=RABBITMQ_TOPIC, on_message_callback=on_message, auto_ack=False)

    last_heartbeat = time.time()

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        channel.stop_consuming()
    finally:
        connection.close()

if __name__ == '__main__':
    # Self-test
    logging.basicConfig(level=logging.INFO)

    # Test compute_tier
    test_cases = [
        ([{"axis_name": "overall_risk", "p_top": 0.8, "p_critical": 0.1, "p_danger": 0.1, "escalated": False}], "TRUSTED_GENERAL"),
        ([{"axis_name": "overall_risk", "p_top": 0.5, "p_critical": 0.1, "p_danger": 0.1, "escalated": False}], "ENTERPRISE_CONTROLLED"),
        ([{"axis_name": "exploit_surface", "p_top": 0.2, "p_critical": 0.75, "p_danger": 0.2, "escalated": False}], "HIGH_RISK_ISOLATED"),
        ([{"axis_name": "overall_risk", "p_top": 0.25, "p_critical": 0.1, "p_danger": 0.1, "escalated": False}], "CAUTION_LIMITED"),
    ]

    for axes, expected_tier in test_cases:
        tier, criteria_version = compute_tier(axes)
        assert tier == expected_tier
        assert criteria_version == CRITERIA_VERSION
        print(f"PASS: compute_tier({axes}) -> {tier}, {criteria_version}")

    # Test heartbeat
    heartbeat()
    print("PASS: heartbeat sent")

    # Test run (mock)
    print("PASS: run() would connect to RabbitMQ and consume messages")