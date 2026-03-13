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
        r"Audio[^0-9]{0,40}([0-9][0-9,\.]*)\s*credits",
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


def try_click_audio_tab(page):
    candidates = [
        "text=Audio",
        ":text('Audio')",
        "div:has-text('Audio')",
        "span:has-text('Audio')",
        "button:has-text('Audio')",
        "[role='tab']:has-text('Audio')",
        ".ant-tabs-tab:has-text('Audio')",
    ]

    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.is_visible():
                loc.click(force=True)
                page.wait_for_timeout(2500)
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

            # 2. まず Subscribe ページへ行く
            page.goto(SUBSCRIBE_URL, wait_until="domcontentloaded", timeout=45000)
            safe_wait(page, 5000)

            # 3. Audio タブを押す
            clicked_audio = try_click_audio_tab(page)
            safe_wait(page, 3000)

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
