from fastapi import FastAPI
from playwright.sync_api import sync_playwright
import os
import re
import traceback

app = FastAPI()

EMAIL = os.getenv("MINIMAX_EMAIL")
PASSWORD = os.getenv("MINIMAX_PASSWORD")

LOGIN_URL = "https://platform.minimax.io/login"
SUBSCRIBE_URL = "https://platform.minimax.io/user-center/payment/subscribe"


@app.get("/")
def root():
    return {"ok": True, "message": "service is running"}


def extract_balance(text: str):
    patterns = [
        r"([0-9][0-9,\.]*)\s*credits",
        r"Remaining Credits[^0-9]*([0-9][0-9,\.]*)",
        r"Available Credits[^0-9]*([0-9][0-9,\.]*)",
        r"Credits Remaining[^0-9]*([0-9][0-9,\.]*)",
        r"Remaining[^0-9]{0,20}([0-9][0-9,\.]*)\s*credits",
        r"Available[^0-9]{0,20}([0-9][0-9,\.]*)\s*credits",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return f"{m.group(1).strip()} credits"

    return None


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


def click_audio_subscription(page):
    candidates = [
        page.get_by_role("link", name="Audio Subscription"),
        page.get_by_role("button", name="Audio Subscription"),
        page.get_by_text("Audio Subscription", exact=True),
        page.locator("text=Audio Subscription"),
        page.locator("a:has-text('Audio Subscription')"),
        page.locator("button:has-text('Audio Subscription')"),
        page.locator("div:has-text('Audio Subscription')"),
        page.locator("span:has-text('Audio Subscription')"),
    ]

    for locator in candidates:
        try:
            target = locator.first
            if target.is_visible():
                target.click(force=True)
                page.wait_for_timeout(4000)
                return True
        except Exception:
            continue

    return False


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
            # 1. ログイン
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_selector("#mail", timeout=45000)
            page.wait_for_selector("#password", timeout=45000)

            page.fill("#mail", EMAIL)
            page.fill("#password", PASSWORD)
            page.get_by_role("button", name="Sign in").click()

            page.wait_for_url("**/user-center/**", timeout=45000)
            safe_wait(page, 4000)

            # 2. Subscribeページへ
            page.goto(SUBSCRIBE_URL, wait_until="domcontentloaded", timeout=45000)
            safe_wait(page, 5000)

            # 3. Audio Subscription をクリック
            clicked_audio = click_audio_subscription(page)
            safe_wait(page, 5000)

            current_url = page.url
            body_text = page.locator("body").inner_text()

            return {
                "url": current_url,
                "text": body_text,
                "clicked_audio": clicked_audio,
            }

        finally:
            browser.close()


@app.get("/balance")
def balance():
    try:
        result = get_balance_page()
        current_url = result["url"]
        body_text = result["text"]
        clicked_audio = result["clicked_audio"]

        # Audio系ページに入れているか
        on_audio_page = (
            "audio" in current_url.lower()
            or "Audio Subscription" in body_text
        )

        if not on_audio_page:
            return {
                "ok": False,
                "reason": "not on audio subscription page",
                "url": current_url,
                "clicked_audio": clicked_audio,
                "preview": body_text[:3000],
            }

        found = extract_balance(body_text)

        if found:
            return {
                "ok": True,
                "balance": found,
                "url": current_url,
                "clicked_audio": clicked_audio,
            }

        return {
            "ok": False,
            "reason": "balance text not matched",
            "url": current_url,
            "clicked_audio": clicked_audio,
            "preview": body_text[:3000],
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc(),
        }
