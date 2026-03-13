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
    # まずは Credit Balance の直後の数値を最優先で取る
    patterns = [
        r"Credit Balance[^0-9]*([0-9,]+)",
        r"Balance Alert[^0-9]*([0-9,]+)",
        r"Reload\s*[0-9,]+\s*credits.*?([0-9,]+)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)

    return None


@app.get("/balance")
def balance():
    try:
        if not EMAIL or not PASSWORD:
            return {
                "ok": False,
                "error": "MINIMAX_EMAIL or MINIMAX_PASSWORD is empty"
            }

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--disable-software-rasterizer",
                ],
            )

            context = browser.new_context(viewport={"width": 1400, "height": 900})
            page = context.new_page()

            try:
                # ログイン
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

                page.wait_for_selector("#mail", timeout=60000)
                page.wait_for_selector("#password", timeout=60000)

                page.fill("#mail", EMAIL)
                page.fill("#password", PASSWORD)

                page.get_by_role("button", name="Sign in").click()

                page.wait_for_url("**/user-center/**", timeout=60000)
                page.wait_for_timeout(3000)

                # 正しいAudio Subscriptionページへ直接移動
                page.goto(BALANCE_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)

                current_url = page.url
                body_text = page.locator("body").inner_text()

                found = extract_balance(body_text)

                if found:
                    return {
                        "ok": True,
                        "balance": found,
                        "url": current_url,
                        "used_url": BALANCE_URL,
                    }

                return {
                    "ok": False,
                    "reason": "balance text not matched",
                    "url": current_url,
                    "used_url": BALANCE_URL,
                    "preview": body_text[:3000],
                }

            finally:
                browser.close()

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc(),
        }
