import os
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import FastAPI

app = FastAPI(title="minimax-balance-api")

MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID = os.environ.get("MINIMAX_GROUP_ID", "")

BALANCE_URL = "https://platform.minimax.io/v1/api/openplatform/charge/combo/cycle_audio_resource_package"


def parse_balance(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


async def fetch_balance_direct() -> dict:
    if not MINIMAX_API_KEY or not MINIMAX_GROUP_ID:
        return {
            "ok": False,
            "balance": None,
            "reason": "MINIMAX_API_KEY / MINIMAX_GROUP_ID が設定されていません",
        }

    params = {
        "biz_line": 2,
        "cycle_type": 3,
        "GroupId": MINIMAX_GROUP_ID,
    }

    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://platform.minimax.io/user-center/payment/audio-subscription",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(BALANCE_URL, params=params, headers=headers)

        if response.status_code != 200:
            return {
                "ok": False,
                "balance": None,
                "reason": f"HTTP {response.status_code}",
                "body": response.text[:1000],
            }

        data = response.json()

        base_resp = data.get("base_resp") or {}
        if base_resp.get("status_code") != 0:
            return {
                "ok": False,
                "balance": None,
                "reason": base_resp.get("status_msg", "unknown api error"),
                "raw": data,
            }

        credit_info = data.get("credit_info") or {}
        balance = parse_balance(credit_info.get("total_credit"))

        if balance is None:
            return {
                "ok": False,
                "balance": None,
                "reason": "credit_info.total_credit not found",
                "raw": data,
            }

        return {
            "ok": True,
            "balance": balance,
        }

    except Exception as e:
        return {
            "ok": False,
            "balance": None,
            "reason": str(e),
        }


@app.get("/")
async def root():
    return {
        "ok": True,
        "message": "minimax-balance-api running",
        "mode": "direct-http",
    }


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/balance")
async def balance():
    result = await fetch_balance_direct()

    if result.get("ok"):
        return {
            "ok": True,
            "balance": result.get("balance"),
        }

    return {
        "ok": False,
        "balance": None,
        "reason": result.get("reason", "unknown error"),
    }


@app.get("/balance/raw")
async def balance_raw():
    return await fetch_balance_direct()
