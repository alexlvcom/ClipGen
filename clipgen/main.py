"""Main entry point for ClipGen application."""

import json
import os
import sys
import time
import traceback

import win32api
import win32event
import winerror


def exception_hook(exctype, value, tb):
    """Handle uncaught exceptions by logging to file."""
    with open("error_log.txt", "a", encoding="utf-8") as f:
        f.write(f"\n\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        f.write(f"Exception: {exctype.__name__}: {value}\n")
        f.write("".join(traceback.format_tb(tb)))

    # Call default hook
    sys.__excepthook__(exctype, value, tb)


def prevent_duplicate_instances():
    """Read the startup setting without migrating or writing the config file."""
    if getattr(sys, "frozen", False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    try:
        with open(os.path.join(app_dir, "settings.json"), "r", encoding="utf-8") as f:
            return json.load(f).get("prevent_duplicate_instances", True) is not False
    except (OSError, ValueError, AttributeError):
        return True


def main():
    """Main entry point."""
    mutex = win32event.CreateMutex(None, False, "Local\\ClipGen.SingleInstance")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS and prevent_duplicate_instances():
        win32api.CloseHandle(mutex)
        return

    # Install exception hook
    sys.excepthook = exception_hook

    try:
        from clipgen.app import ClipGenApp

        app = ClipGenApp()
        sys.exit(app.run())

    except Exception as e:
        print(f"Failed to start: {e}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        win32api.CloseHandle(mutex)


if __name__ == "__main__":
    main()
