import re
import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


LOGIN_URL = "https://platform.minimax.io/login"
AUDIO_URL = "https://platform.minimax.io/user-center/payment/audio-subscription"


def parse_balance(text: str):
    text = re.sub(r"\s+", " ", text).strip()

    patterns = [
        r"Credit Balance[:\s]*([A-Za-z$¥]?\s*[\d,]+(?:\.\d+)?)",
        r"Available Credit[:\s]*([A-Za-z$¥]?\s*[\d,]+(?:\.\d+)?)",
        r"Balance[:\s]*([A-Za-z$¥]?\s*[\d,]+(?:\.\d+)?)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            raw = m.group(1).strip()
            num = re.sub(r"[^\d.]", "", raw)
            return {
                "balanceText": raw,
                "balanceValue": float(num) if num else None
            }

    return None


async def click_menu_row_by_label(page, label: str):
    # p要素ではなく、行全体の cursor-pointer div をクリックする
    row = page.locator(
        f"div.cursor-pointer:has(p:text-is('{label}'))"
    ).first

    await row.wait_for(state="visible", timeout=10000)
    await row.click(force=True)
    await page.wait_for_timeout(1200)


async def goto_audio_page(page):
    # 念のため直URLも試す
    await page.goto(AUDIO_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    body_text = await page.locator("body").inner_text()

    # すでにAudio画面ならそのまま続行
    if "Credit Balance" in body_text:
        return "direct-url"

    # basic-information 等に戻されているなら、左メニューから確実に辿る
    # 親の Subscribe を押す
    await click_menu_row_by_label(page, "Subscribe")

    # 子の Audio を押す
    await click_menu_row_by_label(page, "Audio")

    # URL変化待ちは補助程度
    try:
        await page.wait_for_url("**/user-center/payment/audio-subscription**", timeout=8000)
    except PlaywrightTimeoutError:
        pass

    # 画面本文で到達判定
    for _ in range(10):
        body_text = await page.locator("body").inner_text()
        if "Credit Balance" in body_text:
            return "menu-click"
        await page.wait_for_timeout(1000)

    raise Exception("audio page not reached")


async def fetch_minimax_balance(email: str, password: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle")

            # すでにログイン済みならスキップ
            current_url = page.url
            if "/user-center/" not in current_url:
                await page.locator('input[type="email"]').fill(email)
                await page.locator('input[type="password"]').fill(password)

                sign_in_btn = page.locator("button:has-text('Sign in')").first
                await sign_in_btn.click()

                await page.wait_for_url("**/user-center/**", timeout=20000)
                await page.wait_for_timeout(3000)

            after_login_url = page.url

            used_method = await goto_audio_page(page)

            # 到達後の全文取得
            body_text = await page.locator("body").inner_text()
            preview = body_text[:2000]

            # まず全文から素直に拾う
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

            # Credit Balance の近くだけ再確認
            card = page.locator("div:has-text('Credit Balance')").first
            if await card.count() > 0:
                card_text = await card.inner_text()
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

            return {
                "ok": False,
                "reason": "balance text not matched on audio page",
                "after_login_url": after_login_url,
                "url": page.url,
                "used_method": used_method,
                "preview": preview,
            }

        except Exception as e:
            preview = ""
            try:
                preview = (await page.locator("body").inner_text())[:2000]
            except:
                pass

            return {
                "ok": False,
                "reason": str(e),
                "after_login_url": page.url,
                "url": page.url,
                "used_method": "unknown",
                "preview": preview,
            }

        finally:
            await context.close()
            await browser.close()
