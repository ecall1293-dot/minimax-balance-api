from fastapi import FastAPI
from playwright.sync_api import sync_playwright
import os
import re
import traceback

app = FastAPI()

EMAIL = os.getenv("MINIMAX_EMAIL")
PASSWORD = os.getenv("MINIMAX_PASSWORD")

LOGIN_URL = "https://platform.minimax.io/login"

# まず本命
AUDIO_URL_PRIMARY = "https://platform.minimax.io/subscribe/audio-subscription"
# 保険
AUDIO_URL_FALLBACK = "https://platform.minimax.io/user-center/payment/audio-subscription"


@app.get("/")
def root():
    return {"ok": True, "message": "service is running"}


def extract_balance(text: str):
    patterns = [
        r"([0-9][0-9,\.]*)\s*credits",
        r"Remaining Credits[^0-9]*([0-9][0-9,\.]*)",
        r"Available Credits[^0-9]*([0-9][0-9,\.]*)",
        r"Credits Remaining[^0-9]*([0-9][0-9,\.]*)",
        r"Remaining[^0-9]{0,30}([0-9][0-9,\.]*)\s*credits",
        r"Available[^0-9]{0,30}([0-9][0-9,\.]*)\s*credits",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return f"{m.group(1).strip()} credits"

    return None


def safe_wait(page, ms=3000):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass

    try:
        page.wait_for_load_state("load", timeout=10000)
    except Exception:
        pass

    page.wait_for_timeout(ms)


def is_404_page(text: str):
    return (
        "404" in text
        and ("页面不存在" in text or "page not found" in text.lower() or "访问的页面不存在" in text)
    )


def get_page_text(page):
    try:
        page.wait_for_selector("body", timeout=10000)
    except Exception:
        pass
    safe_wait(page, 2500)
    return page.locator("body").inner_text()


def goto_audio_page(page):
    # 1回目: 本命URL
    page.goto(AUDIO_URL_PRIMARY, wait_until="domcontentloaded", timeout=45000)
    text = get_page_text(page)
    url = page.url

    if not is_404_page(text):
        return {
            "url": url,
            "text": text,
            "used_url": AUDIO_URL_PRIMARY,
        }

    # 2回目: fallback
    page.goto(AUDIO_URL_FALLBACK, wait_until="domcontentloaded", timeout=45000)
    text = get_page_text(page)
    url = page.url

    return {
        "url": url,
        "text": text,
        "used_url": AUDIO_URL_FALLBACK,
    }


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

                # 2. Audio Subscription ページへ
                result = goto_audio_page(page)
                current_url = result["url"]
                body_text = result["text"]
                used_url = result["used_url"]

                if is_404_page(body_text):
                    return {
                        "ok": False,
                        "reason": "audio subscription page returned 404",
                        "url": current_url,
                        "used_url": used_url,
                        "preview": body_text[:3000],
                    }

                found = extract_balance(body_text)

                if found:
                    return {
                        "ok": True,
                        "balance": found,
                        "url": current_url,
                        "used_url": used_url,
                    }

                return {
                    "ok": False,
                    "reason": "balance text not matched",
                    "url": current_url,
                    "used_url": used_url,
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
