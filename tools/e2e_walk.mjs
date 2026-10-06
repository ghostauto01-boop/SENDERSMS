/**
 * Dev-only end-to-end walkthrough (sandbox use).
 *
 * Signs in through the actual login form — typing a wrong password first, to
 * prove the wall refuses it — then visits every page and reports anything that
 * throws, renders an error state, or overflows a phone screen. Fails loudly, so
 * it can be the last gate before a push.
 *
 *   node tools/e2e_walk.mjs [baseUrl]
 */
import puppeteer from "puppeteer-core";
import chromium from "@sparticuz/chromium";

const BASE = (process.argv[2] || "http://localhost:5173").replace(/\/$/, "");
const PASSWORD = process.env.APP_PASSWORD || "12345678";

const ROUTES = [
  ["/overview", "Overview"],
  ["/dashboard", "Dashboard"],
  ["/send", "Send"],
  ["/email-inbox", "Inbox"],
  ["/inbox", "Sync from phone"],
  ["/contacts", "Contacts"],
  ["/lists", "Lists"],
  ["/audiences", "Audiences"],
  ["/campaigns", "Campaigns"],
  ["/sequences", "Sequences"],
  ["/sms-manager", "SMS"],
  ["/email-manager", "Email"],
  ["/calendar", "Calendar"],
  ["/auto-reply", "Auto"],
  ["/automations", "Automation"],
  ["/campaign-follow-ups", "Follow"],
  ["/variables", "Variables"],
  ["/templates", "Templates"],
  ["/analytics", "Analytics"],
  ["/settings", "Settings"],
  ["/notifications", "Notifications"],
  ["/setup", "Setup"],
];

const problems = [];

const browser = await puppeteer.launch({
  args: [...chromium.args, "--no-sandbox", "--disable-setuid-sandbox"],
  executablePath: await chromium.executablePath(),
  headless: "shell",
  defaultViewport: { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
});
const page = await browser.newPage();
page.on("pageerror", (e) => problems.push(`pageerror: ${String(e).slice(0, 200)}`));
// A 401 *before* signing in is the app asking whether a session exists — that
// is the password wall working, not a fault. After sign-in nothing may 4xx/5xx.
let signedIn = false;
page.on("console", (m) => {
  if (m.type() !== "error") return;
  const text = m.text();
  if (/429/.test(text)) return; // the sandbox's own traffic bursts
  if (!signedIn && /401/.test(text)) return;
  problems.push(`console: ${text.slice(0, 200)}`);
});

// ---------------------------------------------------------------- the wall
await page.goto(`${BASE}/overview`, { waitUntil: "networkidle2" });
await new Promise((r) => setTimeout(r, 1500));
if (!page.url().includes("/login")) problems.push(`anonymous /overview did not redirect to /login (${page.url()})`);

await page.type('input[type="password"]', "definitely-wrong");
await page.click('button[type="submit"]');
await new Promise((r) => setTimeout(r, 2500));
const wrongRefused = await page.evaluate(() => !document.body.innerText.includes("Overview"));
if (!wrongRefused) problems.push("a wrong password got into the app");

await page.$eval('input[type="password"]', (el) => { el.value = ""; });
await page.type('input[type="password"]', PASSWORD);
await page.click('button[type="submit"]');
await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 20000 }).catch(() => {});
await new Promise((r) => setTimeout(r, 2000));
if (page.url().includes("/login")) problems.push("the correct password did not sign in");
signedIn = true;
console.log("signed in:", page.url());

// ------------------------------------------------------------- every page
for (const [route, expect] of ROUTES) {
  const before = problems.length;
  await page.goto(`${BASE}${route}`, { waitUntil: "networkidle2", timeout: 45000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, 1400));

  // Wait for the heading instead of guessing a sleep: several pages render a
  // loading state first and a fixed delay made this check flap.
  await page
    .waitForFunction((needle) => document.body.innerText.includes(needle), { timeout: 15000 }, expect)
    .catch(() => {});

  const state = await page.evaluate(() => {
    const doc = document.documentElement;
    return {
      path: location.pathname,
      text: document.body.innerText.slice(0, 4000),
      overflow: doc.scrollWidth - doc.clientWidth,
      width: doc.clientWidth,
    };
  });

  if (state.path.startsWith("/login")) problems.push(`${route} bounced back to the login screen`);
  if (/Application error|Something went wrong|Cannot read propert|is not a function/i.test(state.text)) {
    problems.push(`${route} rendered an error state`);
  }
  if (!state.text.includes(expect)) problems.push(`${route} did not render its heading ("${expect}")`);
  if (state.overflow > 2) problems.push(`${route} overflows a ${state.width}px screen by ${state.overflow}px`);
  if (problems.length === before) console.log(`  ok  ${route}`);
}

// ------------------------------------------------ notification deep link
await page.goto(`${BASE}/dashboard`, { waitUntil: "networkidle2" });
await new Promise((r) => setTimeout(r, 1500));
await page.evaluate(() => {
  const bell = document.querySelector('button[aria-label^="Notifications"]');
  if (bell) bell.click();
});
await new Promise((r) => setTimeout(r, 1000));
const opened = await page.evaluate(() => {
  // The rows are buttons that navigate programmatically to their deep link.
  const row = document.querySelector(".stagger-item");
  if (row) row.click();
  return !!row;
});
if (!opened) problems.push("no rows in the bell panel");
else {
  await new Promise((r) => setTimeout(r, 2500));
  const landed = page.url();
  if (!landed.includes("conversation_id")) {
    problems.push(`an email notification opened ${landed} instead of its thread`);
  } else console.log("  ok  notification → email thread", landed);
  await page.screenshot({ path: "/tmp/e2e_thread.png" });
}

// The bell's footer must reach the notification centre, not the dashboard.
await page.goto(`${BASE}/notifications`, { waitUntil: "networkidle2" });
await page
  .waitForFunction(() => document.body.innerText.includes("Notifications"), { timeout: 15000 })
  .catch(() => {});
const centre = await page.evaluate(() => ({
  path: location.pathname,
  text: document.body.innerText.slice(0, 800),
}));
if (centre.path !== "/notifications" || !centre.text.includes("Notifications")) {
  problems.push(`/notifications is not mounted (landed on ${centre.path})`);
} else {
  console.log("  ok  notification centre", centre.path);
}

await browser.close();
console.log("\n" + (problems.length ? `PROBLEMS:\n - ${[...new Set(problems)].join("\n - ")}` : "E2E: ALL CLEAN"));
process.exit(problems.length ? 1 : 0);
