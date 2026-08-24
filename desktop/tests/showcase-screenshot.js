/*
 * showcase-screenshot.js — capture theokit-showcase.html (light + dark) for
 * visual review. Writes tests/reports/theokit-showcase-{light,dark}.png.
 */
const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

(async () => {
  const outDir = path.join(__dirname, "reports");
  fs.mkdirSync(outDir, { recursive: true });
  const url = "file://" + path.join(__dirname, "..", "src", "theokit-showcase.html");

  const browser = await chromium.launch();
  for (const theme of ["light", "dark"]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(url);
    await page.evaluate((t) => {
      document.documentElement.classList.toggle("dark", t === "dark");
      try { window.localStorage.setItem("theokit-showcase-theme", t); } catch (e) {}
    }, theme);
    await page.waitForTimeout(400);
    // top of page + a mid-section for component variety
    await page.screenshot({ path: path.join(outDir, `theokit-showcase-${theme}-top.png`) });
    await page.evaluate(() => {
      const el = document.getElementById("s-chat");
      if (el) el.scrollIntoView();
    });
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(outDir, `theokit-showcase-${theme}-chat.png`) });
    await page.evaluate(() => {
      const el = document.getElementById("s-tools");
      if (el) el.scrollIntoView();
    });
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(outDir, `theokit-showcase-${theme}-tools.png`) });
    await page.close();
  }
  await browser.close();
  console.log("showcase-screenshot: OK");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
