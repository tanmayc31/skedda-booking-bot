"""
Smart booking orchestrator.

Designed to run multiple times per day (3 PM, 4 PM, 5 PM, 6 PM).
- First run: books the best available slot for tomorrow.
- Later runs: checks if a better slot is now available. If so, cancels
  the old booking first, then rebooks.

IMPORTANT: Skedda enforces a 4-hour-per-day limit. You cannot hold two
bookings simultaneously — must cancel before rebooking.

"Better" means:
  1. Higher priority room (lower index in ROOM_PRIORITY)
  2. Same room priority but longer duration

Usage:
    python smart_book.py
"""

import json
import re
import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from book_room import book_room, ROOM_PRIORITY, NNBSP, time_to_minutes
from cancel_booking import cancel_bookings

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger(__name__)

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
STATE_FILE = "current_booking.json"
PREFERRED_ROOMS = ROOM_PRIORITY[:5]  # CN-123A, CN-123B, CN-170A, CN-170B, CN-115


# ── State management ─────────────────────────────────────────────────────────

def load_current_booking():
    """Load the current booking from state file. Returns dict or None."""
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%B %-d, %Y")
        if data.get("date") != tomorrow:
            log.info(f"Stale booking in state file (date: {data.get('date')}), ignoring")
            return None
        return data
    except (json.JSONDecodeError, KeyError) as e:
        log.warning(f"Could not read state file: {e}")
        return None


def save_current_booking(booking):
    """Save the current booking to state file."""
    with open(STATE_FILE, "w") as f:
        json.dump(booking, f, indent=2)
    log.info(f"Saved booking to {STATE_FILE}")


# ── Booking comparison ───────────────────────────────────────────────────────

def is_better(new, current):
    """Check if new booking is better than current.
    Better = higher priority room, or same priority but longer."""
    if new["room_priority"] < current["room_priority"]:
        return True
    if new["room_priority"] == current["room_priority"] and new["duration_h"] > current["duration_h"]:
        return True
    return False


def is_optimal(booking_summary):
    """Check if booking is the best we could hope for.
    booking_summary has total_hours and room_priority."""
    return booking_summary["room_priority"] < len(PREFERRED_ROOMS) and booking_summary["total_hours"] >= 4.0


def should_retry(booking_summary):
    """Decide if it's worth cancelling to try for better.
    
    Worth retrying if:
    - Room is NOT in top 5 preferred rooms, OR
    - Room IS in top 5 but total hours < 3h (not enough to justify the risk)
    
    NOT worth retrying if:
    - Room IS in top 5 AND total hours >= 3h
    """
    if booking_summary["room"] not in PREFERRED_ROOMS:
        return True
    if booking_summary["total_hours"] < 3.0:
        return True
    return False


# ── Skedda check ─────────────────────────────────────────────────────────────

def parse_booking_row(row_text):
    """Parse a booking row from the list view.
    Example: '3:30 PM–7:30 PM (4h) | CN-123A | Tanmay Chandan | User booking'
    """
    match = re.match(
        r'(\d{1,2}:\d{2}\s*[APap][Mm])\s*[–\-]\s*(\d{1,2}:\d{2}\s*[APap][Mm])\s*\([^)]+\)\s*\|\s*(CN-\w+)',
        row_text
    )
    if not match:
        return None

    start_str = match.group(1).strip()
    end_str = match.group(2).strip()
    room = match.group(3).strip()

    start_mins = time_to_minutes(start_str)
    end_mins = time_to_minutes(end_str)
    duration_h = (end_mins - start_mins) / 60
    room_priority = ROOM_PRIORITY.index(room) if room in ROOM_PRIORITY else 999

    tomorrow_display = (datetime.now() + timedelta(days=1)).strftime("%B %-d, %Y")

    return {
        "room": room,
        "start": start_str,
        "end": end_str,
        "start_mins": start_mins,
        "end_mins": end_mins,
        "duration_h": duration_h,
        "room_priority": room_priority,
        "date": tomorrow_display,
    }


