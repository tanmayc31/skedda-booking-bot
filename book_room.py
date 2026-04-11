import os
import re
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger(__name__)

# ── Credentials from .env (local) or GitHub Secrets (cloud) ──────────────────
load_dotenv()
NU_FULLNAME = os.environ["NU_FULLNAME"]
NU_NUID     = os.environ["NU_NUID"]
NU_DEGREE   = os.environ["NU_DEGREE"]

SESSION_DIR = "session_data"
BOOKING_URL = (
    "https://nustudyspaces.skedda.com/booking"
    "?viewmapid=c8da574c60ce457b8a760bb2a4e7ce37"
    "&viewtype=0"
)

# ── Room priority (no Pods) ──────────────────────────────────────────────────
ROOM_PRIORITY = [
    "CN-123A", "CN-123B", "CN-170A", "CN-170B",
    "CN-115",  "CN-109",  "CN-111",  "CN-113",
    "CN-107",  "CN-025",  "CN-021",  "CN-030J",
    "CN-030H", "CN-030G", "CN-019",  "CN-030F",
    "CN-017",  "CN-030E", "CN-015",  "CN-030D",
    "CN-030C", "CN-030B", "CN-030A",
]

# ── Booking constraints ──────────────────────────────────────────────────────
EARLIEST_START = "3:00 PM"   # ideal start
LATEST_END     = "9:00 PM"   # building closes
MAX_DURATION_H = 4           # max allowed booking
MIN_DURATION_H = 3           # minimum worth booking
NNBSP = "\u202f"             # narrow no-break space (Skedda uses this)


# ── Time helpers ─────────────────────────────────────────────────────────────

def time_to_minutes(t_str):
    """Convert '3:00 PM' to minutes since midnight (900)."""
    t_str = t_str.replace(NNBSP, " ").strip()
    dt = datetime.strptime(t_str, "%I:%M %p")
    return dt.hour * 60 + dt.minute


def minutes_to_skedda(mins):
    """Convert minutes since midnight to Skedda format with narrow no-break space."""
    h = mins // 60
    m = mins % 60
    period = "AM" if h < 12 else "PM"
    display_h = h % 12 or 12
    return f"{display_h}:{m:02d}{NNBSP}{period}"


def generate_time_slots():
    """Generate (start, end) slots in priority order.
    
    Strategy:
    1. Slide start from 3:00 PM forward in 15-min steps, keeping 4h duration
       3:00-7:00, 3:15-7:15, ... 5:00-9:00
    2. Once end hits 9:00 PM, shrink duration down to 3h minimum
       5:15-9:00 (3h45m), 5:30-9:00 (3h30m), 5:45-9:00 (3h15m), 6:00-9:00 (3h)
    """
    earliest = time_to_minutes(EARLIEST_START)  # 900
    latest   = time_to_minutes(LATEST_END)      # 1260
    max_dur  = MAX_DURATION_H * 60              # 240
    min_dur  = MIN_DURATION_H * 60              # 180

    slots = []
    start = earliest
    while start + min_dur <= latest:
        end = min(start + max_dur, latest)
        duration = end - start
        if duration >= min_dur:
            slots.append((start, end))
        start += 15

    return slots


def parse_conflict_time(error_text):
    """Extract the conflict time from Skedda's error message.
    Returns minutes since midnight, or None if unparseable.
    
    Example: '...conflicts with one already scheduled on Tuesday, March 31, 2026, 3:30 PM (CN-123A)...'
    """
    match = re.search(r'(\d{1,2}:\d{2}\s*[APap][Mm])\s*\(', error_text)
    if match:
        return time_to_minutes(match.group(1))
    return None


# ── Playwright helpers ───────────────────────────────────────────────────────

