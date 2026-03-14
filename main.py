import os
import re
from typing import Optional, Any, Dict

from fastapi import FastAPI
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

app = FastAPI()

print("MINIMAX VERSION: 2026-03-14-03")

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
        r"Balance[:\s]*([A-Za-z$¥]?\s*([0-9][\d,]*(?:\.\d+)?))",
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


async def get_body_text(page) -> str:
    try:
        return await page.locator("body").inner_text()
    except Exception:
        return ""


async def get_body_preview(page, limit: int = 1500) -> str:
    text = await get_body_text(page)
    return text[:limit]


async def login_if_needed(page, email: str, password: str) -> str:
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)

    current_url = page.url
    print("login page url =", current_url)

    if "/user-center/" in current_url:
        print("already logged in")
        return current_url

    email_input = None
    email_candidates = [
        page.get_by_placeholder("Email").first,
        page.locator('input[placeholder="Email"]').first,
        page.locator('input[autocomplete="username"]').first,
        page.locator('input[type="text"]').first,
        page.locator('input[type="email"]').first,
    ]

    for idx, candidate in enumerate(email_candidates, start=1):
        try:
            await candidate.wait_for(state="visible", timeout=2500)
            email_input = candidate
            print(f"email input matched candidate #{idx}")
            break
        except Exception:
            pass

    if email_input is None:
        preview = await get_body_preview(page)
        raise Exception(f"email input not found; preview={preview}")

    password_input = None
    password_candidates = [
        page.locator('input[type="password"]').first,
        page.locator('input[autocomplete="current-password"]').first,
    ]

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

    sign_in_button = None
    sign_in_candidates = [
        page.get_by_role("button", name="Sign in").first,
        page.locator("button:has-text('Sign in')").first,
        page.locator("button").filter(has_text=re.compile(r"sign in|login|log in", re.I)).first,
    ]

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

    await page.wait_for_url("**/user-center/**", timeout=20000)
    await page.wait_for_timeout(3000)

    print("after login url =", page.url)
    return page.url


async def click_subscribe_audio(page) -> None:
    # まず Basic Information を安定表示
    await page.goto(BASIC_INFO_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)

    # Subscribe 親セクション
    subscribe_section = page.locator("div.flex.flex-col.mb-[8px]").filter(
        has=page.locator("p:text-is('Subscribe')")
    ).first

    await subscribe_section.wait_for(state="visible", timeout=10000)

    # 親ヘッダー Subscribe 行
    subscribe_header = subscribe_section.locator("div.cursor-pointer").filter(
        has=page.locator("section p:text-is('Subscribe')")
    ).first

    await subscribe_header.wait_for(state="visible", timeout=10000)
    await subscribe_header.click(force=True)
    await page.wait_for_timeout(1200)

    # 子行 Audio
    audio_row = subscribe_section.locator("div.cursor-pointer").filter(
        has=page.locator("p:text-is('Audio')")
    ).first

    await audio_row.wait_for(state="visible", timeout=10000)
    await audio_row.click(force=True)
    await page.wait_for_timeout(3000)

    print("after audio click url =", page.url)
    body_preview = await get_body_preview(page, 1000)
    print("after audio click preview =", body_preview)


async def ensure_audio_page(page) -> str:
    # 1. 直URLを先に試す
    await page.goto(AUDIO_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)

    body_text = await get_body_text(page)
    print("after direct goto url =", page.url)

    if "Credit Balance" in body_text:
        return "direct-url"

    # 2. メニュー操作で再遷移
    await click_subscribe_audio(page)

    try:
        await page.wait_for_url("**/user-center/payment/audio-subscription**", timeout=8000)
    except PlaywrightTimeoutError:
        pass

    for _ in range(10):
        body_text = await get_body_text(page)
        if "Credit Balance" in body_text:
            print("audio page detected by body text")
            return "menu-click"
        await page.wait_for_timeout(1000)

    raise Exception(f"audio page not reached; current url={page.url}")


async def extract_balance(page) -> Optional[Dict[str, Any]]:
    # 全文から探す
    body_text = await get_body_text(page)
    parsed = parse_balance(body_text)
    if parsed:
        return parsed

    # Credit Balance 周辺候補
    candidates = [
        page.locator("div:has-text('Credit Balance')").first,
        page.locator("span:has-text('Credit Balance')").first.locator("xpath=.."),
        page.locator("text=Credit Balance").first.locator("xpath=../.."),
        page.locator("text=Credit Balance").first.locator("xpath=../../.."),
    ]

    for candidate in candidates:
        try:
            if await candidate.count() > 0:
                text = await candidate.inner_text()
                parsed = parse_balance(text)
                if parsed:
                    return parsed
        except Exception:
            pass

    return None


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
        after_login_url = ""

        try:
            after_login_url = await login_if_needed(page, email, password)
            used_method = await ensure_audio_page(page)

            body_text = await get_body_text(page)
            preview = body_text[:2000]

            print("final url =", page.url)
            print("final used_method =", used_method)
            print("final preview =", preview[:1000])

            parsed = await extract_balance(page)
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

            return {
                "ok": False,
                "reason": "balance text not matched on audio page",
                "after_login_url": after_login_url,
                "url": page.url,
                "used_method": used_method,
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
        "version": "2026-03-14-03",
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
