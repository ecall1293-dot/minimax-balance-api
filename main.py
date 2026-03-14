import os
import re
from typing import Optional, Dict, Any, List

from fastapi import FastAPI
from playwright.async_api import async_playwright

app = FastAPI()

VERSION = "2026-03-14-08"
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
    return (await get_body_text(page))[:limit]


async def wait_basic_info_ready(page, logs: List[str]) -> None:
    await page.wait_for_url("**/user-center/basic-information**", timeout=20000)
    logs.append(f"basic-info url reached: {page.url}")

    # 画面の主要要素が見えるまで待つ
    for text in ["Basic Information", "Email address", "GroupID", "Get Your Key"]:
        try:
            await page.locator(f"text={text}").first.wait_for(state="visible", timeout=10000)
            logs.append(f"basic-info marker visible: {text}")
        except Exception:
            logs.append(f"basic-info marker missing: {text}")

    # 初期化待ち
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
        logs.append("basic-info networkidle reached")
    except Exception:
        logs.append("basic-info networkidle timeout")

    await page.wait_for_timeout(5000)
    logs.append("basic-info extra wait done")


async def login(page, email: str, password: str, logs: List[str]) -> str:
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    logs.append(f"login page url: {page.url}")

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
            logs.append(f"email input matched candidate #{idx}")
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
            logs.append(f"password input matched candidate #{idx}")
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
            logs.append(f"sign in button matched candidate #{idx}")
            break
        except Exception:
            pass

    if sign_in_button is None:
        preview = await get_body_preview(page)
        raise Exception(f"sign in button not found; preview={preview}")

    await sign_in_button.click()
    logs.append("sign in clicked")

    await wait_basic_info_ready(page, logs)
    logs.append(f"after login final url: {page.url}")
    return page.url


async def try_open_audio(page, logs: List[str]) -> bool:
    body = await get_body_text(page)
    body_clean = clean_text(body)

    if "Audio Subscription" in body_clean and "Credit Balance" in body_clean:
        logs.append("audio markers found in body")
        return True

    if AUDIO_URL in page.url and "Credit Balance" in body_clean:
        logs.append("audio url and credit balance found")
        return True

    return False


async def open_audio_subscription(page, logs: List[str]) -> str:
    # いきなり1回で決めに行かず、安定化込みで複数回試す
    for attempt in range(1, 4):
        logs.append(f"audio attempt #{attempt} start from url={page.url}")

        # 一度 basic-info を明示して土台を揃える
        await page.goto(BASIC_INFO_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        logs.append(f"audio attempt #{attempt} basic-info reload url={page.url}")

        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
            logs.append(f"audio attempt #{attempt} basic-info networkidle ok")
        except Exception:
            logs.append(f"audio attempt #{attempt} basic-info networkidle timeout")

        await page.wait_for_timeout(2500)

        # 1. 普通の goto
        await page.goto(AUDIO_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000 + attempt * 1000)
        logs.append(f"audio attempt #{attempt} after goto url={page.url}")
        logs.append(f"audio attempt #{attempt} preview after goto={(await get_body_preview(page, 700))}")

        if await try_open_audio(page, logs):
            return page.url

        # 2. location.href でもう一押し
        await page.evaluate(f"window.location.href = '{AUDIO_URL}'")
        await page.wait_for_timeout(4000 + attempt * 1000)
        logs.append(f"audio attempt #{attempt} after location.href url={page.url}")
        logs.append(f"audio attempt #{attempt} preview after location.href={(await get_body_preview(page, 700))}")

        if await try_open_audio(page, logs):
            return page.url

    raise Exception(f"audio page not reached after retries; current url={page.url}")


async def extract_balance(page, logs: List[str]) -> Optional[Dict[str, Any]]:
    # 貼ってもらった Credit Balance のDOM構造に寄せて取得
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
            logs.append(f"balance candidate #{idx}: {text}")

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

    # 全文フォールバック
    body_text = clean_text(await get_body_text(page))
    logs.append("fallback body parse used")

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
    logs: List[str] = []

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
            after_login_url = await login(page, email, password, logs)
            current_url = await open_audio_subscription(page, logs)

            parsed = await extract_balance(page, logs)
            preview = await get_body_preview(page, 2000)

            if parsed:
                return {
                    "ok": True,
                    "balanceText": parsed["balanceText"],
                    "balanceValue": parsed["balanceValue"],
                    "after_login_url": after_login_url,
                    "url": current_url,
                    "used_method": "login-basicinfo-audio-retry",
                    "preview": preview,
                    "logs": logs,
                }

            return {
                "ok": False,
                "reason": "balance text not matched on audio page",
                "after_login_url": after_login_url,
                "url": page.url,
                "used_method": "login-basicinfo-audio-retry",
                "preview": preview,
                "logs": logs,
            }

        except Exception as e:
            preview = await get_body_preview(page, 2000)
            logs.append(f"exception: {str(e)}")
            logs.append(f"exception url: {page.url}")

            return {
                "ok": False,
                "reason": str(e),
                "after_login_url": after_login_url or page.url,
                "url": page.url,
                "used_method": "login-basicinfo-audio-retry",
                "preview": preview,
                "logs": logs,
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