def js_click_dropdown_item(page, value):
    """Click a dropdown item by exact text match using JS."""
    result = page.evaluate("""
        (targetValue) => {
            const menu = document.querySelector('.dropdown-menu.show');
            if (!menu) return { found: false, reason: 'no menu' };
            const items = menu.querySelectorAll('.dropdown-item');
            for (const item of items) {
                if (item.textContent.trim() === targetValue) {
                    const scrollParent = item.closest('.dropdown-menu') || item.parentElement;
                    scrollParent.scrollTop = item.offsetTop - scrollParent.offsetTop;
                    item.scrollIntoView({ block: 'center' });
                    item.click();
                    return { found: true };
                }
            }
            return { found: false, reason: 'no match', count: items.length };
        }
    """, value)
    return result.get("found", False)


def scroll_modal(page, to="bottom"):
    """Scroll the modal body to top or bottom."""
    modal_body = page.locator(".modal.show .modal-body").first
    if to == "bottom":
        modal_body.evaluate("el => el.scrollTop = el.scrollHeight")
    else:
        modal_body.evaluate("el => el.scrollTop = 0")
    page.wait_for_timeout(400)


def take_debug_screenshot(page, filename="booking_debug.png"):
    try:
        scroll_modal(page, to="top")
        page.screenshot(path=filename, full_page=True)
        log.info(f"Screenshot saved: {filename}")
    except Exception:
        try:
            page.screenshot(path=filename)
        except Exception:
            pass


def set_time_dropdown(page, modal, filter_text, target_time_skedda):
    """Open a time dropdown and select a value. Returns True on success."""
    try:
        modal.locator(".dropdown-toggle").filter(has_text=filter_text).first.click()
        page.wait_for_timeout(600)
        if js_click_dropdown_item(page, target_time_skedda):
            page.wait_for_timeout(300)
            return True
        log.warning(f"  Could not find '{target_time_skedda}' in dropdown")
        return False
    except Exception as e:
        log.warning(f"  Dropdown error: {e}")
        return False


def switch_room(page, modal, old_room, new_room):
    """Deselect old room and select new room. Returns True on success."""
    try:
        # Click the spaces dropdown (shows current room name)
        modal.locator(".dropdown-toggle").filter(has_text=old_room).first.click()
        page.wait_for_timeout(600)
        js_click_dropdown_item(page, old_room)  # deselect
        page.wait_for_timeout(300)

        # Reopen if closed
        if page.locator(".dropdown-menu.show").count() == 0:
            modal.locator(".dropdown-toggle").filter(has_text="No spaces selected").first.click()
            page.wait_for_timeout(600)

        if js_click_dropdown_item(page, new_room):
            page.wait_for_timeout(300)
            return True
        return False
    except Exception as e:
        log.warning(f"  Room switch error: {e}")
        return False


def set_time_slot(page, modal, start_mins, end_mins, current_start_text, current_end_text):
    """Update start and end time dropdowns. Returns (new_start_text, new_end_text) or None on failure."""
    new_start = minutes_to_skedda(start_mins)
    new_end = minutes_to_skedda(end_mins)

    # Only change start if different
    if new_start != current_start_text:
        if not set_time_dropdown(page, modal, current_start_text.replace(NNBSP, " ").split()[0], new_start):
            # Fallback: match on "From" or the current time text
            modal.locator(".dropdown-toggle").filter(has_text="From").first.click()
            page.wait_for_timeout(600)
            if not js_click_dropdown_item(page, new_start):
                return None

    # Only change end if different
    if new_end != current_end_text:
        if not set_time_dropdown(page, modal, "to", new_end):
            return None

    return (new_start, new_end)


# ── Main Booking Flow ────────────────────────────────────────────────────────

