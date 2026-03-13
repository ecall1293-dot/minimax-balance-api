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


def safe_wait(page, ms=3000):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("load", timeout=15000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(ms)


def extract_balance_only_on_target_page(text: str):
    """
    クレジットページでしか抽出しない前提の抽出関数。
    誤爆しやすい単純な数値抽出はしない。
    """
    patterns = [
        r"([0-9][0-9,\.]*)\s*credits",
        r"Remaining Credits[^0-9]*([0-9][0-9,\.]*)",
        r"Available Credits[^0-9]*([0-9][0-9,\.]*)",
        r"Credits Remaining[^0-9]*([0-9][0-9,\.]*)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            value = m.group(1).strip()
            return f"{value} credits"

    return None


def get_balance_page():
    if not EMAIL or not PASSWORD:
        raise Exception("MINIMAX_EMAIL or MINIMAX_PASSWORD is empty")

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

        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        try:
            # 1. ログインページを開く
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            safe_wait(page, 2000)

            page.wait_for_selector("#mail", timeout=60000)
            page.wait_for_selector("#password", timeout=60000)

            page.fill("#mail", EMAIL)
            page.fill("#password", PASSWORD)
            page.get_by_role("button", name="Sign in").click()

            # 2. ログイン後、user-center配下に来るのを待つ
            page.wait_for_url("**/user-center/**", timeout=60000)
            safe_wait(page, 4000)

            # 3. 目的ページへ移動
            page.goto(BALANCE_URL, wait_until="domcontentloaded", timeout=60000)
            safe_wait(page, 5000)

            current_url = page.url

            try:
                title = page.title()
            except Exception:
                title = "(title unavailable)"

            try:
                page.wait_for_selector("body", timeout=10000)
            except Exception:
                pass

            safe_wait(page, 3000)

            body_text = page.locator("body").inner_text()

            return {
                "title": title,
                "url": current_url,
                "text": body_text,
            }

        finally:
            browser.close()


@app.get("/balance")
def balance():
    try:
        result = get_balance_page()
        title = result["title"]
        url = result["url"]
        text = result["text"]

        # 目的URLに到達していないなら抽出しない
        if "audio-subscription" not in url:
            return {
                "ok": False,
                "balance": "not found",
                "reason": "not on audio subscription page",
                "title": title,
                "url": url,
                "preview": text[:3000],
            }

        extracted = extract_balance_only_on_target_page(text)

        if extracted:
            return {
                "ok": True,
                "balance": extracted,
                "title": title,
                "url": url,
            }

        return {
            "ok": False,
            "balance": "not found",
            "reason": "balance text not matched",
            "title": title,
            "url": url,
            "preview": text[:3000],
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc(),
        }
