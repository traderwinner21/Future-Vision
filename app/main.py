import os
import json
import logging
from typing import Any, Dict

from fastapi import FastAPI, Request, Body, HTTPException
from fastapi.responses import JSONResponse

# -------------------------------------------------
# Logging
# -------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn.error")

# -------------------------------------------------
# App
# -------------------------------------------------
app = FastAPI(title="TradingView Railway Webhook")

TRADINGVIEW_SECRET = os.getenv("TRADINGVIEW_SECRET", "").strip()

# -------------------------------------------------
# Health routes
# -------------------------------------------------
@app.get("/")
async def root():
    return {"message": "TradingView webhook server is running"}

@app.get("/health")
async def health():
    return {"status": "ok"}

# -------------------------------------------------
# Global validation/error logger
# -------------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    try:
        response = await call_next(request)
        return response
    except Exception as e:
        body = await request.body()
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        logger.error("Raw request body: %s", body.decode("utf-8", errors="ignore"))
        raise e

# -------------------------------------------------
# TradingView webhook
# -------------------------------------------------
@app.post("/webhook/tradingview/bar")
async def tradingview_bar(request: Request):
    """
    Accept raw JSON from TradingView.
    This avoids 422 errors caused by strict Pydantic models.
    """

    raw_body = await request.body()
    raw_text = raw_body.decode("utf-8", errors="ignore")

    logger.info("Received POST /webhook/tradingview/bar")
    logger.info("Raw body: %s", raw_text)

    # 1) Ensure body is valid JSON
    try:
        payload: Dict[str, Any] = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error("Invalid JSON received from TradingView")
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "Invalid JSON body",
                "raw_body": raw_text
            }
        )

    # 2) Optional secret check
    if TRADINGVIEW_SECRET:
        incoming_secret = str(payload.get("secret", "")).strip()
        if incoming_secret != TRADINGVIEW_SECRET:
            logger.error("Invalid TradingView secret")
            return JSONResponse(
                status_code=401,
                content={
                    "ok": False,
                    "error": "Invalid TradingView secret"
                }
            )

    # 3) Read fields safely
    symbol = payload.get("symbol") or payload.get("ticker")
    open_price = payload.get("open")
    high_price = payload.get("high")
    low_price = payload.get("low")
    close_price = payload.get("close")
    volume = payload.get("volume")
    time_value = payload.get("time")
    interval = payload.get("interval")

    # 4) Basic field validation
    if not symbol:
        logger.error("Missing symbol/ticker in payload")
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "Missing symbol or ticker",
                "payload": payload
            }
        )

    # 5) Log parsed values
    logger.info(
        "Parsed payload -> symbol=%s open=%s high=%s low=%s close=%s volume=%s time=%s interval=%s",
        symbol, open_price, high_price, low_price, close_price, volume, time_value, interval
    )

    # -------------------------------------------------
    # PLACE YOUR STRATEGY / AI / FORWARDING LOGIC HERE
    # -------------------------------------------------
    # Example:
    # - calculate features
    # - call AI model
    # - send to TradersPost
    # - store journal record
    # For now, just return success.

    return {
        "ok": True,
        "message": "TradingView payload received successfully",
        "symbol": symbol,
        "close": close_price,
        "payload": payload
    }
