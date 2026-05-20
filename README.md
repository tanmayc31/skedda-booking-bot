# 🤖 Skedda Booking Bot

An automated study room booking system for Northeastern University's Cullinane Hall, built with Python and Playwright. The bot intelligently books the best available room at the optimal time, with smart conflict resolution, room priority ranking, and automatic retry scheduling.

## 📋 Overview

Northeastern University uses [Skedda](https://nustudyspaces.skedda.com) for study room reservations, with a strict **24-hour advance booking window** and a **4-hour daily booking limit per student**.

This bot eliminates the need to manually race for rooms by automating the entire booking flow — from room selection to conflict resolution — and runs on a cloud VM with scheduled retries to continuously upgrade to better rooms as they become available.

## ✨ Features

### 🧠 Smart Booking Engine
- **Room Priority System**: Ranks 23 rooms by preference — always tries for the best room first
- **Time-Slot Sliding**: Generates 13 possible time windows (3 PM–9 PM) and slides through them to find availability
- **Conflict Parsing**: Reads Skedda's error messages to extract conflict times and intelligently jumps ahead instead of blindly retrying
- **45-Minute Smart Jump**: When a conflict is detected before the requested slot, jumps 45 minutes ahead to skip over long existing bookings
- **Advance-Notice Detection**: Automatically skips rooms hitting the 24-hour booking rule

### 🔄 Orchestrator with Retry Logic
- **Skedda State Check**: Reads the live booking list to detect existing bookings (including manual ones)
- **Fragmented Booking Support**: Groups multiple bookings on the same room and sums total hours — three 1-hour slots on CN-123A = 4 hours, not 1 hour
- **Cancel-and-Rebook**: If a better room becomes available in a later retry, cancels the old booking first (respecting the 4-hour daily limit), then books the upgrade
- **Smart Retry Thresholds**: Only cancels a preferred room if total booked hours < 3h — protects fragmented bookings that effectively hold a room

### 🔐 Authentication
- **Northeastern SSO + Duo 2FA**: One-time manual login saves the browser session
- **Persistent Sessions**: Reuses saved Chromium session data to skip authentication on subsequent runs
- **Session Expiry Detection**: Automatically detects expired sessions and alerts for re-authentication

### ☁️ Cloud Deployment
- **Oracle Cloud Free Tier**: Runs on an always-on Ubuntu 22.04 VM at zero cost
- **Cron Scheduling**: Automated runs at 3, 4, 5, and 6 PM every Friday, Saturday, and Sunday
- **noVNC Remote Access**: Browser-based VNC for session re-authentication when needed
- **Headless Execution**: Runs Chromium in headless mode on the VM with Xvfb virtual display

## 🛠️ Technologies Used

**Languages & Frameworks**

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-1.52+-2EAD33?logo=playwright&logoColor=white)

**Infrastructure**

