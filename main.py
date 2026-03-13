import { chromium, Page } from "playwright";
import fs from "fs";
import path from "path";

type MinimaxCreditResult =
  | {
      ok: true;
      balanceText: string;
      balanceValue: number | null;
      url: string;
      usedMethod: string;
      debugPath?: string;
    }
  | {
      ok: false;
      reason: string;
      url: string;
      usedMethod?: string;
      afterLoginUrl?: string;
      preview?: string;
      debugPath?: string;
    };

const LOGIN_URL = "https://platform.minimax.io/login";
const BASIC_INFO_URL = "https://platform.minimax.io/user-center/basic-information";
const AUDIO_SUBSCRIPTION_URL =
  "https://platform.minimax.io/user-center/payment/audio-subscription";

function ensureDir(dir: string) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

async function saveDebugFiles(page: Page, prefix = "minimax_debug") {
  const debugDir = path.resolve(process.cwd(), "debug");
  ensureDir(debugDir);

  const now = new Date();
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
    "_",
    String(now.getHours()).padStart(2, "0"),
    String(now.getMinutes()).padStart(2, "0"),
    String(now.getSeconds()).padStart(2, "0"),
  ].join("");

  const base = path.join(debugDir, `${prefix}_${stamp}`);
  const screenshotPath = `${base}.png`;
  const htmlPath = `${base}.html`;
  const txtPath = `${base}.txt`;

  await page.screenshot({ path: screenshotPath, fullPage: true });
  const html = await page.content();
  fs.writeFileSync(htmlPath, html, "utf-8");

  const bodyText = await page.locator("body").innerText().catch(() => "");
  fs.writeFileSync(txtPath, bodyText, "utf-8");

  return base;
}

async function logPageSnapshot(page: Page, label: string) {
  const url = page.url();
  const title = await page.title().catch(() => "");
  const bodyText = await page.locator("body").innerText().catch(() => "");
  console.log(`\n===== ${label} =====`);
  console.log("URL:", url);
  console.log("TITLE:", title);
  console.log("BODY PREVIEW:", bodyText.slice(0, 1200));
  console.log("====================\n");
}

async function safeClickByText(page: Page, text: string, timeout = 5000) {
  const locator = page.getByText(text, { exact: false }).first();
  await locator.waitFor({ state: "visible", timeout });
  await locator.click();
}

async function waitForCreditBalance(page: Page, timeout = 15000) {
  const candidates = [
    page.getByText("Credit Balance", { exact: false }).first(),
    page.locator("text=Credit Balance").first(),
    page.locator("span:has-text('Credit Balance')").first(),
    page.locator("div:has-text('Credit Balance')").first(),
  ];

  for (const locator of candidates) {
    try {
      await locator.waitFor({ state: "visible", timeout: 3000 });
      return locator;
    } catch {
      // continue
    }
  }

  // 最後に少し長めに待つ
  try {
    const fallback = page.locator("text=Credit Balance").first();
    await fallback.waitFor({ state: "visible", timeout });
    return fallback;
  } catch {
    return null;
  }
}

function normalizeSpace(text: string): string {
  return text.replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
}

function parseBalanceFromText(text: string): { balanceText: string; balanceValue: number | null } | null {
  const normalized = normalizeSpace(text);

  // 例:
  // "Credit Balance 1,234"
  // "Credit Balance: 1,234"
  // "Credit Balance USD 12.34"
  // "Credit Balance 1234 credits"
  const patterns = [
    /Credit Balance[:\s]*([A-Za-z]{0,5}\s*[\d,]+(?:\.\d+)?)/i,
    /Credit Balance.*?([A-Za-z]{0,5}\s*[\d,]+(?:\.\d+)?)/i,
    /Balance[:\s]*([A-Za-z]{0,5}\s*[\d,]+(?:\.\d+)?)/i,
  ];

  for (const pattern of patterns) {
    const match = normalized.match(pattern);
    if (match?.[1]) {
      const balanceText = match[1].trim();
      const numeric = balanceText.replace(/[^\d.]/g, "");
      const balanceValue = numeric ? Number(numeric) : null;
      return {
        balanceText,
        balanceValue: Number.isFinite(balanceValue) ? balanceValue : null,
      };
    }
  }

  return null;
}

async function extractBalanceNearHeader(page: Page) {
  const creditHeader = await waitForCreditBalance(page, 8000);
  if (!creditHeader) return null;

  // まず親要素周辺から取る
  const parentTexts: string[] = [];

  try {
    const parent = creditHeader.locator("xpath=..");
    parentTexts.push(await parent.innerText());
  } catch {}

  try {
    const grandParent = creditHeader.locator("xpath=../..");
    parentTexts.push(await grandParent.innerText());
  } catch {}

  try {
    const section = creditHeader.locator("xpath=ancestor::div[1]");
    parentTexts.push(await section.innerText());
  } catch {}

  for (const text of parentTexts) {
    const parsed = parseBalanceFromText(text);
    if (parsed) return parsed;
  }

  return null;
}

async function gotoAudioSubscriptionDirect(page: Page) {
  await page.goto(AUDIO_SUBSCRIPTION_URL, { waitUntil: "domcontentloaded" });

  try {
    await page.waitForURL("**/user-center/payment/audio-subscription**", {
      timeout: 15000,
    });
  } catch {
    // SPAやリダイレクトでURL待ちがこけても続行
  }

  await page.waitForLoadState("networkidle").catch(() => {});
}

