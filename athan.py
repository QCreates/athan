#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import json
import threading
from datetime import datetime, date, time as dtime, timedelta
from flask_socketio import SocketIO

import requests
from bs4 import BeautifulSoup
import requests

NTFY_TOPIC = "my_athan"  # same name you used in the app

socketio = SocketIO(message_queue='redis://localhost:6379')

def send_notification(title, message):
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                      data=message.encode("utf-8"),
                      headers={"Title": title})
    except Exception as e:
        print(f"[WARN] Failed to send notification: {e}")

# --- Quran pre-clip uses pygame + mutagen (no compiler needed) ---
try:
    import pygame
    HAVE_PYGAME = True
except Exception as e:
    HAVE_PYGAME = False
    _PG_ERR = str(e)

try:
    from mutagen import File as MutagenFile
    HAVE_MUTAGEN = True
except Exception as e:
    HAVE_MUTAGEN = False
    _MT_ERR = str(e)

# ===================== CONFIG =====================
PRAYERS = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")
EPIC_URL = "https://epicmasjid.org/"

SOUND_GENERAL   = r"./audio/athanfull.mp3"
SOUND_FAJR      = r"./audio/athanfullfajr.mp3"
SOUND_SHORT     = r"./audio/athanshort.mp3"
SOUND_ISHA_PRE  = r"./audio/athkar_masaa.mp3"
SOUND_MORNING   = r"./audio/morning_athkar.mp3"
SOUND_KAHF      = r"./audio/alkahf.mp3"

# Daily Quran pre-clip (10 minutes before each applicable prayer)
DAILY_QURAN     = r"./audio/daily_quran.mp3"
PRECLIP_SECONDS = 600
STATE_FILE      = r"./quran_preclip_state.json"

POLL_SEC = 5
TRIGGER_WINDOW_SEC = 60
# ==================================================

stop_flag = threading.Event()

# ---------- Preclip rotation (daily_quran) ----------
def _get_quran_duration_sec():
    if not HAVE_MUTAGEN:
        print(f"[ERROR] mutagen not available: {_MT_ERR if '_MT_ERR' in globals() else ''}")
        return None
    if not os.path.exists(DAILY_QURAN):
        print(f"[WARN] Missing Quran file: {DAILY_QURAN}")
        return None
    try:
        mf = MutagenFile(DAILY_QURAN)
        return float(mf.info.length) if mf and mf.info else None
    except Exception as e:
        print(f"[ERROR] Failed to read Quran duration: {e}")
        return None

def load_offset():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return float(json.load(f).get("offset_sec", 0.0))
    except Exception:
        pass
    return 0.0

def save_offset(offset):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"offset_sec": float(offset)}, f)
    except Exception as e:
        print(f"[WARN] Could not save offset: {e}")

def reset_quran_offset():
    save_offset(0.0)
    print("[MANUAL] Quran preclip offset reset to 0s (will start from beginning next time)")

def play_quran_segment():
    """
    Plays exactly PRECLIP_SECONDS of daily_quran.mp3 starting at saved offset.
    If the clip ends before the 10 minutes are over, it loops seamlessly.
    Saves the next offset so playback resumes from where it last stopped.
    """
    if not HAVE_PYGAME:
        print(f"[ERROR] pygame not available: {_PG_ERR if '_PG_ERR' in globals() else ''}")
        return
    if not os.path.exists(DAILY_QURAN):
        print(f"[WARN] Missing Quran file: {DAILY_QURAN}")
        return

    # Get total clip duration
    duration = _get_quran_duration_sec()
    if not duration or duration <= 0:
        print("[ERROR] Unknown Quran duration")
        return

    # Load the last saved offset
    offset = load_offset() % duration
    end_time = time.time() + PRECLIP_SECONDS  # Total 10-minute playback window

    def _play_loop():
        nonlocal offset
        print(f"[INFO] Quran Preclip started at offset {int(offset)}s, looping until {PRECLIP_SECONDS//60} minutes are up")

        while time.time() < end_time:
            pygame.mixer.music.load(DAILY_QURAN)
            pygame.mixer.music.play(start=offset)

            # Calculate how long this specific play will run
            remaining_clip_time = duration - offset
            remaining_total_time = end_time - time.time()

            # Only play for the smaller of the two durations
            play_time = min(remaining_clip_time, remaining_total_time)
            time.sleep(play_time)

            pygame.mixer.music.stop()

            # If we finished the clip but still have time left, restart from 0
            offset = 0

        # Save the exact point where playback ended for next session
        next_offset = (offset + (time.time() - (end_time - PRECLIP_SECONDS))) % duration
        save_offset(next_offset)
        print(f"[INFO] Quran preclip finished. Next session will start from {int(next_offset)}s.")

    # Start playback in a background thread
    threading.Thread(target=_play_loop, daemon=True).start()
    send_notification("Athan Pi", f"Now playing looping Quran preclip for {PRECLIP_SECONDS//60} minutes")

# ---------- existing sound handling ----------
def sound_for(prayer: str) -> str:
    return SOUND_FAJR if prayer == "Fajr" else SOUND_GENERAL

def play_sound(path: str):
    if not path:
        return
    if not os.path.exists(path):
        print(f"[WARN] Missing audio file: {path}")
        return

    if HAVE_PYGAME:
        try:
            # Stop anything currently playing
            pygame.mixer.music.stop()
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            send_notification("Athan Pi", f"Now playing: {os.path.basename(path)}")
            print(f"[INFO] Playing: {os.path.basename(path)} (via pygame)")
        except Exception as e:
            print(f"[ERROR] pygame failed to play '{path}': {e}")
    else:
        print("[ERROR] No audio backend available (pygame missing).")


