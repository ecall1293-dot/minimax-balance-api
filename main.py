import os
import re
from typing import Optional, Dict, Any

from fastapi import FastAPI
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

app = FastAPI()

VERSION = "2026-03-14-07"
print(f"MINIMAX VERSION: {VERSION}")

LOGIN_URL = "https://platform.minimax.io/login"
BASIC_INFO_URL = "https://platform.minimax.io/user-center/basic-information"
AUDIO_URL = "https://platform.minimax.io/user-center/payment/audio-subscription"


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_number(text: str) -> Optional[float]:
    num = re.sub(r"[^\d.]", "", text or "")
    if not num:
        return None
    try:
        return float(num)
    except ValueError:
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

    email_candidates = [
        page.get_by_placeholder("Email").first,
        page.locator('input[placeholder="Email"]').first,
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

    await page.wait_for_url("**/user-center/basic-information**", timeout=20000)
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2000)

    print("after login url =", page.url)
    print("after login preview =", await get_body_preview(page, 1000))
    return page.url


async def open_audio_subscription_in_new_tab(context) -> Any:
    page = await context.new_page()

    # まず basic-information を一度開いて認証済み状態を安定させる
    await page.goto(BASIC_INFO_URL, wait_until="domcontentloaded")
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1500)

    print("new tab basic-info url =", page.url)
    print("new tab basic-info preview =", await get_body_preview(page, 800))

    # その後 audio を直開き
    await page.goto(AUDIO_URL, wait_until="domcontentloaded")
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2500)

    print("after goto audio url =", page.url)
    print("after goto audio preview =", await get_body_preview(page, 1200))

    await page.locator("text=Audio Subscription").first.wait_for(
        state="visible",
        timeout=15000,
    )
    await page.locator("text=Credit Balance").first.wait_for(
        state="visible",
        timeout=15000,
    )

    return page


async def extract_balance(page) -> Optional[Dict[str, Any]]:
    candidates = [
        page.locator("text=Credit Balance").first.locator("xpath=ancestor::div[1]"),
        page.locator("text=Credit Balance").first.locator("xpath=ancestor::div[2]"),
        page.locator("text=Credit Balance").first.locator("xpath=ancestor::div[3]"),
        page.locator("div:has-text('Credit Balance')").first,
    ]

    for idx, candidate in enumerate(candidates, start=1):
        try:
            if await candidate.count() == 0:
                continue

            text = clean_text(await candidate.inner_text())
            print(f"card candidate #{idx} =", text)

            m = re.search(
                r"Credit Balance\s*(?:Balance Alert)?\s*([0-9][0-9,]*(?:\.\d+)?)",
                text,
                re.I,
            )
            if m:
                raw = m.group(1).strip()
                return {
                    "balanceText": raw,
                    "balanceValue": parse_number(raw),
                }

            spans = candidate.locator("span")
            span_count = await spans.count()
            for i in range(span_count):
                span_text = clean_text(await spans.nth(i).inner_text())
                if re.fullmatch(r"[0-9][0-9,]*(?:\.\d+)?", span_text):
                    return {
                        "balanceText": span_text,
                        "balanceValue": parse_number(span_text),
                    }
        except Exception:
            pass

    body_text = clean_text(await get_body_text(page))
    print("fallback body parse")

    for pattern in [
        r"Credit Balance\s*(?:Balance Alert)?\s*([0-9][0-9,]*(?:\.\d+)?)",
        r"Credit Balance.*?([0-9][0-9,]*(?:\.\d+)?)",
    ]:
        m = re.search(pattern, body_text, re.I)
        if m:
            raw = m.group(1).strip()
            return {
                "balanceText": raw,
                "balanceValue": parse_number(raw),
            }

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
        login_page = await context.new_page()

        after_login_url = ""

        try:
            after_login_url = await login(login_page, email, password)

            audio_page = await open_audio_subscription_in_new_tab(context)

            parsed = await extract_balance(audio_page)
            preview = await get_body_preview(audio_page, 2000)

            if parsed:
                return {
                    "ok": True,
                    "balanceText": parsed["balanceText"],
                    "balanceValue": parsed["balanceValue"],
                    "after_login_url": after_login_url,
                    "url": audio_page.url,
                    "used_method": "login-basicinfo-newtab-audio-url",
                    "preview": preview,
                }

            return {
                "ok": False,
                "reason": "balance text not matched on audio page",
                "after_login_url": after_login_url,
                "url": audio_page.url,
                "used_method": "login-basicinfo-newtab-audio-url",
                "preview": preview,
            }

        except Exception as e:
            preview = ""
            try:
                preview = await get_body_preview(login_page, 2000)
            except Exception:
                pass

            print("exception =", str(e))
            print("exception url =", login_page.url)
            print("exception preview =", preview[:1000])

            return {
                "ok": False,
                "reason": str(e),
                "after_login_url": after_login_url or login_page.url,
                "url": login_page.url,
                "used_method": "login-basicinfo-newtab-audio-url",
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
        "version": VERSION,
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
