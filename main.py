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


def is_login_page(text: str):
    t = text.lower()
    return (
        "welcome back" in t
        and "sign in" in t
        and "continue with google" in t
    )


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

            context = browser.new_context(
                viewport={"width": 1400, "height": 900}
            )
            page = context.new_page()

            try:
                # 1. ログインページ
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_selector("#mail", timeout=60000)
                page.wait_for_selector("#password", timeout=60000)

                page.fill("#mail", EMAIL)
                page.fill("#password", PASSWORD)

                # 2. サインイン
                page.get_by_role("button", name="Sign in").click()

                # 3. ログイン完了待ち
                # user-centerに入るのを待つ。失敗しても続行して状態確認する
                try:
                    page.wait_for_url("**/user-center/**", timeout=60000)
                except Exception:
                    pass

                page.wait_for_timeout(8000)

                after_login_url = page.url
                after_login_text = page.locator("body").inner_text()

                # 4. まだログイン画面ならここで返す
                if is_login_page(after_login_text):
                    return {
                        "ok": False,
                        "reason": "login not completed",
                        "after_login_url": after_login_url,
                        "preview": after_login_text[:2000],
                    }

                # 5. ログイン後に残高ページへ
                page.goto(BALANCE_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(8000)

                current_url = page.url
                body_text = page.locator("body").inner_text()

                # 6. もし残高ページでもログイン画面に戻されたら、その情報を返す
                if is_login_page(body_text):
                    return {
                        "ok": False,
                        "reason": "redirected back to login page",
                        "after_login_url": after_login_url,
                        "url": current_url,
                        "preview": body_text[:2000],
                    }

                found = extract_balance(body_text)

                if found:
                    return {
                        "ok": True,
                        "balance": found,
                        "after_login_url": after_login_url,
                        "url": current_url,
                        "used_url": BALANCE_URL,
                    }

                return {
                    "ok": False,
                    "reason": "balance not found",
                    "after_login_url": after_login_url,
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