def stop_all_sounds():
    stop_flag.set()
    # Also stop any Quran pre-clip currently playing via pygame
    try:
        if HAVE_PYGAME:
            pygame.mixer.music.stop()
    except Exception:
        pass
    print("[INFO] ESC pressed → stop requested. (playsound clips can’t be interrupted mid-file)")

# ---------- EPIC scraping & schedule ----------
def fetch_epic_adhaan_times():
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
    return datetime.strptime(
        f"{date.today().strftime('%m/%d/%Y')} {hhmm_ampm}",
        "%m/%d/%Y %I:%M%p"
    )

def build_today_schedule():
    """
    Returns a list of (label, datetime, kind, payload).
    kind: "play" (normal mp3 via playsound) or "quran" (5-min pre-clip via pygame).
    """
    times = fetch_epic_adhaan_times()
    schedule = []

    # Five prayers
    for p in PRAYERS:
        if p in times:
            dtm = parse_today_dt(times[p])
            audio = sound_for(p)
            if audio:
                schedule.append((p, dtm, "play", audio))

            # Quran preclip 5 min before each (exclude Isha, and Asr on Fridays)
            if p != "Isha" and not (p == "Asr" and date.today().weekday() == 4):
                pre_dt = dtm - timedelta(minutes=5)
                schedule.append((f"{p}-QuranPre", pre_dt, "quran", None))

    # Isha - 20 minutes
    if "Isha" in times and os.path.exists(SOUND_ISHA_PRE):
        isha_dt = parse_today_dt(times["Isha"])
        pre_dt = isha_dt - timedelta(seconds=19.45*60)
        schedule.append(("Isha-Pre", pre_dt, "play", SOUND_ISHA_PRE))

    # Morning Athkar at 6:30 AM
    morning_dt = datetime.combine(date.today(), dtime(6, 30))
    schedule.append(("Morning-Athkar", morning_dt, "play", SOUND_MORNING))

    # Friday: Kahf at Asr - 31 minutes (your latest change)
    if date.today().weekday() == 4 and "Asr" in times:
        asr_dt = parse_today_dt(times["Asr"])
        kahf_dt = asr_dt - timedelta(minutes=31)
        schedule.append(("Kahf-PreAsr", kahf_dt, "play", SOUND_KAHF))

    schedule.sort(key=lambda t: t[1])
    return schedule

def should_refresh(now: datetime, last_refresh_date: date, refreshed_after_2am: bool) -> bool:
    if last_refresh_date is None:
        return True
    if now.date() != last_refresh_date:
        return True
    if not refreshed_after_2am and now.time() >= dtime(2, 0):
        return True
    return False

# ---------- key watcher ----------
def key_watcher():
    print("[INFO] Controls: Enter = short test, Q = Quran preclip, R = reset Quran offset, Esc = stop")
    while True:
        try:
            line = input().strip().lower()
            if line == "":
                print("[MANUAL] Enter pressed → short test")
                play_sound(SOUND_SHORT)
            elif line == "q":
                print("[MANUAL] Q pressed → Quran preclip test")
                play_quran_segment()
            elif line == "r":
                reset_quran_offset()
            elif line == "esc":
                stop_all_sounds()
        except Exception as e:
            print(f"[WARN] Input watcher error: {e}")
            time.sleep(1)


# ---------- main ----------
def main():
    print("[INFO] Athan daemon starting…")

    # Initialize pygame mixer if available (for Quran pre-clip)
    if HAVE_PYGAME:
        try:
            pygame.mixer.init()
        except Exception as e:
            print(f"[WARN] pygame mixer init failed: {e}")

    for p in (SOUND_GENERAL, SOUND_FAJR, SOUND_SHORT, SOUND_ISHA_PRE, SOUND_MORNING, SOUND_KAHF, DAILY_QURAN):
        if not os.path.exists(p):
            print(f"[WARN] Path not found: {p}")

    threading.Thread(target=key_watcher, daemon=True).start()

    schedule = []
    fired = set()
    last_refresh_date = None
    refreshed_after_2am = False

    # Initial fetch
    while True:
        try:
            schedule = build_today_schedule()
            last_refresh_date = date.today()
            refreshed_after_2am = datetime.now().time() >= dtime(2, 0)
            print("[INFO] Today’s events:")
            for label, t, _, _ in schedule:
                print(f"  - {label}: {t.strftime('%I:%M %p')}")
            break
        except Exception as e:
            print(f"[ERROR] Initial fetch failed: {e}. Retrying in 60s…")
            time.sleep(60)

    while True:
        now = datetime.now()

        if should_refresh(now, last_refresh_date, refreshed_after_2am):
            try:
                schedule = build_today_schedule()
                fired.clear()
                last_refresh_date = date.today()
                refreshed_after_2am = now.time() >= dtime(2, 0)
                print("[INFO] Refreshed events:")
                for label, t, _, _ in schedule:
                    print(f"  - {label}: {t.strftime('%I:%M %p')}")
            except Exception as e:
                print(f"[WARN] Refresh failed: {e}. Will retry in 5 minutes.")
                time.sleep(300)
                continue

        for label, t, kind, payload in schedule:
            key = (label, t.date(), t.time())
            if key in fired:
                continue
            diff = (now - t).total_seconds()
            if 0 <= diff < TRIGGER_WINDOW_SEC:
                print(f"[INFO] {label} time reached ({t.strftime('%I:%M %p')}).")
                if kind == "play":
                    play_sound(payload)
                elif kind == "quran":
                    play_quran_segment()
                socketio.emit("refresh", {"message": "Prayer time reached"}, namespace="/") # Refresh web page
                fired.add(key)

        if not refreshed_after_2am and now.time() >= dtime(2, 0) and now.date() == last_refresh_date:
            refreshed_after_2am = True

        time.sleep(POLL_SEC)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Exiting…")
