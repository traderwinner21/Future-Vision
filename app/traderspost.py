from __future__ import annotations

import json
import httpx
from sqlalchemy.orm import Session

from .config import settings
from .models import SignalLog
from .schemas import TraderPostSignal


async def send_signal(db: Session, symbol: str, signal_type: str, action: str, score: float | None, payload: TraderPostSignal, reason: str) -> SignalLog:
    log = SignalLog(
        symbol=symbol,
        signal_type=signal_type,
        action=action,
        score=score,
        accepted=False,
        payload_json=payload.model_dump_json(),
        reason=reason,
    )
    db.add(log)
    db.flush()

    if not settings.enable_live_ordering:
        log.accepted = True
        log.response_text = "paper_mode_enabled"
        return log

    if not settings.traderspost_webhook:
        log.response_text = "missing_TRADERSPOST_WEBHOOK"
        return log

    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.post(
                settings.traderspost_webhook,
                headers={"Content-Type": "application/json"},
                content=payload.model_dump_json(),
            )
        log.response_text = response.text[:4000]
        log.accepted = response.is_success
    except Exception as exc:
        log.response_text = f"request_failed: {type(exc).__name__}: {exc}"

    return log


def pretty_payload(payload: TraderPostSignal) -> str:
    return json.dumps(payload.model_dump(), indent=2)