![Oracle Cloud](https://img.shields.io/badge/Oracle_Cloud-Free_Tier-F80000?logo=oracle&logoColor=white)
![Ubuntu](https://img.shields.io/badge/Ubuntu-22.04-E95420?logo=ubuntu&logoColor=white)
![Cron](https://img.shields.io/badge/Cron-Scheduling-333333?logo=linux&logoColor=white)

**Tools**

![Git](https://img.shields.io/badge/Git-F05032?logo=git&logoColor=white)
![GitHub](https://img.shields.io/badge/GitHub-181717?logo=github&logoColor=white)
![noVNC](https://img.shields.io/badge/noVNC-Remote_Access-4285F4)

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Oracle Cloud VM (Ubuntu 22.04)             │
│                                                              │
│  ┌─────────┐    ┌──────────────┐    ┌───────────────────┐   │
│  │  Cron   │───►│ smart_book.py│───►│   book_room.py    │   │
│  │ Schedule │    │ (Orchestrator)│    │ (Booking Engine)  │   │
│  └─────────┘    └──────┬───────┘    └───────────────────┘   │
│                        │                                     │
│                        ▼                                     │
│                 ┌──────────────┐    ┌───────────────────┐   │
│                 │cancel_booking│    │  save_session.py  │   │
│                 │    .py       │    │ (One-time Auth)   │   │
│                 └──────────────┘    └───────────────────┘   │
│                        │                                     │
│                        ▼                                     │
│                 ┌──────────────┐                             │
│                 │   Skedda     │                             │
│                 │  (Headless   │                             │
│                 │  Chromium)   │                             │
│                 └──────────────┘                             │
└──────────────────────────────────────────────────────────────┘
```

## 🚀 Getting Started

### ✅ Prerequisites
- Python 3.10+
- Playwright with Chromium
- Northeastern University credentials (SSO + Duo 2FA)

### 🔧 Installation

```bash
# Clone the repository
git clone https://github.com/tanmayc31/skedda-booking-bot.git
cd skedda-booking-bot

# Create virtual environment (conda or venv)
conda create -n skedda-bot python=3.11
conda activate skedda-bot

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install --with-deps chromium
```

### ⚙️ Configure Environment Variables

Create a `.env` file:

```env
NU_EMAIL=your_northeastern_email@northeastern.edu
NU_PASSWORD=your_password_here
NU_FULLNAME=Your Name
NU_NUID=0000000000
NU_DEGREE=Graduate
```

### ▶️ Usage

```bash
# Step 1: Save your browser session (one-time, opens browser for SSO + Duo)
python save_session.py

# Step 2: Book a room for tomorrow
python book_room.py

# Step 3: Smart booking with retry logic
python smart_book.py

# Step 4: Cancel a booking
python cancel_booking.py 2026-04-12 CN-123A
```

## 📁 Project Structure

```
skedda-booking-bot/
├── book_room.py          # Core booking engine with smart time-sliding
├── cancel_booking.py     # Automated booking cancellation
├── smart_book.py         # Orchestrator — checks, compares, cancels, rebooks
├── save_session.py       # One-time SSO + Duo 2FA session saver
├── requirements.txt      # Python dependencies
├── .env                  # Credentials (gitignored)
├── .env.example          # Credential template
├── session_data/         # Saved browser session (gitignored)
├── current_booking.json  # State file for orchestrator (gitignored)
└── .gitignore
```

## 💻 Technical Highlights

### 🎯 Intelligent Conflict Resolution
The booking engine doesn't blindly retry — it **parses Skedda's error messages** to extract conflict times and makes informed decisions:
- **Conflict during slot**: Jumps to 15 minutes after the conflict
- **Conflict before slot** (long existing booking): Jumps 45 minutes ahead to skip it entirely
- **Advance-notice violation**: Skips to the next room immediately
- **Duration rule violation**: Skips to the next room (handles Pod 2-hour limits)

### ⏰ Time-Slot Generation Strategy
```
Slot Priority (13 slots total):
1. 3:00 PM – 7:00 PM  (4h, ideal)
2. 3:15 PM – 7:15 PM  (4h, slide)
   ...
9. 5:00 PM – 9:00 PM  (4h, latest full)
10. 5:15 PM – 9:00 PM (3h 45m, shrink)
   ...
13. 6:00 PM – 9:00 PM (3h, minimum)
```

### 🏠 Room Priority System
```
Tier 1 (Preferred):  CN-123A → CN-123B → CN-170A → CN-170B → CN-115
Tier 2 (Acceptable): CN-109 → CN-111 → CN-113 → CN-107 → CN-025
Tier 3 (Fallback):   CN-021 → CN-030J → CN-030H → ... → CN-030A
Excluded:            Pod 1–5 (2-hour max booking limit)
```

### 🔍 Skedda DOM Insights
Key technical discoveries made during development:
- **`viewtype=0`** must be in the URL for the booking button to appear
- Skedda uses **narrow no-break space (`\u202f`)** between time and AM/PM in all dropdowns
- Date picker uses **`aria-label`** on `div[role="button"]` elements, not `<button>` tags
- Standard Playwright `.click()` fails on scrollable dropdowns — requires **JavaScript `scrollIntoView + click`**
- Checkboxes are **`role="checkbox"` divs**, not native `<input>` elements, and must be filtered to exclude room-list checkboxes (31 total in modal, only 3 are form checkboxes)
- Modal selectors must be scoped to **`.modal.show`** to avoid hitting navbar elements

### 🔄 Orchestrator Decision Logic
```
Check Skedda for existing bookings
        │
        ├── No booking → book_room()
        │
        ├── Preferred room + ≥4h → "Already optimal" ✅
        │
        ├── Preferred room + ≥3h → "Keep it" (not worth risk) ✅
        │
        ├── Preferred room + <3h → Cancel → book_room() (try for longer)
        │
        └── Non-preferred room → Cancel → book_room() (try for better room)
```

## ☁️ Cloud Deployment (Oracle Cloud Free Tier)

### VM Setup
```bash
# SSH into the VM
ssh -i ~/Downloads/ssh-key.key ubuntu@<VM_IP>

# Install dependencies
sudo apt update && sudo apt install -y python3 python3-pip xvfb x11vnc novnc websockify
pip3 install playwright python-dotenv
playwright install --with-deps chromium

# Clone and configure
git clone https://github.com/tanmayc31/skedda-booking-bot.git
cd skedda-booking-bot
# Create .env with credentials

# Save session via noVNC (one-time)
Xvfb :99 -screen 0 1280x800x24 &
x11vnc -display :99 -passwd <password> -forever -noxdamage &
websockify --web /usr/share/novnc/ 6080 localhost:5900 &
DISPLAY=:99 python3 save_session.py
```

### Cron Schedule (UTC, converts to 3/4/5/6 PM EDT)
```cron
0 19 * * 5,6,0  cd ~/skedda-booking-bot && python3 smart_book.py >> ~/booking.log 2>&1
0 20 * * 5,6,0  cd ~/skedda-booking-bot && python3 smart_book.py >> ~/booking.log 2>&1
0 21 * * 5,6,0  cd ~/skedda-booking-bot && python3 smart_book.py >> ~/booking.log 2>&1
0 22 * * 5,6,0  cd ~/skedda-booking-bot && python3 smart_book.py >> ~/booking.log 2>&1
```

## 📈 Future Enhancements

- **Slot Fragmentation**: Book multiple smaller slots with gaps to hold a room beyond the 4-hour limit (already done manually — automation planned)
- **Multi-Day Scheduling**: Extend orchestrator to handle week-ahead booking strategies
- **Notification System**: Send booking confirmations and failure alerts via email or Slack
- **Dashboard**: Web UI to view booking history, upcoming reservations, and system health
- **Session Auto-Refresh**: Periodically refresh the browser session before it expires

## 🤝 Contributing

Contributions are welcome! Please feel free to fork the repo, make changes, and submit a Pull Request.

## ✍️ Author

**Tanmay Chandan**

⭐ Star this repo if you found it useful!