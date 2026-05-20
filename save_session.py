from playwright.sync_api import sync_playwright
import os

SESSION_DIR = "session_data"
BOOKING_URL = "https://nustudyspaces.skedda.com/booking?viewmapid=c8da574c60ce457b8a760bb2a4e7ce37"

os.makedirs(SESSION_DIR, exist_ok=True)

print("=" * 55)
print("  SKEDDA SESSION SETUP")
print("=" * 55)
print("Complete login in the browser (5 min timeout).")
print("=" * 55)

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=SESSION_DIR,
        headless=False,
        args=["--start-maximized", "--no-sandbox"],
    )
    page = context.new_page()
    page.goto(BOOKING_URL, wait_until="domcontentloaded", timeout=120000)

    print("\nWaiting for login... approve Duo push on your phone!")
    try:
        page.wait_for_selector('button[title="Make a new booking"]', timeout=300000)
        print("\n✅ Login successful! Saving session...")
        context.close()
        print("✅ Session saved!")
    except Exception as e:
        print(f"❌ Timed out: {e}")
        context.close()
