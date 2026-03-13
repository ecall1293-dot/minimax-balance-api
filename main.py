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

@app.get("/balance")
def balance():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto("https://platform.minimax.io/login", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            title = page.title()
            url = page.url
            body = page.locator("body").inner_text()

            browser.close()

            return {
                "title": title,
                "url": url,
                "preview": body[:3000]
            }

    except Exception as e:
        return {
            "error": str(e),
            "trace": traceback.format_exc()
        }
