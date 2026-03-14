import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

LOGIN_URL = "https://platform.minimax.io/login"
BASIC_INFO_URL = "https://platform.minimax.io/user-center/basic-information"

EMAIL = os.environ.get("MINIMAX_EMAIL", "")
PASSWORD = os.environ.get("MINIMAX_PASSWORD", "")

HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"
STATE_FILE = "minimax_state.json"

app = FastAPI(title="minimax-balance-api")


def parse_number(text: str):
    try:
        return float(text.replace(",", "").strip())
    except Exception:
        return None


async def get_body_preview(page, limit: int = 1200):
    try:
        text = await page.locator("body").inner_text()
        text = re.sub(r"\s+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()
        return text[:limit]
    except Exception:
        return ""


async def wait_visible_any(page, selectors, timeout=15000):
    last_error = None
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=timeout)
            return loc, selector
        except Exception as e:
            last_error = e
    raise last_error if last_error else Exception("No selector matched")


async def js_click_menu_row(page, label: str, logs: list[str]):
    script = """
    (label) => {
      const nodes = Array.from(document.querySelectorAll("div, p, span, button, a"));
      const target = nodes.find(el => (el.innerText || "").trim() === label);
      if (!target) return { ok: false, reason: "not found" };

      let clickable = target;
      for (let i = 0; i < 6; i++) {
        if (!clickable || !clickable.parentElement) break;
        const style = window.getComputedStyle(clickable);
        if (
          clickable.tagName === "BUTTON" ||
          clickable.tagName === "A" ||
          clickable.onclick ||
          style.cursor === "pointer"
        ) {
          break;
        }
        clickable = clickable.parentElement;
      }

      clickable.click();
      return {
        ok: true,
        text: (clickable.innerText || "").trim()
      };
    }
    """
    result = await page.evaluate(script, label)
    logs.append(f"js click '{label}' result={result}")
    if not result.get("ok"):
        raise Exception(f"js click failed for {label}: {result}")


async def login(page, logs: list[str]):
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
    logs.append(f"login page url: {page.url}")

    email_selectors = [
        'input[type="email"]',
        'input[placeholder*="Email"]',
        'input[placeholder*="email"]',
        'input[name="email"]',
        'input',
    ]
    password_selectors = [
        'input[type="password"]',
        'input[placeholder*="Password"]',
        'input[placeholder*="password"]',
    ]

    email_input, email_selector = await wait_visible_any(page, email_selectors, timeout=15000)
    logs.append(f"email input matched: {email_selector}")
    await email_input.fill(EMAIL)

    password_input, password_selector = await wait_visible_any(page, password_selectors, timeout=15000)
    logs.append(f"password input matched: {password_selector}")
    await password_input.fill(PASSWORD)

    sign_in_candidates = [
        page.get_by_role("button", name=re.compile(r"sign in", re.I)),
        page.locator("button:has-text('Sign in')"),
        page.locator("button"),
    ]

    clicked = False
    for idx, candidate in enumerate(sign_in_candidates, start=1):
        try:
            if idx < 3:
                await candidate.first.wait_for(state="visible", timeout=5000)
                await candidate.first.click()
                logs.append(f"sign in button matched candidate #{idx}")
                clicked = True
                break
            else:
                count = await candidate.count()
                for i in range(count):
                    text = (await candidate.nth(i).inner_text()).strip().lower()
                    if "sign in" in text:
                        await candidate.nth(i).click()
                        logs.append(f"sign in button matched candidate #{idx}/{i}")
                        clicked = True
                        break
                if clicked:
                    break
        except Exception as e:
            logs.append(f"sign in candidate #{idx} failed: {e}")

    if not clicked:
        raise Exception("sign in button not found")

    logs.append("sign in clicked")

    try:
        await page.wait_for_url(re.compile(r"/user-center/basic-information"), timeout=20000)
        logs.append(f"basic-info url reached: {page.url}")
    except PlaywrightTimeoutError:
        logs.append(f"basic-info url wait timeout, current={page.url}")

    basic_markers = ["Basic Information", "Email address", "GroupID", "Get Your Key"]
    for marker in basic_markers:
        try:
            await page.get_by_text(marker, exact=False).first.wait_for(state="visible", timeout=8000)
            logs.append(f"basic-info marker visible: {marker}")
        except Exception:
            logs.append(f"basic-info marker missing: {marker}")

    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
        logs.append("basic-info networkidle reached")
    except Exception:
        logs.append("basic-info networkidle timeout")

    await page.wait_for_timeout(2000)
    logs.append("basic-info extra wait done")
    logs.append(f"after login final url: {page.url}")
    logs.append(f"after login preview: {await get_body_preview(page, 1200)}")


