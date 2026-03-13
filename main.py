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


def extract_balance(text: str) -> str | None:
    patterns = [
        r"([0-9][0-9,\.]*)\s*credits",
        r"Remaining[^0-9]*([0-9][0-9,\.]*)",
        r"Available[^0-9]*([0-9][0-9,\.]*)",
        r"Balance[^0-9]*([0-9][0-9,\.]*)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if "credit" in pattern.lower():
                return f"{value} credits"
            return value

    return None


def get_balance_page_text() -> dict:
    if not EMAIL or not PASSWORD:
        raise Exception("MINIMAX_EMAIL or MINIMAX_PASSWORD is empty")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page()

        try:
            # 1. ログインページへ
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            # 2. 入力欄待機
            page.wait_for_selector("#mail", timeout=60000)
            page.wait_for_selector("#password", timeout=60000)

            # 3. 入力
            page.fill("#mail", EMAIL)
            page.fill("#password", PASSWORD)

            # 4. ログイン
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_timeout(7000)

            # 5. 音声サブスクページへ
            page.goto(BALANCE_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(7000)

            # 6. ページ情報取得
            title = page.title()
            url = page.url
            body_text = page.locator("body").inner_text()

            return {
                "title": title,
                "url": url,
                "text": body_text
            }

        finally:
            browser.close()


@app.get("/balance")
def balance():
    try:
        result = get_balance_page_text()
        text = result["text"]
        title = result["title"]
        url = result["url"]

        extracted = extract_balance(text)

        if extracted:
            return {
                "ok": True,
                "balance": extracted,
                "title": title,
                "url": url
            }

        return {
            "ok": False,
            "balance": "not found",
            "title": title,
            "url": url,
            "preview": text[:3000]
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc()
        }
