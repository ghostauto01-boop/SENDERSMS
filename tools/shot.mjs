/**
 * Dev-only screenshot helper (sandbox use; safe to delete).
 *
 * Usage:
 *   node tools/shot.mjs <url> <out.png> [width] [height] [waitMs] [--login]
 *
 * `--login` signs in through /api/v1/auth/login first (password from
 * APP_PASSWORD, default 12345678) and reuses the session cookie, so pages
 * behind the password wall can be captured.
 */
import puppeteer from "puppeteer-core";
import chromium from "@sparticuz/chromium";

const args = process.argv.slice(2);
const useLogin = args.includes("--login");
const [url, out, w = "430", h = "900", waitMs = "3500"] = args.filter((a) => !a.startsWith("--"));
const base = new URL(url).origin;
const password = process.env.APP_PASSWORD || "12345678";

let cookie = null;
if (useLogin) {
  const res = await fetch(`${base}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "admin", password }),
  });
  if (!res.ok) {
    console.error("login failed:", res.status, await res.text());
    process.exit(1);
  }
  const raw = res.headers.getSetCookie?.() || [];
  const session = raw.map((c) => c.split(";")[0]).find((c) => c.startsWith("sendsms_session="));
  if (!session) {
    console.error("no session cookie returned");
    process.exit(1);
  }
  const [name, value] = session.split("=");
  cookie = { name, value, domain: new URL(base).hostname, path: "/" };
}

const browser = await puppeteer.launch({
  args: [...chromium.args, "--no-sandbox", "--disable-setuid-sandbox"],
  executablePath: await chromium.executablePath(),
  headless: "shell",
  defaultViewport: { width: Number(w), height: Number(h), deviceScaleFactor: 1 },
});

const page = await browser.newPage();
if (cookie) await page.setCookie(cookie);
await page.goto(url, { waitUntil: "networkidle2", timeout: 60000 }).catch((e) => console.log("goto:", e.message));
await new Promise((r) => setTimeout(r, Number(waitMs)));
await page.screenshot({ path: out, fullPage: false });
console.log("saved", out);
await browser.close();