def check_existing_bookings_on_skedda():
    """Check Skedda list view for your existing bookings tomorrow.
    Returns a summary dict with total hours and booking details, or None.
    
    Handles fragmented bookings: if you have multiple bookings on the same room,
    they're treated as one combined booking.
    """
    tomorrow_iso = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow_display = (datetime.now() + timedelta(days=1)).strftime("%B %-d, %Y")
    fullname = os.environ.get("NU_FULLNAME", "").strip()

    log.info(f"Checking Skedda for existing bookings on {tomorrow_iso}...")

    list_url = (
        f"https://nustudyspaces.skedda.com/booking"
        f"?viewdate={tomorrow_iso}"
        f"&viewmapid=c8da574c60ce457b8a760bb2a4e7ce37"
        f"&viewtype=2"
    )

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir="session_data",
            headless=False,
            args=["--start-maximized"],
        )
        page = context.new_page()

        try:
            page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            if "login" in page.url.lower():
                log.error("Session expired — run save_session.py again")
                context.close()
                return None

            your_rows = page.locator(f"div.tr-hover:has-text('{fullname}')").all()

            if not your_rows:
                log.info("No existing bookings found on Skedda.")
                context.close()
                return None

            # Parse all bookings and group by room
            all_bookings = []
            log.info(f"Found {len(your_rows)} existing booking(s):")
            for row in your_rows:
                txt = row.inner_text().strip().replace("\n", " | ")
                log.info(f"  {txt}")
                parsed = parse_booking_row(txt)
                if parsed:
                    all_bookings.append(parsed)

            if not all_bookings:
                context.close()
                return None

            # Group by room and sum hours
            rooms = {}
            for b in all_bookings:
                room = b["room"]
                if room not in rooms:
                    rooms[room] = {
                        "room": room,
                        "bookings": [],
                        "total_hours": 0,
                        "room_priority": b["room_priority"],
                        "date": tomorrow_display,
                    }
                rooms[room]["bookings"].append(b)
                rooms[room]["total_hours"] += b["duration_h"]

            # Log summary per room
            log.info(f"\nBooking summary by room:")
            for room, info in rooms.items():
                count = len(info["bookings"])
                log.info(f"  {room}: {count} booking(s), {info['total_hours']}h total "
                         f"[priority: {info['room_priority']}]")

            # Find the best room (highest priority, then most hours)
            best_room = None
            for room, info in rooms.items():
                if best_room is None:
                    best_room = info
                elif info["room_priority"] < best_room["room_priority"]:
                    best_room = info
                elif (info["room_priority"] == best_room["room_priority"]
                      and info["total_hours"] > best_room["total_hours"]):
                    best_room = info

            context.close()
            return best_room

        except Exception as e:
            log.warning(f"Could not check Skedda: {e}")
            context.close()
            return None


# ── Main orchestrator ────────────────────────────────────────────────────────

def smart_book():
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%B %-d, %Y")
    tomorrow_iso = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%I:%M %p")

    log.info(f"{'=' * 60}")
    log.info(f"SMART BOOK — {now_str}")
    log.info(f"Target date: {tomorrow}")
    log.info(f"{'=' * 60}")

    # ── Step 1: Check what's already booked ───────────────────────────
    current = check_existing_bookings_on_skedda()

    if current:
        save_current_booking(current)

        count = len(current.get("bookings", []))
        log.info(f"\nCurrent booking: {current['room']} — "
                 f"{count} slot(s), {current['total_hours']}h total "
                 f"[priority: {current['room_priority']}]")

        if is_optimal(current):
            log.info("✅ Already have an optimal booking (preferred room, 4h). Done!")
            return True

        if not should_retry(current):
            log.info("✅ Current booking is in a preferred room. Not worth risking a cancel.")
            return True

        # Current booking is suboptimal — worth trying for better
        log.info(f"\nCurrent booking is suboptimal (room not in top 5). Cancelling to try for better...")
        log.info(f"  Room: {current['room']} (priority: {current['room_priority']})")
        log.info(f"  Total hours: {current['total_hours']}h")

        cancelled = cancel_bookings(target_date=tomorrow_iso, room_filter=current["room"])
        if not cancelled:
            log.error("Could not cancel existing booking. Aborting to avoid losing it.")
            return False

        log.info("Old booking cancelled ✅")

    else:
        log.info("\nNo existing booking for tomorrow.")

    # ── Step 2: Book the best available slot ──────────────────────────
    log.info("\nAttempting to book the best available slot...")
    new_booking = book_room()

    if new_booking:
        log.info(f"\n{'=' * 60}")
        log.info(f"✅ RESULT: {new_booking['room']} "
                 f"{new_booking['start']}–{new_booking['end']} ({new_booking['duration_h']}h)")
        log.info(f"{'=' * 60}")
        save_current_booking(new_booking)
        return True
    else:
        log.error("\n❌ Could not book any slot!")
        if current:
            log.error(f"⚠️ WARNING: Previous booking was cancelled but new booking failed!")
            log.error(f"   Lost: {current['room']} {current['start']}–{current['end']}")
            log.error(f"   You may need to rebook manually.")
        return False


if __name__ == "__main__":
    success = smart_book()
    exit(0 if success else 1)