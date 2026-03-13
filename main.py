from fastapi import FastAPI
from playwright.sync_api import sync_playwright
import os
import re
import traceback

app = FastAPI()

EMAIL = os.getenv("MINIMAX_EMAIL")
PASSWORD = os.getenv("MINIMAX_PASSWORD")

LOGIN_URL = "https://platform.minimax.io/login"


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


def click_if_exists(page, role: str, name: str, timeout: int = 5000):
    try:
        locator = page.get_by_role(role, name=name)
        locator.first.wait_for(timeout=timeout)
        locator.first.click()
        page.wait_for_timeout(2000)
        return True
    except Exception:
        return False


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
                # 1. login
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_selector("#mail", timeout=45000)
                page.wait_for_selector("#password", timeout=45000)

                page.fill("#mail", EMAIL)
                page.fill("#password", PASSWORD)
                page.get_by_role("button", name="Sign in").click()

                page.wait_for_url("**/user-center/**", timeout=45000)
                page.wait_for_timeout(4000)

                # 2. 直URLではなく、画面内メニューから遷移を試す
                clicked = False

                # まず Subscribe を試す
                if click_if_exists(page, "link", "Subscribe") or click_if_exists(page, "button", "Subscribe"):
                    clicked = True

                # 次に Audio を試す
                if click_if_exists(page, "link", "Audio") or click_if_exists(page, "button", "Audio"):
                    clicked = True

                # クリックで変化しない場合、テキストクリックも試す
                if not clicked:
                    try:
                        page.locator("text=Audio").first.click()
                        page.wait_for_timeout(3000)
                        clicked = True
                    except Exception:
                        pass

                if not clicked:
                    try:
                        page.locator("text=Subscribe").first.click()
                        page.wait_for_timeout(3000)
                        clicked = True
                    except Exception:
                        pass

                # 念のため少し待つ
                page.wait_for_timeout(5000)

                current_url = page.url
                body_text = page.locator("body").inner_text()

                found = extract_balance(body_text)

                if found:
                    return {
                        "ok": True,
                        "balance": found,
                        "url": current_url,
                    }

                return {
                    "ok": False,
                    "reason": "balance text not matched",
                    "url": current_url,
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
