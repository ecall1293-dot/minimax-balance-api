from fastapi import FastAPI
from playwright.sync_api import sync_playwright
import os
import re
import traceback

app = FastAPI()

EMAIL = os.getenv("MINIMAX_EMAIL")
PASSWORD = os.getenv("MINIMAX_PASSWORD")

LOGIN_URL = "https://platform.minimax.io/login"
BALANCE_URL = "https://platform.minimax.io/user-center/payment/audio-subscription"


@app.get("/")
def root():
    return {"ok": True, "message": "service is running"}


def extract_balance(text: str):
    match = re.search(r"Credit Balance[^0-9]*([0-9,]+)", text)
    if match:
        return match.group(1)
    return None


@app.get("/balance")
def balance():
    try:

        if not EMAIL or not PASSWORD:
            return {"ok": False, "error": "env not set"}

        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu"
                ],
            )

            context = browser.new_context()
            page = context.new_page()

            # ログイン
            page.goto(LOGIN_URL)

            page.wait_for_selector("#mail")
            page.fill("#mail", EMAIL)

            page.wait_for_selector("#password")
            page.fill("#password", PASSWORD)

            page.get_by_role("button", name="Sign in").click()

            page.wait_for_url("**/user-center/**", timeout=60000)

            # クレジットページへ
            page.goto(BALANCE_URL)

            page.wait_for_timeout(5000)

            text = page.locator("body").inner_text()

            balance = extract_balance(text)

            browser.close()

            if balance:
                return {
                    "ok": True,
                    "balance": balance,
                    "url": BALANCE_URL
                }

            return {
                "ok": False,
                "reason": "balance not found",
                "preview": text[:2000]
            }

    except Exception as e:

        return {
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc(),
        }
