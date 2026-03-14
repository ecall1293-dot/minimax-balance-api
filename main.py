import os
import re
from typing import Optional, Dict, Any

from fastapi import FastAPI
from playwright.async_api import async_playwright

app = FastAPI()

print("MINIMAX VERSION: 2026-03-14-05")

LOGIN_URL = "https://platform.minimax.io/login"
BASIC_INFO_URL = "https://platform.minimax.io/user-center/basic-information"
AUDIO_URL = "https://platform.minimax.io/user-center/payment/audio-subscription"


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_balance_from_text(text: str) -> Optional[Dict[str, Any]]:
    normalized = clean_text(text)

    patterns = [
        r"Credit Balance\s*([0-9][0-9,]*(?:\.\d+)?)",
        r"Credit Balance.*?([0-9][0-9,]*(?:\.\d+)?)",
        r"Balance\s*([0-9][0-9,]*(?:\.\d+)?)",
    ]

    for pattern in patterns:
        m = re.search(pattern, normalized, re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            num = raw.replace(",", "")
            value = None
            try:
                value = float(num)
            except ValueError:
                value = None

            return {
                "balanceText": raw,
                "balanceValue": value,
            }

    return None


async def get_body_text(page) -> str:
    try:
        return await page.locator("body").inner_text()
    except Exception:
        return ""


async def get_body_preview(page, limit: int = 1500) -> str:
    text = await get_body_text(page)
    return text[:limit]


async def login(page, email: str, password: str) -> str:
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    print("login url =", page.url)

    # メール欄
    email_candidates = [
        page.locator('input[placeholder="Email"]').first,
        page.get_by_placeholder("Email").first,
        page.locator('input[type="text"]').first,
        page.locator('input[type="email"]').first,
    ]

    email_input = None
    for idx, candidate in enumerate(email_candidates, start=1):
        try:
            await candidate.wait_for(state="visible", timeout=3000)
            email_input = candidate
            print(f"email input matched candidate #{idx}")
            break
        except Exception:
            pass

    if email_input is None:
        preview = await get_body_preview(page)
        raise Exception(f"email input not found; preview={preview}")

    # パスワード欄
    password_candidates = [
        page.locator('input[type="password"]').first,
        page.locator('input[autocomplete="current-password"]').first,
    ]

    password_input = None
    for idx, candidate in enumerate(password_candidates, start=1):
        try:
            await candidate.wait_for(state="visible", timeout=3000)
            password_input = candidate
            print(f"password input matched candidate #{idx}")
            break
        except Exception:
            pass

    if password_input is None:
        preview = await get_body_preview(page)
        raise Exception(f"password input not found; preview={preview}")

    await email_input.fill(email)
    await password_input.fill(password)

    # Sign in ボタン
    sign_in_candidates = [
        page.get_by_role("button", name="Sign in").first,
        page.locator("button:has-text('Sign in')").first,
    ]

    sign_in_button = None
    for idx, candidate in enumerate(sign_in_candidates, start=1):
        try:
            await candidate.wait_for(state="visible", timeout=3000)
            sign_in_button = candidate
            print(f"sign in button matched candidate #{idx}")
            break
        except Exception:
            pass

    if sign_in_button is None:
        preview = await get_body_preview(page)
        raise Exception(f"sign in button not found; preview={preview}")

    await sign_in_button.click()

    # basic-information 到達待ち
    await page.wait_for_url("**/user-center/basic-information**", timeout=20000)
    await page.wait_for_timeout(2500)

    print("after login url =", page.url)
    return page.url


async def open_audio_subscription(page) -> str:
    await page.goto(AUDIO_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)

    print("after goto audio url =", page.url)

    # 画面の固有要素を待つ
    await page.locator("h1").filter(has_text="Audio Subscription").first.wait_for(
        state="visible",
        timeout=15000,
    )

    await page.locator("text=Credit Balance").first.wait_for(
        state="visible",
        timeout=15000,
    )

    body_preview = await get_body_preview(page, 1200)
    print("audio page preview =", body_preview)

    return page.url


async def extract_balance(page) -> Optional[Dict[str, Any]]:
    # カード単位で優先取得
    card_candidates = [
        page.locator("div:has-text('Credit Balance')").first,
        page.locator("text=Credit Balance").first.locator("xpath=ancestor::div[1]"),
        page.locator("text=Credit Balance").first.locator("xpath=ancestor::div[2]"),
        page.locator("text=Credit Balance").first.locator("xpath=ancestor::div[3]"),
    ]

    for idx, card in enumerate(card_candidates, start=1):
        try:
            if await card.count() > 0:
                text = await card.inner_text()
                print(f"credit card candidate #{idx} =", text)
                parsed = parse_balance_from_text(text)
                if parsed:
                    return parsed
        except Exception:
            pass

    # 最後に全文
    body_text = await get_body_text(page)
    print("fallback body parse")
    return parse_balance_from_text(body_text)


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

        after_login_url = ""
        try:
            after_login_url = await login(page, email, password)
            audio_url = await open_audio_subscription(page)

            parsed = await extract_balance(page)
            preview = await get_body_preview(page, 2000)

            if parsed:
                return {
                    "ok": True,
                    "balanceText": parsed["balanceText"],
                    "balanceValue": parsed["balanceValue"],
                    "after_login_url": after_login_url,
                    "url": audio_url,
                    "used_method": "login-basicinfo-audio-url",
                    "preview": preview,
                }

            return {
                "ok": False,
                "reason": "balance text not matched on audio page",
                "after_login_url": after_login_url,
                "url": page.url,
                "used_method": "login-basicinfo-audio-url",
                "preview": preview,
            }

        except Exception as e:
            preview = await get_body_preview(page, 2000)
            print("exception =", str(e))
            print("exception url =", page.url)
            print("exception preview =", preview[:1000])

            return {
                "ok": False,
                "reason": str(e),
                "after_login_url": after_login_url or page.url,
                "url": page.url,
                "used_method": "login-basicinfo-audio-url",
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
        "version": "2026-03-14-05",
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