async function gotoAudioSubscriptionByMenu(page: Page) {
  // ページによってメニュー表示が少し違っても通しやすいように順番に試す
  const clickPatterns = [
    async () => {
      await safeClickByText(page, "Subscribe", 5000);
      await page.waitForTimeout(1000);
      await safeClickByText(page, "Audio", 5000);
    },
    async () => {
      await safeClickByText(page, "Billing", 5000);
      await page.waitForTimeout(1000);
      await safeClickByText(page, "Subscribe", 5000);
      await page.waitForTimeout(1000);
      await safeClickByText(page, "Audio", 5000);
    },
    async () => {
      const audio = page.getByText("Audio", { exact: false }).first();
      await audio.waitFor({ state: "visible", timeout: 5000 });
      await audio.click();
    },
  ];

  let lastError: unknown = null;

  for (const action of clickPatterns) {
    try {
      await action();
      await page.waitForTimeout(1500);
      await page.waitForURL("**/user-center/payment/audio-subscription**", {
        timeout: 10000,
      }).catch(() => {});
      await page.waitForLoadState("networkidle").catch(() => {});
      return;
    } catch (err) {
      lastError = err;
    }
  }

  throw lastError ?? new Error("menu navigation failed");
}

async function ensureLoggedIn(page: Page, email: string, password: string) {
  await page.goto(LOGIN_URL, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => {});

  const currentUrl = page.url();
  if (
    currentUrl.includes("/user-center/") ||
    (await page.locator("text=Basic Information").count().catch(() => 0)) > 0
  ) {
    return;
  }

  // すでにログイン済みでなければログイン処理
  const emailInput = page.locator('input[type="email"], input[placeholder*="Email"]').first();
  const passwordInput = page.locator('input[type="password"]').first();

  await emailInput.waitFor({ state: "visible", timeout: 15000 });
  await emailInput.fill(email);

  await passwordInput.waitFor({ state: "visible", timeout: 15000 });
  await passwordInput.fill(password);

  // ボタンの表記ゆれ対策
  const signInButton = page
    .locator("button")
    .filter({ hasText: /sign in|login|log in/i })
    .first();

  await Promise.all([
    page.waitForLoadState("domcontentloaded").catch(() => {}),
    signInButton.click(),
  ]);

  // ログイン後は basic-information か user-center 配下にいるはず
  await page.waitForURL("**/user-center/**", { timeout: 20000 });
  await page.waitForLoadState("networkidle").catch(() => {});
}

export async function fetchMinimaxAudioCredit(params: {
  email: string;
  password: string;
  headless?: boolean;
}): Promise<MinimaxCreditResult> {
  const browser = await chromium.launch({
    headless: params.headless ?? true,
  });

  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    await ensureLoggedIn(page, params.email, params.password);

    const afterLoginUrl = page.url();
    await logPageSnapshot(page, "AFTER LOGIN");

    // 1. まずURL直移動
    let usedMethod = "direct-url";
    await gotoAudioSubscriptionDirect(page);
    await logPageSnapshot(page, "AFTER DIRECT GOTO");

    let creditHeader = await waitForCreditBalance(page, 8000);

    // 2. 見つからなければメニュー遷移
    if (!creditHeader) {
      usedMethod = "menu-navigation";
      await gotoAudioSubscriptionByMenu(page);
      await logPageSnapshot(page, "AFTER MENU NAVIGATION");
      creditHeader = await waitForCreditBalance(page, 12000);
    }

    if (!creditHeader) {
      const debugPath = await saveDebugFiles(page, "minimax_credit_not_found");
      const preview = (await page.locator("body").innerText().catch(() => "")).slice(0, 1500);

      return {
        ok: false,
        reason: "balance not found",
        afterLoginUrl,
        url: page.url(),
        usedMethod,
        preview,
        debugPath,
      };
    }

    // 3. ヘッダー近辺から取る
    const nearHeader = await extractBalanceNearHeader(page);
    if (nearHeader) {
      return {
        ok: true,
        balanceText: nearHeader.balanceText,
        balanceValue: nearHeader.balanceValue,
        url: page.url(),
        usedMethod,
      };
    }

    // 4. 全文から取る
    const bodyText = await page.locator("body").innerText();
    const parsed = parseBalanceFromText(bodyText);

    if (parsed) {
      return {
        ok: true,
        balanceText: parsed.balanceText,
        balanceValue: parsed.balanceValue,
        url: page.url(),
        usedMethod: `${usedMethod}-body-parse`,
      };
    }

    // 5. それでもだめならデバッグ保存
    const debugPath = await saveDebugFiles(page, "minimax_balance_parse_failed");
    return {
      ok: false,
      reason: "balance text not matched",
      afterLoginUrl,
      url: page.url(),
      usedMethod,
      preview: bodyText.slice(0, 1500),
      debugPath,
    };
  } catch (error) {
    const debugPath = await saveDebugFiles(page, "minimax_exception").catch(() => undefined);
    const preview = await page.locator("body").innerText().catch(() => "");

    return {
      ok: false,
      reason: error instanceof Error ? error.message : "unknown error",
      afterLoginUrl: page.url(),
      url: page.url(),
      preview: preview.slice(0, 1500),
      debugPath,
    };
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
}
