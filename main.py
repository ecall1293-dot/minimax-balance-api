import os
import re
from typing import Optional, Dict, Any, List

from fastapi import FastAPI
from playwright.async_api import async_playwright

app = FastAPI()

VERSION = "2026-03-14-10"
print(f"MINIMAX VERSION: {VERSION}")

LOGIN_URL = "https://platform.minimax.io/login"
BASIC_INFO_URL = "https://platform.minimax.io/user-center/basic-information"


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

    for text in ["Basic Information", "Email address", "GroupID", "Get Your Key"]:
        try:
            await page.locator(f"text={text}").first.wait_for(state="visible", timeout=10000)
            logs.append(f"basic-info marker visible: {text}")
        except Exception:
            logs.append(f"basic-info marker missing: {text}")

    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
        logs.append("basic-info networkidle reached")
    except Exception:
        logs.append("basic-info networkidle timeout")

    await page.wait_for_timeout(3000)
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
        raise Exception(f"email input not found; preview={await get_body_preview(page)}")

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
        raise Exception(f"password input not found; preview={await get_body_preview(page)}")

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
        raise Exception(f"sign in button not found; preview={await get_body_preview(page)}")

    await sign_in_button.click()
    logs.append("sign in clicked")

    await wait_basic_info_ready(page, logs)
    logs.append(f"after login final url: {page.url}")
    logs.append(f"after login preview: {await get_body_preview(page, 800)}")
    return page.url


async def js_click_menu_row(page, label: str, logs: List[str]) -> None:
    """
    テキスト自身ではなく、label を含む menu row の親 div.cursor-pointer を JS クリックする。
    pointer-event の被りを回避するため、Playwright の通常 click ではなく DOM click を使う。
    """
    script = """
    (label) => {
      const all = Array.from(document.querySelectorAll('div.cursor-pointer'));
      const row = all.find(el => {
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        return t === label || t.includes(label);
      });
      if (!row) return { ok: false, reason: 'row not found' };

      row.scrollIntoView({ block: 'center' });
      row.click();

      return {
        ok: true,
        text: (row.innerText || '').replace(/\\s+/g, ' ').trim()
      };
    }
    """
    result = await page.evaluate(script, label)
    logs.append(f"js click '{label}' result={result}")

    if not result or not result.get("ok"):
        raise Exception(f"menu row '{label}' not found or not clicked")

    await page.wait_for_timeout(2000)


async def open_audio_by_sidebar(page, logs: List[str]) -> str:
    await page.goto(BASIC_INFO_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    logs.append(f"sidebar nav start url: {page.url}")
    logs.append(f"sidebar nav start preview: {await get_body_preview(page, 700)}")

    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
        logs.append("sidebar nav networkidle ok")
    except Exception:
        logs.append("sidebar nav networkidle timeout")

    # 念のため親メニューを押す
    try:
        await js_click_menu_row(page, "Subscribe", logs)
    except Exception as e:
        logs.append(f"subscribe js click skipped/failed: {str(e)}")

    # Audio 行を親divごと JSクリック
    await js_click_menu_row(page, "Audio", logs)

    for step in range(1, 11):
        current_url = page.url
        preview = await get_body_preview(page, 1000)
        body_text = clean_text(await get_body_text(page))

        logs.append(f"after audio click step#{step} url={current_url}")
        logs.append(f"after audio click step#{step} preview={preview}")

        if "Audio Subscription" in body_text and "Credit Balance" in body_text:
            logs.append("audio page detected by body markers")
            return page.url

        await page.wait_for_timeout(1000)

    raise Exception(f"audio page not reached by sidebar; current url={page.url}")


async def extract_balance(page, logs: List[str]) -> Optional[Dict[str, Any]]:
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
            current_url = await open_audio_by_sidebar(page, logs)

            parsed = await extract_balance(page, logs)
            preview = await get_body_preview(page, 2000)

            if parsed:
                return {
                    "ok": True,
                    "balanceText": parsed["balanceText"],
                    "balanceValue": parsed["balanceValue"],
                    "after_login_url": after_login_url,
                    "url": current_url,
                    "used_method": "login-basicinfo-sidebar-audio-jsclick",
                    "preview": preview,
                    "logs": logs,
                }

            return {
                "ok": False,
                "reason": "balance text not matched on audio page",
                "after_login_url": after_login_url,
                "url": page.url,
                "used_method": "login-basicinfo-sidebar-audio-jsclick",
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
                "used_method": "login-basicinfo-sidebar-audio-jsclick",
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
