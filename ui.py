#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
from datetime import datetime, timedelta, date
from hijri_converter import Gregorian
from flask import Flask, render_template, request
from flask_socketio import SocketIO
import requests
import re
from bs4 import BeautifulSoup

# ===================== CONFIG =====================
EPIC_URL = "https://epicmasjid.org/"
PRAYERS = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
STATIC_DIR = "static"

# Flask setup
app = Flask(__name__, static_folder=STATIC_DIR, template_folder="templates")
socketio = SocketIO(app, cors_allowed_origins="*")
# ==================================================

@app.route("/trigger-refresh", methods=["POST"])
def trigger_refresh():
    print("[INFO] Received UI refresh trigger.")
    socketio.emit("refresh", {"message": "Refresh triggered"})
    return "OK", 200

@socketio.on("refresh")
def handle_refresh_event(data):
    print(f"[SOCKET] Refresh event received: {data}")

def fetch_epic_adhaan_times():
    """Scrape Epic Masjid prayer times from the website"""
    r = requests.get(EPIC_URL, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    times = {}
    for p in PRAYERS:
        m = re.search(
            rf"\b{re.escape(p)}\b\s+(\d{{1,2}}:\d{{2}}\s*[AP]M)\s+(\d{{1,2}}:\d{{2}}\s*[AP]M)",
            text, re.IGNORECASE
        )
        if m:
            adhan = m.group(1).upper().replace(" ", "")
            times[p] = adhan
    return times

def parse_today_dt(hhmm_ampm: str) -> datetime:
    """Convert scraped time string into datetime today"""
    return datetime.strptime(
        f"{date.today().strftime('%m/%d/%Y')} {hhmm_ampm}",
        "%m/%d/%Y %I:%M%p"
    )

def build_schedule():
    """Return dict {prayer: datetime} sorted by time"""
    times = fetch_epic_adhaan_times()
    schedule = {}
    for p in PRAYERS:
        if p in times:
            schedule[p] = parse_today_dt(times[p])
    return schedule

def get_current_and_next(schedule):
    """Figure out current and next prayer given the schedule"""
    now = datetime.now()
    current = None
    next_p = None

    ordered = sorted(schedule.items(), key=lambda x: x[1])
    for i, (prayer, t) in enumerate(ordered):
        if now >= t:
            current = prayer
            if i + 1 < len(ordered):
                next_p, next_time = ordered[i + 1]
            else:
                next_p, next_time = None, None
        elif now < t and next_p is None:
            next_p, next_time = prayer, t

    if current is None:  # before Fajr
        current = "Isha"
        next_p, next_time = ordered[0]

    return current, next_p, next_time

@app.route("/")
def index():
    schedule = build_schedule()
    today = date.today()
    hijri = Gregorian(today.year, today.month, today.day).to_hijri()
    hijri_date = f"{hijri.day} {hijri.month_name()} {hijri.year} AH"
    gregorian_date = datetime.now().strftime("%A, %B %d")

    current_prayer, next_prayer, next_time = get_current_and_next(schedule)

    # Format times for display
    prayer_times = {p: t.strftime("%I:%M %p") for p, t in schedule.items()}

    # Time until next prayer
    if next_time:
        delta = next_time - datetime.now()
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        time_until = f"{hours}:{minutes:02d}"
    else:
        time_until = "--:--"

    # Choose PNG for current prayer (fajr.png, dhuhr.png, etc.)
    prayer_image = f"{current_prayer.lower()}.png" if current_prayer else "fajr.png"

    return render_template(
        "index.html",
        prayer_times=prayer_times,
        hijri_date=hijri_date,
        gregorian_date=gregorian_date,
        next_prayer=next_prayer,
        time_until=time_until,
        next_time=next_time.strftime("%Y-%m-%d %H:%M:%S") if next_time else None,
        current_prayer=current_prayer,
        prayer_image=prayer_image
    )

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=8000)

