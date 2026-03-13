from fastapi import FastAPI
from playwright.sync_api import sync_playwright
import os
import re
import traceback

app = FastAPI()

EMAIL = os.getenv("MINIMAX_EMAIL")
PASSWORD = os.getenv("MINIMAX_PASSWORD")

@app.get("/")
def root():
    return {"ok": True, "message": "service is running"}

def get_balance():
    if not EMAIL or not PASSWORD:
        raise Exception("MINIMAX_EMAIL or MINIMAX_PASSWORD is empty")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            page.goto("https://platform.minimax.io/login", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            page.fill('input[type="email"]', EMAIL)
            page.fill('input[type="password"]', PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_timeout(5000)

            page.goto("https://platform.minimax.io/billing", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            text = page.inner_text("body")
            print("===== PAGE TEXT START =====")
            print(text[:5000])
            print("===== PAGE TEXT END =====")

            return text

        finally:
            browser.close()

@app.get("/balance")
def balance():
    try:
        text = get_balance()
        match = re.search(r"([0-9,]+)\s*credits", text, re.IGNORECASE)
        if match:
            return {"balance": f"{match.group(1)} credits"}

        return {
            "balance": "not found",
            "preview": text[:1000]
        }

    except Exception as e:
        return {
            "error": str(e),
            "trace": traceback.format_exc()
        }
