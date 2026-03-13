from fastapi import FastAPI
from playwright.sync_api import sync_playwright
import os
import re
import traceback

app = FastAPI()

EMAIL = os.getenv("MINIMAX_EMAIL")
PASSWORD = os.getenv("MINIMAX_PASSWORD")

LOGIN_URL = "https://platform.minimax.io/login"
AFTER_LOGIN_URL = "https://platform.minimax.io/user-center/basic-information"
BALANCE_URL = "https://platform.minimax.io/user-center/payment/audio-subscription"


@app.get("/")
def root():
    return {"ok": True, "message": "service is running"}


def is_login_page(text: str) -> bool:
    t = text.lower()
    return (
        "welcome back" in t
        and "sign in" in t
        and "continue with google" in t
    )


def safe_wait(page, ms=2500):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass

    try:
        page.wait_for_load_state("load", timeout=10000)
    except Exception:
        pass

    page.wait_for_timeout(ms)


def extract_balance_from_card(page):
    """
    Credit Balanceカードだけを狙って数値を抜く。
    body全文からの雑な抽出はしない。
    """
    # Credit Balance を含む最上位カード候補
    card = page.locator(
        "xpath=//span[normalize-space()='Credit Balance']/ancestor::div[contains(@class,'justify-between')][1]"
    ).first

    if card.count() == 0:
        return None

    texts = card.locator("xpath=.//span").all_inner_texts()

    for text in texts:
        value = text.strip()
        if re.fullmatch(r"[0-9,]+", value):
            return value

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

            context = browser.new_context(
                viewport={"width": 1440, "height": 900}
            )
            page = context.new_page()

            try:
                # 1. ログインページへ
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

                page.wait_for_selector("#mail", timeout=60000)
                page.wait_for_selector("#password", timeout=60000)

                page.fill("#mail", EMAIL)
                page.fill("#password", PASSWORD)

                page.get_by_role("button", name="Sign in").click()

                # 2. ログイン後は basic-information に飛ぶ前提
                try:
                    page.wait_for_url("**/user-center/basic-information", timeout=60000)
                except Exception:
                    # 万一URL完全一致しなくても user-center まで来ていれば継続
                    try:
                        page.wait_for_url("**/user-center/**", timeout=15000)
                    except Exception:
                        pass

                safe_wait(page, 5000)

                after_login_url = page.url
                after_login_text = page.locator("body").inner_text()

                # 3. まだログイン画面なら終了
                if is_login_page(after_login_text):
                    return {
                        "ok": False,
                        "reason": "login not completed",
                        "after_login_url": after_login_url,
                        "preview": after_login_text[:2000],
                    }

                # 4. Audio Subscription ページへ移動
                page.goto(BALANCE_URL, wait_until="domcontentloaded", timeout=60000)
                safe_wait(page, 6000)

                current_url = page.url
                body_text = page.locator("body").inner_text()

                # 5. ログイン画面に戻されたか確認
                if is_login_page(body_text):
                    return {
                        "ok": False,
                        "reason": "redirected back to login page",
                        "after_login_url": after_login_url,
                        "url": current_url,
                        "used_url": BALANCE_URL,
                        "preview": body_text[:2000],
                    }

                # 6. Credit Balanceカードから数値を取得
                balance_value = extract_balance_from_card(page)

                if balance_value:
                    return {
                        "ok": True,
                        "balance": balance_value,
                        "after_login_url": after_login_url,
                        "url": current_url,
                        "used_url": BALANCE_URL,
                    }

                return {
                    "ok": False,
                    "reason": "balance not found in credit balance card",
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
