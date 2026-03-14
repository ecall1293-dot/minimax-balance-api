import os
import re
from typing import Optional, Any, Dict

from fastapi import FastAPI
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

app = FastAPI()

print("MINIMAX VERSION: 2026-03-14-02")

LOGIN_URL = "https://platform.minimax.io/login"
BASIC_INFO_URL = "https://platform.minimax.io/user-center/basic-information"
AUDIO_URL = "https://platform.minimax.io/user-center/payment/audio-subscription"


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_balance(text: str) -> Optional[Dict[str, Any]]:
    normalized = clean_text(text)

    patterns = [
        r"Credit Balance[:\s]*([A-Za-z$¥]?\s*[\d,]+(?:\.\d+)?)",
        r"Credit Balance.*?([A-Za-z$¥]?\s*[\d,]+(?:\.\d+)?)",
        r"Available Credit[:\s]*([A-Za-z$¥]?\s*[\d,]+(?:\.\d+)?)",
        r"Balance[:\s]*([A-Za-z$¥]?\s*[\d,]+(?:\.\d+)?)",
    ]

    for pattern in patterns:
        m = re.search(pattern, normalized, re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            num = re.sub(r"[^\d.]", "", raw)
            value = None
            if num:
                try:
                    value = float(num)
                except ValueError:
                    value = None

            return {
                "balanceText": raw,
                "balanceValue": value,
            }

    return None


async def get_body_preview(page, limit: int = 1500) -> str:
    try:
        text = await page.locator("body").inner_text()
        return text[:limit]
    except Exception:
        return ""


async def click_subscribe_audio(page) -> None:
    # Subscribe の親ブロックを特定
    subscribe_section = page.locator("div.flex.flex-col.mb-[8px]").filter(
        has=page.locator("p:text-is('Subscribe')")
    ).first

    await subscribe_section.wait_for(state="visible", timeout=10000)

    # 親行 Subscribe をクリック
    subscribe_header = subscribe_section.locator("div.cursor-pointer").filter(
        has=page.locator("section p:text-is('Subscribe')")
    ).first

    await subscribe_header.wait_for(state="visible", timeout=10000)
    await subscribe_header.click(force=True)
    await page.wait_for_timeout(1000)

    # 子行 Audio をクリック
    audio_row = subscribe_section.locator("div.cursor-pointer").filter(
        has=page.locator("p:text-is('Audio')")
    ).first

    await audio_row.wait_for(state="visible", timeout=10000)
    await audio_row.click(force=True)
    await page.wait_for_timeout(2500)


async def ensure_audio_page(page) -> str:
    # 1. 直URLを試す
    await page.goto(AUDIO_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)

    body_text = await page.locator("body").inner_text()
    if "Credit Balance" in body_text:
        return "direct-url"

    # 2. Basic Information に戻されている前提でメニューから移動
    await page.goto(BASIC_INFO_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    await click_subscribe_audio(page)

    try:
        await page.wait_for_url("**/user-center/payment/audio-subscription**", timeout=8000)
    except PlaywrightTimeoutError:
        pass

    # 本文側で到達判定
    for _ in range(10):
        body_text = await page.locator("body").inner_text()
        if "Credit Balance" in body_text:
            return "menu-click"
        await page.wait_for_timeout(1000)

    raise Exception(f"audio page not reached; current url={page.url}")


async def login_if_needed(page, email: str, password: str) -> str:
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    current_url = page.url
    if "/user-center/" in current_url:
        return current_url

    email_input = page.locator('input[type="email"]').first
    password_input = page.locator('input[type="password"]').first

    await email_input.wait_for(state="visible", timeout=15000)
    await email_input.fill(email)

    await password_input.wait_for(state="visible", timeout=15000)
    await password_input.fill(password)

    sign_in_button = page.locator("button").filter(has_text=re.compile(r"sign in|login|log in", re.I)).first
    await sign_in_button.click()

    await page.wait_for_url("**/user-center/**", timeout=20000)
    await page.wait_for_timeout(3000)

    return page.url


async def fetch_minimax_balance(email: str, password: str) -> Dict[str, Any]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context()
        page = await context.new_page()

        used_method = "unknown"

        try:
            after_login_url = await login_if_needed(page, email, password)
            print("after login url =", after_login_url)

            used_method = await ensure_audio_page(page)
            print("after audio navigation url =", page.url)

            body_text = await page.locator("body").inner_text()
            preview = body_text[:2000]
            print("body preview =", preview[:1000])

            # 全文から先に拾う
            parsed = parse_balance(body_text)
            if parsed:
                return {
                    "ok": True,
                    "balanceText": parsed["balanceText"],
                    "balanceValue": parsed["balanceValue"],
                    "after_login_url": after_login_url,
                    "url": page.url,
                    "used_method": used_method,
                    "preview": preview,
                }

            # Credit Balance を含むカードっぽい範囲で再探索
            candidates = [
                page.locator("div:has-text('Credit Balance')").first,
                page.locator("span:has-text('Credit Balance')").first.locator("xpath=.."),
                page.locator("text=Credit Balance").first.locator("xpath=../.."),
            ]

            for candidate in candidates:
                try:
                    if await candidate.count() > 0:
                        card_text = await candidate.inner_text()
                        parsed = parse_balance(card_text)
                        if parsed:
                            return {
                                "ok": True,
                                "balanceText": parsed["balanceText"],
                                "balanceValue": parsed["balanceValue"],
                                "after_login_url": after_login_url,
                                "url": page.url,
                                "used_method": used_method,
                                "preview": card_text[:1000],
                            }
                except Exception:
                    pass

            return {
                "ok": False,
                "reason": "balance text not matched on audio page",
                "after_login_url": after_login_url,
                "url": page.url,
                "used_method": used_method,
                "preview": preview,
            }

        except Exception as e:
            preview = await get_body_preview(page)
            print("exception =", str(e))
            print("exception url =", page.url)
            print("exception preview =", preview[:1000])

            return {
                "ok": False,
                "reason": str(e),
                "after_login_url": page.url,
                "url": page.url,
                "used_method": used_method,
                "preview": preview,
            }

        finally:
            await context.close()
            await browser.close()


@app.get("/")
async def root():
    return {
        "ok": True,
        "message": "MiniMax balance API is running",
        "version": "2026-03-14-02",
    }


@app.get("/balance")
async def balance():
    email = os.getenv("MINIMAX_EMAIL", "").strip()
    password = os.getenv("MINIMAX_PASSWORD", "").strip()

    if not email or not password:
        return {
            "ok": False,
            "reason": "MINIMAX_EMAIL or MINIMAX_PASSWORD is not set",
        }

    return await fetch_minimax_balance(email, password)