def book_room():
    tomorrow = datetime.now() + timedelta(days=1)
    target_date = tomorrow.strftime("%B %-d, %Y")
    aria_label = tomorrow.strftime("%A, %B %-d, %Y")
    log.info(f"Starting smart booking for {target_date}")

    all_slots = generate_time_slots()

    if not all_slots:
        log.error("No valid time slots generated.")
        return False

    log.info(f"Generated {len(all_slots)} time slots")
    log.info(f"  First: {minutes_to_skedda(all_slots[0][0])}–{minutes_to_skedda(all_slots[0][1])}")
    log.info(f"  Last:  {minutes_to_skedda(all_slots[-1][0])}–{minutes_to_skedda(all_slots[-1][1])}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,  # flip to True once verified
            args=["--start-maximized"],
        )
        page = context.new_page()

        # ── Step 1: Load booking page ─────────────────────────────────────
        log.info("Loading booking page...")
        try:
            page.goto(BOOKING_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            if "login" in page.url.lower():
                log.error("Session expired — run save_session.py again")
                context.close()
                return False
            log.info("Logged in ✅")
        except PlaywrightTimeout:
            log.error("Timed out loading booking page")
            context.close()
            return False

        # ── Step 2: Open booking modal ────────────────────────────────────
        log.info("Opening new booking modal...")
        try:
            plus_btn = page.locator('button[title="Make a new booking"]')
            plus_btn.wait_for(state="visible", timeout=15000)
            plus_btn.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            plus_btn.click()
            page.wait_for_selector("text=New booking", timeout=10000)
            page.wait_for_timeout(1000)
            log.info("Booking modal opened ✅")
        except PlaywrightTimeout:
            log.error("Could not open booking modal")
            context.close()
            return False

        modal = page.locator(".modal.show").first

        # ── Step 3: Set date ──────────────────────────────────────────────
        log.info(f"Setting date to {target_date}...")
        try:
            modal.locator(".dropdown-toggle").filter(has_text="2026").first.click()
            page.wait_for_timeout(800)
            clicked = page.evaluate(
                """(label) => {
                    const el = document.querySelector('.dropdown-menu.show div.day[aria-label="' + label + '"]');
                    if (el) { el.click(); return true; }
                    return false;
                }""", aria_label
            )
            if not clicked:
                log.error(f"Could not find date '{aria_label}'")
                context.close()
                return False
            page.wait_for_timeout(500)
            log.info("Date set ✅")
        except Exception as e:
            log.error(f"Could not set date: {e}")
            context.close()
            return False

        # ── Step 4: Set initial time slot (first slot: 3 PM – 7 PM) ──────
        first_start, first_end = all_slots[0]
        start_skedda = minutes_to_skedda(first_start)
        end_skedda = minutes_to_skedda(first_end)

        log.info(f"Setting initial time: {start_skedda} – {end_skedda}...")
        try:
            modal.locator(".dropdown-toggle").filter(has_text="From").first.click()
            page.wait_for_timeout(800)
            js_click_dropdown_item(page, start_skedda)
            page.wait_for_timeout(500)

            modal.locator(".dropdown-toggle").filter(has_text="to").first.click()
            page.wait_for_timeout(800)
            js_click_dropdown_item(page, end_skedda)
            page.wait_for_timeout(500)
            log.info(f"Time set: {start_skedda} – {end_skedda} ✅")
        except Exception as e:
            log.error(f"Could not set time: {e}")
            context.close()
            return False

        current_start = start_skedda
        current_end = end_skedda
        current_start_mins = first_start
        current_end_mins = first_end

        # ── Step 5: Select first room ─────────────────────────────────────
        log.info(f"Selecting first room: {ROOM_PRIORITY[0]}...")
        try:
            modal.locator(".dropdown-toggle").filter(has_text="No spaces selected").first.click()
            page.wait_for_timeout(800)
            js_click_dropdown_item(page, ROOM_PRIORITY[0])
            page.wait_for_timeout(500)
            log.info(f"Room {ROOM_PRIORITY[0]} selected ✅")
        except Exception as e:
            log.error(f"Could not select room: {e}")
            context.close()
            return False

        current_room = ROOM_PRIORITY[0]

        # ── Step 6: Fill personal details ─────────────────────────────────
        log.info("Filling personal details...")
        try:
            # Full Name & NUID
            filled = {"name": False, "nuid": False}
            for inp in modal.locator("input[type='text']").all():
                if not inp.is_visible():
                    continue
                ph = (inp.get_attribute("placeholder") or "").lower()
                label_text = inp.evaluate("""el => {
                    const id = el.id;
                    if (id) { const lbl = document.querySelector('label[for=\"' + id + '\"]'); if (lbl) return lbl.textContent.toLowerCase(); }
                    const parent = el.closest('.form-group, .mb-3, .field');
                    if (parent) { const lbl = parent.querySelector('label'); if (lbl) return lbl.textContent.toLowerCase(); }
                    return '';
                }""")
                ctx = ph + " " + label_text
                if not filled["name"] and "name" in ctx and "title" not in ctx:
                    inp.fill(NU_FULLNAME)
                    filled["name"] = True
                elif not filled["nuid"] and "nuid" in ctx:
                    inp.fill(NU_NUID)
                    filled["nuid"] = True

            if not filled["name"] or not filled["nuid"]:
                visible = [i for i in modal.locator("input[type='text']").all() if i.is_visible()]
                for inp in visible:
                    ph = (inp.get_attribute("placeholder") or "").lower()
                    if "title" in ph:
                        continue
                    if not filled["name"]:
                        inp.fill(NU_FULLNAME)
                        filled["name"] = True
                    elif not filled["nuid"]:
                        inp.fill(NU_NUID)
                        filled["nuid"] = True

            # Degree
            degree_opened = False
            for label in ["Graduate or Undergraduate", "Graduate", "Undergraduate", "Select"]:
                try:
                    dd = modal.locator(".dropdown-toggle").filter(has_text=label).first
                    if dd.is_visible(timeout=1000):
                        dd.click()
                        degree_opened = True
                        break
                except Exception:
                    continue
            if not degree_opened:
                for dd in modal.locator(".dropdown-toggle").all():
                    if dd.inner_text().strip() in ("", "Select...", "Select"):
                        dd.click()
                        degree_opened = True
                        break
            if degree_opened:
                page.wait_for_timeout(500)
                js_click_dropdown_item(page, NU_DEGREE)
                page.wait_for_timeout(400)

            # Guests
            try:
                guests = modal.locator("textarea").first
                if guests.is_visible(timeout=2000):
                    guests.fill("None")
            except Exception:
                pass

            log.info("Personal details filled ✅")
        except Exception as e:
            log.error(f"Could not fill details: {e}")
            context.close()
            return False

        # ── Step 7: Checkboxes ────────────────────────────────────────────
        log.info("Checking checkboxes...")
        try:
            scroll_modal(page, to="bottom")
            page.wait_for_timeout(500)
            result = page.evaluate("""
                (() => {
                    const modal = document.querySelector('.modal.show');
                    if (!modal) return { count: 0 };
                    const cbs = Array.from(modal.querySelectorAll('[role="checkbox"]'))
                                     .filter(cb => !cb.closest('.dropdown-menu'));
                    let clicked = 0;
                    for (const cb of cbs) {
                        if (cb.getAttribute('aria-checked') !== 'true') {
                            cb.scrollIntoView({ block: 'center' });
                            cb.click();
                            clicked++;
                        }
                    }
                    return { count: clicked, total: cbs.length };
                })()
            """)
            log.info(f"Checked {result.get('count', 0)} checkboxes ✅")
        except Exception as e:
            log.warning(f"Checkbox error: {e}")

        # ── Step 8: Try rooms × time slots ────────────────────────────────
        log.info("=" * 60)
        log.info("STARTING ROOM × TIME SEARCH")
        log.info("=" * 60)

        for room_idx, room in enumerate(ROOM_PRIORITY):
            if room != current_room:
                log.info(f"\nSwitching to {room}...")
                if not switch_room(page, modal, current_room, room):
                    log.warning(f"  Could not switch to {room}, skipping")
                    continue
                current_room = room

            # Reset to first time slot for each new room
            slot_idx = 0

            while slot_idx < len(all_slots):
                s_mins, e_mins = all_slots[slot_idx]
                s_skedda = minutes_to_skedda(s_mins)
                e_skedda = minutes_to_skedda(e_mins)
                duration_h = (e_mins - s_mins) / 60

                log.info(f"\n  [{room}] Trying {s_skedda}–{e_skedda} ({duration_h:.1f}h)...")

                # Update time if changed
                if s_mins != current_start_mins or e_mins != current_end_mins:
                    # Update start time
                    if s_mins != current_start_mins:
                        modal.locator(".dropdown-toggle").filter(has_text=current_start.split(NNBSP)[0]).first.click()
                        page.wait_for_timeout(600)
                        js_click_dropdown_item(page, s_skedda)
                        page.wait_for_timeout(300)

                    # Update end time
                    if e_mins != current_end_mins:
                        modal.locator(".dropdown-toggle").filter(has_text="to").first.click()
                        page.wait_for_timeout(600)
                        js_click_dropdown_item(page, e_skedda)
                        page.wait_for_timeout(300)

                    current_start = s_skedda
                    current_end = e_skedda
                    current_start_mins = s_mins
                    current_end_mins = e_mins

                # Click Confirm
                try:
                    confirm_btn = page.locator(".modal.show button:has-text('Confirm booking')").first
                    confirm_btn.scroll_into_view_if_needed()
                    page.wait_for_timeout(200)
                    confirm_btn.click()
                    page.wait_for_timeout(4000)
                except Exception as e:
                    log.error(f"  Could not click Confirm: {e}")
                    break

                # Check result
                modal_gone = page.locator(".modal.show").count() == 0
                error_loc = page.locator(
                    ".alert-danger, .alert-error, [class*='error'], .modal.show .text-danger"
                )
                has_error = error_loc.count() > 0

                if modal_gone:
                    log.info(f"\n{'=' * 60}")
                    log.info(f"✅ BOOKED: {room} on {target_date} {s_skedda}–{e_skedda} ({duration_h:.1f}h)")
                    log.info(f"{'=' * 60}")
                    take_debug_screenshot(page, "booking_success.png")
                    context.close()
                    return True

                if has_error:
                    try:
                        error_text = error_loc.first.inner_text()
                    except Exception:
                        error_text = ""
                    log.warning(f"  ⚠️ Failed: {error_text[:120]}")

                    # Dismiss error
                    try:
                        page.locator(".modal.show button:has-text('Close'), .modal.show .btn-close, .modal.show button:has-text('OK')").first.click(timeout=2000)
                        page.wait_for_timeout(500)
                    except Exception:
                        pass

                    if "conflict" in error_text.lower():
                        conflict_mins = parse_conflict_time(error_text)
                        if conflict_mins is not None:
                            if conflict_mins < s_mins:
                                # Conflict starts BEFORE our slot — we don't know when it ends.
                                # Jump by 45 min to avoid slow crawling through a long booking.
                                new_start = s_mins + 45
                                log.info(f"  → Conflict at {minutes_to_skedda(conflict_mins)} is before our start, jumping +45min")
                            else:
                                # Conflict starts DURING our slot — jump to 15 min after it
                                new_start = conflict_mins + 15

                            # Find the next slot that starts at or after new_start
                            jumped = False
                            for next_idx in range(slot_idx + 1, len(all_slots)):
                                if all_slots[next_idx][0] >= new_start:
                                    slot_idx = next_idx
                                    jumped = True
                                    log.info(f"  → Jumping to {minutes_to_skedda(all_slots[next_idx][0])}")
                                    break
                            if jumped:
                                continue
                        # If can't jump or no parseable time, just advance one slot
                        slot_idx += 1
                        continue

                    elif "not allowed" in error_text.lower() or "advance-notice" in error_text.lower():
                        # Duration/rule/advance-notice error — skip to next room
                        log.info(f"  → Rule violation, skipping {room}")
                        break
                    else:
                        # Unknown error — advance one slot
                        slot_idx += 1
                        continue
                else:
                    # No error, no modal close — ambiguous
                    log.warning("  ⚠️ Ambiguous result — check screenshot")
                    take_debug_screenshot(page, "booking_ambiguous.png")
                    context.close()
                    return True

        # Exhausted all rooms
        log.error("\n❌ Could not book any room — all slots taken or unavailable")
        take_debug_screenshot(page, "all_failed.png")
        context.close()
        return False


if __name__ == "__main__":
    success = book_room()
    exit(0 if success else 1)