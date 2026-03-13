from fastapi import FastAPI
from playwright.sync_api import sync_playwright
import os

app = FastAPI()

EMAIL = os.getenv("MINIMAX_EMAIL")
PASSWORD = os.getenv("MINIMAX_PASSWORD")

def get_balance():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://platform.minimax.io/login")

        page.fill("input[type=email]", EMAIL)
        page.fill("input[type=password]", PASSWORD)
        page.click("button[type=submit]")

        page.wait_for_timeout(5000)

        page.goto("https://platform.minimax.io/billing")

        page.wait_for_timeout(5000)

        text = page.inner_text("body")

        browser.close()

        return text

@app.get("/balance")
def balance():
    text = get_balance()

    # 簡易抽出
    import re
    match = re.search(r"([0-9,]+)\s*credits", text)

    if match:
        return {"balance": match.group(1) + " credits"}
    else:
        return {"balance": "not found"}