from fastapi import FastAPI
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
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


def safe_wait(page):
    # SPA対策。load_stateは失敗しても続行
    for state in ["domcontentloaded", "load", "networkidle"]:
        try:
            page.wait_for_load_state(state, timeout=15000)
        except Exception:
            pass

    # 追加待機
    page.wait_for_timeout(3000)


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

        context = browser.new_context(
            viewport={"width": 1280, "height": 800}
        )

        page = context.new_page()

        try:
            # ログインページ
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

            page.wait_for_selector("#mail", timeout=60000)
            page.wait_for_selector("#password", timeout=60000)

            page.fill("#mail", EMAIL)
            page.fill("#password", PASSWORD)

            page.get_by_role("button", name="Sign in").click()

            # ログイン完了待機
            page.wait_for_url("**/user-center/**", timeout=60000)
            safe_wait(page)

            # 残高ページへ
            page.goto(BALANCE_URL, wait_until="domcontentloaded", timeout=60000)
            safe_wait(page)

            # ここでさらにSPA遷移しても落ちにくくする
            current_url = page.url

            # title取得は失敗してもURLだけ返せば十分
            try:
                title = page.title()
            except Exception:
                title = "(title unavailable)"

            # body取得前にも少し待つ
            try:
                page.wait_for_selector("body", timeout=10000)
            except Exception:
                pass

            safe_wait(page)

            try:
                body_text = page.locator("body").inner_text(timeout=15000)
            except TypeError:
                # 古い互換のため
                body_text = page.locator("body").inner_text()
            except Exception:
                body_text = ""

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

        text = result["text"]
        title = result["title"]
        url = result["url"]

        extracted = extract_balance(text)

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
