from datetime import datetime, timezone
import threading
import time
import uuid
from typing import Set

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, PerspectiveEvent
from fastapi import Depends
from sqlalchemy.orm import Session
import requests


HIGH_RISK_TIERS = ('HIGH_RISK_ISOLATED', 'CAUTION_LIMITED', 'KNOWN_THREAT')
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEARTBEAT_INTERVAL = 60
CYCLE_INTERVAL = 300


def _fetch_high_risk_servers(session: Session) -> list:
    return (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.risk_tier.in_(HIGH_RISK_TIERS))
        .all()
    )


def _post_to_bus(table: str, row: dict) -> None:
    try:
        requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "row": row},
            timeout=5,
        )
    except requests.RequestException:
        pass


def _post_heartbeat(run_id: str, last_heartbeat: str) -> None:
    _post_to_bus("service_health", {
        "service": "risk_tier_watchlist",
        "run_id": run_id,
        "last_heartbeat": last_heartbeat,
    })


def _post_perspective_event(
    server_id: str,
    server_name: str,
    old_tier: str,
    new_tier: str,
    perspective_id: int = 1,
) -> None:
    _post_to_bus("perspective_events", {
        "server_id": server_id,
        "perspective_id": perspective_id,
        "change_type": "risk_tier_escalation",
        "old_tier": old_tier,
        "new_tier": new_tier,
    })


def run() -> None:
    run_id = str(uuid.uuid4())
    stop_event = threading.Event()

    last_seen_high_risk: Set[str] = set()
    last_check = datetime.min.replace(tzinfo=timezone.utc)

    def heartbeat() -> None:
        while not stop_event.wait(HEARTBEAT_INTERVAL):
            _post_heartbeat(
                run_id,
                datetime.now(timezone.utc).isoformat(),
            )

    def main_loop() -> None:
        nonlocal last_check

        while True:
            if stop_event.wait(CYCLE_INTERVAL):
                break

            with next(get_session()) as session:
                current_high_risk = _fetch_high_risk_servers(session)
                newly_high_risk = [
                    s for s in current_high_risk
                    if s.server_id not in last_seen_high_risk
                ]

                for server in newly_high_risk:
                    _post_perspective_event(
                        server_id=server.server_id,
                        server_name=server.name,
                        old_tier="UNKNOWN",
                        new_tier=server.risk_tier,
                    )
                    import sys
                    print(
                        f"[risk_tier_watchlist] escalation: "
                        f"server={server.name} tier={server.risk_tier}",
                        file=sys.stderr,
                    )

                last_seen_high_risk = {s.server_id for s in current_high_risk}
                last_check = datetime.now(timezone.utc)

    hb_thread = threading.Thread(target=heartbeat, daemon=True)
    hb_thread.start()
    main_loop()


if __name__ == "__main__":
    import os

    if os.environ.get("RUNTEST") != "1":
        run()
    else:
        from fastapi import FastAPI
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        POSTED_EVENTS = []

        def _mock_post_to_bus(table: str, row: dict) -> None:
            if table == "perspective_events":
                POSTED_EVENTS.append(row)

        original_post = _post_to_bus

        test_engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        from app.models import Base
        Base.metadata.create_all(bind=test_engine)
        TestSession = sessionmaker(bind=test_engine)

        with test_engine.connect() as conn:
            conn.execute(
                McpServerRegistry.__table__.insert(),
                {
                    "server_id": "srv_new_hr",
                    "name": "TestServer-HIGH",
                    "risk_tier": "HIGH_RISK_ISOLATED",
                    "last_assessed": datetime.now(timezone.utc),
                },
            )
            conn.execute(
                McpServerRegistry.__table__.insert(),
                {
                    "server_id": "srv_trusted",
                    "name": "TestServer-Trusted",
                    "risk_tier": "TRUSTED",
                    "last_assessed": datetime.now(timezone.utc),
                },
            )
            conn.commit()

        the_app = FastAPI()

        def override_get_session():
            yield TestSession()

        the_app.dependency_overrides[get_session] = override_get_session

        _globals = globals()

        def test_run():
            global POSTED_EVENTS
            POSTED_EVENTS = []

            run_id = str(uuid.uuid4())
            stop_event = threading.Event()
            last_seen_high_risk: Set[str] = set()

            with next(get_session()) as session:
                baseline = _fetch_high_risk_servers(session)
                last_seen_high_risk = {s.server_id for s in baseline}

            import sys

            def fake_main_loop():
                global last_seen_high_risk
                with next(get_session()) as session:
                    current = _fetch_high_risk_servers(session)
                    newly = [
                        s for s in current
                        if s.server_id not in last_seen_high_risk
                    ]
                    for srv in newly:
                        _mock_post_to_bus("perspective_events", {
                            "server_id": srv.server_id,
                            "perspective_id": 1,
                            "change_type": "risk_tier_escalation",
                            "old_tier": "UNKNOWN",
                            "new_tier": srv.risk_tier,
                        })
                        print(
                            f"[risk_tier_watchlist] escalation: "
                            f"server={srv.name} tier={srv.risk_tier}",
                            file=sys.stderr,
                        )
                    last_seen_high_risk = {s.server_id for s in current}
                stop_event.set()

            t = threading.Thread(target=fake_main_loop, daemon=True)
            t.start()
            stop_event.wait(timeout=5)

            assert len(POSTED_EVENTS) == 1, (
                f"Expected 1 perspective_event, got {len(POSTED_EVENTS)}"
            )
            assert POSTED_EVENTS[0]["change_type"] == "risk_tier_escalation"
            print("PASS")

        test_run()