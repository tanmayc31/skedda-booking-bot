"""
Cancel a Skedda booking for a given date.

Usage:
    python cancel_booking.py                    # cancels all your bookings for tomorrow
    python cancel_booking.py 2026-04-12         # cancels all your bookings for a specific date
    python cancel_booking.py 2026-04-12 CN-123A # cancels only CN-123A booking on that date
"""

import sys
import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv()
NU_FULLNAME = os.environ["NU_FULLNAME"]
SESSION_DIR = "session_data"


def get_list_url(date_str):
    return (
        f"https://nustudyspaces.skedda.com/booking"
        f"?viewdate={date_str}"
        f"&viewmapid=c8da574c60ce457b8a760bb2a4e7ce37"
        f"&viewtype=2"
    )


def cancel_bookings(target_date=None, room_filter=None):
    """Cancel bookings for a given date. Optionally filter by room name.
    
    Args:
        target_date: date string like '2026-04-12', defaults to tomorrow
        room_filter: optional room name like 'CN-123A' to only cancel that room
    
    Returns:
        list of cancelled booking descriptions
    """
    if target_date is None:
        tomorrow = datetime.now() + timedelta(days=1)
        target_date = tomorrow.strftime("%Y-%m-%d")

    log.info(f"Cancelling bookings for {target_date}" +
             (f" (room: {room_filter})" if room_filter else " (all your bookings)"))

    cancelled = []

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,  # flip to True once verified
            args=["--start-maximized"],
        )
        page = context.new_page()

        # ── Load list view ────────────────────────────────────────────────
        log.info("Loading booking list...")
        try:
            page.goto(get_list_url(target_date), wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            if "login" in page.url.lower():
                log.error("Session expired — run save_session.py again")
                context.close()
                return cancelled
            log.info("Page loaded ✅")
        except PlaywrightTimeout:
            log.error("Timed out loading page")
            context.close()
            return cancelled

        # ── Find your bookings ────────────────────────────────────────────
        # Extract the first name from NU_FULLNAME for matching
        # (Skedda shows full name like "Tanmay Chandan")
        search_name = NU_FULLNAME.strip()
        log.info(f"Looking for bookings by '{search_name}'...")

        your_rows = page.locator(f"div.tr-hover:has-text('{search_name}')").all()

        if not your_rows:
            log.info("No bookings found for you on this date.")
            context.close()
            return cancelled

        log.info(f"Found {len(your_rows)} booking(s):")
        for i, row in enumerate(your_rows):
            txt = row.inner_text().strip().replace("\n", " | ")
            log.info(f"  [{i}] {txt}")

        # ── Filter by room if specified ───────────────────────────────────
        if room_filter:
            your_rows = [r for r in your_rows if room_filter in r.inner_text()]
            if not your_rows:
                log.info(f"No bookings found for room {room_filter}")
                context.close()
                return cancelled
            log.info(f"Filtered to {len(your_rows)} booking(s) for {room_filter}")

        # ── Cancel each booking ───────────────────────────────────────────
        # We cancel one at a time, reloading the page between each
        # because the DOM changes after each cancellation.
        for i in range(len(your_rows)):
            log.info(f"\nCancelling booking {i + 1}...")

            # Reload to get fresh DOM (rows shift after each cancel)
            if i > 0:
                page.goto(get_list_url(target_date), wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)

            # Re-find your bookings
            rows = page.locator(f"div.tr-hover:has-text('{search_name}')").all()
            if room_filter:
                rows = [r for r in rows if room_filter in r.inner_text()]

            if not rows:
                log.info("No more bookings to cancel.")
                break

            row = rows[0]
            booking_text = row.inner_text().strip().replace("\n", " | ")
            log.info(f"  Target: {booking_text}")

            try:
                # Click the booking row to expand it
                row.click()
                page.wait_for_timeout(2000)

                # Click "Manage" button
                manage_btn = page.locator("button:has-text('Manage')").first
                if not manage_btn.is_visible(timeout=5000):
                    log.warning("  Manage button not visible, skipping")
                    continue
                manage_btn.click()
                page.wait_for_timeout(1000)

                # Click "Cancel booking" from dropdown
                cancel_btn = page.locator(".dropdown-menu.show button:has-text('Cancel booking')").first
                if not cancel_btn.is_visible(timeout=3000):
                    log.warning("  Cancel booking button not found, skipping")
                    continue
                cancel_btn.click()
                page.wait_for_timeout(1000)

                # Click "Yes do it" confirmation
                confirm_btn = page.locator("button:has-text('Yes')").first
                if not confirm_btn.is_visible(timeout=5000):
                    # Try other variations
                    confirm_btn = page.locator("button:has-text('Yes do it'), button:has-text('Yes, do it'), button:has-text('Confirm')").first

                if confirm_btn.is_visible(timeout=3000):
                    confirm_btn.click()
                    page.wait_for_timeout(3000)
                    log.info(f"  ✅ Cancelled: {booking_text}")
                    cancelled.append(booking_text)
                else:
                    log.warning("  Could not find confirmation button")

            except Exception as e:
                log.error(f"  Error cancelling: {e}")
                page.screenshot(path=f"cancel_error_{i}.png")

        # ── Summary ───────────────────────────────────────────────────────
        log.info(f"\n{'=' * 50}")
        log.info(f"Cancelled {len(cancelled)} booking(s)")
        for c in cancelled:
            log.info(f"  ✅ {c}")
        if len(cancelled) == 0:
            log.info("  (none)")
        log.info(f"{'=' * 50}")

        context.close()
        return cancelled


if __name__ == "__main__":
    target = None
    room = None

    if len(sys.argv) >= 2:
        target = sys.argv[1]
    if len(sys.argv) >= 3:
        room = sys.argv[2]

    result = cancel_bookings(target_date=target, room_filter=room)
    exit(0 if result else 1)