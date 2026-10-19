# Athan

A local prayer-time and audio project built with Python. It reads the daily adhan times displayed by [EPIC Masjid](https://epicmasjid.org/), schedules audio playback, and offers a separate Flask dashboard for the current and next prayer.

## What runs

- `athan.py` refreshes the source schedule, plays the included audio files at configured times, and can send notifications through `ntfy.sh`.
- `ui.py` serves a dashboard with prayer times and a Hijri date. The scheduler can call its local refresh endpoint when the display needs to update.
- `audio/`, `static/`, and `templates/` contain the media and interface assets used by those processes.

The parser depends on the external site's page text. If that site changes its format or is unreachable, time retrieval may fail. The schedule uses the machine's local clock.

## Run locally

Create a Python virtual environment and install the listed packages:

```sh
python -m venv .venv
python -m pip install -r requirements.txt
```

Run the scheduler and dashboard in separate terminals from the repository root:

```sh
python athan.py
python ui.py
```

The dashboard is configured for port `8000` and currently binds to all network interfaces. Use it only on a trusted network unless you change that setting. Audio playback requires a working sound device. Review the notification topic in `athan.py` before running it; the current value is in source control.

This is a personal project with environment-specific assumptions, not a hosted prayer-time service. No license file is currently included.