async def extract_balance(page, logs: list[str], tag: str = ""):
    try:
        locator = page.locator("div:has(span:text('Credit Balance'))").first
        if await locator.count() > 0:
            text = await locator.inner_text()
            logs.append(f"{tag} balance card text={text}")

            value_locator = locator.locator("span.text-\\[16px\\].font-\\[500\\].text-\\[\\#181E25\\]").last
            if await value_locator.count() > 0:
                raw = (await value_locator.inner_text()).strip()
                if re.fullmatch(r"[0-9][0-9,]*", raw):
                    return {
                        "balanceText": raw,
                        "balanceValue": parse_number(raw),
                    }
    except Exception as e:
        logs.append(f"{tag} direct balance parse failed: {e}")

    try:
        body = await page.locator("body").inner_text()
        m = re.search(r"Credit Balance.*?([0-9][0-9,]*)", body, re.S)
        if m:
            raw = m.group(1).strip()
            logs.append(f"{tag} fallback matched balance={raw}")
            return {
                "balanceText": raw,
                "balanceValue": parse_number(raw),
            }
    except Exception as e:
        logs.append(f"{tag} fallback parse failed: {e}")

    return None


async def open_audio_and_capture_balance(page, logs: list[str]):
    await page.goto(BASIC_INFO_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    logs.append(f"sidebar nav start url: {page.url}")
    logs.append(f"sidebar nav start preview: {await get_body_preview(page, 1200)}")

    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
        logs.append("sidebar nav networkidle ok")
    except Exception:
        logs.append("sidebar nav networkidle timeout")

    try:
        await js_click_menu_row(page, "Subscribe", logs)
    except Exception as e:
        logs.append(f"subscribe js click skipped/failed: {e}")

    await js_click_menu_row(page, "Audio", logs)

    for step in range(1, 11):
        current_url = page.url
        preview = await get_body_preview(page, 1500)
        logs.append(f"after audio click step#{step} url={current_url}")
        logs.append(f"after audio click step#{step} preview={preview}")

        parsed = await extract_balance(page, logs, tag=f"step#{step}")
        if parsed:
            logs.append(f"balance captured at step#{step}")
            return {
                "url": current_url,
                "balanceText": parsed["balanceText"],
                "balanceValue": parsed["balanceValue"],
                "preview": preview,
            }

        await page.wait_for_timeout(1000)

    raise Exception(f"balance not captured after audio click; current url={page.url}")


async def fetch_balance() -> dict[str, Any]:
    logs: list[str] = []

    if not EMAIL or not PASSWORD:
        return {
            "ok": False,
            "reason": "MINIMAX_EMAIL / MINIMAX_PASSWORD が設定されていません",
            "logs": logs,
        }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context()

        state_path = Path(STATE_FILE)
        if state_path.exists():
            try:
                await context.add_cookies(json.loads(state_path.read_text(encoding="utf-8")))
                logs.append("existing cookies loaded")
            except Exception as e:
                logs.append(f"cookie load failed: {e}")

        page = await context.new_page()

        try:
            await login(page, logs)

            try:
                cookies = await context.cookies()
                state_path.write_text(
                    json.dumps(cookies, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logs.append("cookies saved")
            except Exception as e:
                logs.append(f"cookie save failed: {e}")

            result = await open_audio_and_capture_balance(page, logs)

            return {
                "ok": True,
                "balanceText": result["balanceText"],
                "balanceValue": result["balanceValue"],
                "url": result["url"],
                "used_method": "login-basicinfo-sidebar-audio-capture-early",
                "preview": result["preview"],
                "logs": logs,
            }

        except Exception as e:
            return {
                "ok": False,
                "reason": str(e),
                "url": page.url,
                "preview": await get_body_preview(page, 2000),
                "logs": logs,
            }

        finally:
            await context.close()
            await browser.close()


@app.get("/")
async def root():
    return {
        "ok": True,
        "message": "minimax-balance-api running",
    }


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/balance")
async def balance():
    result = await fetch_balance()

    if result.get("ok"):
        return {
            "ok": True,
            "balance": int(result.get("balanceValue") or 0),
        }

    return {
        "ok": False,
        "balance": None,
        "reason": result.get("reason", "unknown error"),
    }


@app.get("/balance/raw")
async def balance_raw():
    return await fetch_balance()
