"""Replit build entry point; uses argv arrays, never shell interpolation."""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
os.chdir(root)
browser = os.getenv("BROWSER_ENABLED", "false").lower() == "true"
requirements = "requirements-browser.txt" if browser else "requirements.txt"
subprocess.run([sys.executable, "-m", "pip", "install", "-r", requirements], check=True)
if browser and not os.getenv("CHROMIUM_EXECUTABLE"):
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
subprocess.run([sys.executable, "tools/test_site.py", "4khdhub", "--fixtures"], check=True)
