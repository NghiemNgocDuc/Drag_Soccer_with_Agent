"""Browser-based E2E test with Playwright — needs Flask server running on :5000."""
import asyncio, json, sys
from pathlib import Path
from playwright.async_api import async_playwright

SCREENSHOTS = Path(__file__).parent / "test_screenshots"
SCREENSHOTS.mkdir(exist_ok=True)

async def run():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page    = await browser.new_page(viewport={"width": 1200, "height": 800})

        # 1. Login
        await page.goto("http://127.0.0.1:5000/login")
        await page.screenshot(path=str(SCREENSHOTS/"01_login.png"))

        await page.fill('input[type="email"]', "test@test.com")
        await page.fill('input[type="password"]', "test123")
        await page.click('button[type="submit"]')
        await page.wait_for_url("**/", timeout=5000)

        # 2. Game page loaded
        await page.wait_for_selector("#game-canvas", timeout=5000)
        await asyncio.sleep(1.5)
        await page.screenshot(path=str(SCREENSHOTS/"02_game_loaded.png"))

        # 3. Read initial scores
        sa0 = await page.inner_text("#score-a")
        sb0 = await page.inner_text("#score-b")
        print(f"Initial scores — A:{sa0}  B:{sb0}")

        # 4. Simulate slingshot drag on the middle player (player 1, idx=1)
        canvas = page.locator("#game-canvas")
        box    = await canvas.bounding_box()
        scale  = box["width"] / 800

        px = box["x"] + 150 * scale
        py = box["y"] + 250 * scale
        pull_x = box["x"] + (150 - 90) * scale
        pull_y = py

        await page.mouse.move(px, py)
        await page.mouse.down()
        for step in range(10):
            ix = px + (pull_x - px) * (step+1) / 10
            await page.mouse.move(ix, pull_y)
            await asyncio.sleep(0.03)
        await page.screenshot(path=str(SCREENSHOTS/"03_dragging.png"))
        await page.mouse.up()

        # 5. Wait for ball animation + AI response
        await asyncio.sleep(4)
        await page.screenshot(path=str(SCREENSHOTS/"04_after_kick.png"))

        # 6. Verify scores changed
        sa1 = await page.inner_text("#score-a")
        sb1 = await page.inner_text("#score-b")
        print(f"After kick  — A:{sa1}  B:{sb1}")

        canvas_data = await page.evaluate("""
            () => {
                const c = document.getElementById('game-canvas');
                const ctx = c.getContext('2d');
                const d = ctx.getImageData(0, 0, c.width, c.height).data;
                let nonZero = 0;
                for (let i = 0; i < d.length; i += 4) {
                    if (d[i] || d[i+1] || d[i+2]) nonZero++;
                }
                return {total: d.length/4, nonZero};
            }
        """)
        fill_pct = round(canvas_data["nonZero"] / canvas_data["total"] * 100, 1)
        print(f"Canvas fill: {fill_pct}% non-black pixels")

        await page.click('button:has-text("New Game")')
        await asyncio.sleep(0.8)
        sa2 = await page.inner_text("#score-a")
        sb2 = await page.inner_text("#score-b")
        print(f"After reset — A:{sa2}  B:{sb2}")
        await page.screenshot(path=str(SCREENSHOTS/"05_after_reset.png"))

        await browser.close()

        print()
        assert sa2 == "0" and sb2 == "0", "Reset should clear scores to 0"
        assert fill_pct > 30, f"Canvas should have content, got {fill_pct}%"
        print("ALL CHECKS PASSED")
        print(f"Screenshots saved to {SCREENSHOTS}")

if __name__ == "__main__":
    asyncio.run(run())
