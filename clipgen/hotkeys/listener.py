"""Global hotkey listener using pynput."""

import logging
import threading
from queue import Queue
from typing import Dict, Any, Optional, Set

from pynput import keyboard as pkb

logger = logging.getLogger('ClipGen')


class HotkeyListener:
    """Listens for global hotkeys and dispatches events to queue."""

    def __init__(self, config: Dict[str, Any], event_queue: Queue):
        """Initialize listener.

        Args:
            config: Application config with hotkeys
            event_queue: Queue to put hotkey events
        """
        self.config = config
        self.queue = event_queue

        # Key state tracking
        self.key_states: Dict[str, bool] = {
            "ctrl": False,
            "alt": False,
            "shift": False,
            "meta": False
        }
        self.key_states_lock = threading.Lock()

        # Pasting flag (to ignore hotkeys during paste)
        self.is_pasting = False
        self.pasting_lock = threading.Lock()

        self.listener_lock = threading.Lock()
        self.listener: Optional[pkb.Listener] = None
        self.last_error: str = ""

    def _reset_key_states(self) -> None:
        """Clear tracked modifier states."""
        with self.key_states_lock:
            for key in self.key_states:
                self.key_states[key] = False

    def _remember_error(self, context: str, error: Exception) -> None:
        """Store and log listener errors for later recovery diagnostics."""
        self.last_error = f"{context}: {error}"
        logger.exception(self.last_error)

    def _get_key_name(self, key) -> Optional[str]:
        """Convert pynput key to standardized string."""
        if isinstance(key, pkb.KeyCode):
            return key.char.lower() if key.char else None
        if isinstance(key, pkb.Key):
            name = key.name.lower()
            # Normalize: 'ctrl_l' -> 'ctrl', 'win_r' -> 'meta'
            if name.endswith(('_l', '_r')):
                name = name[:-2]
            if name == 'alt_gr':
                name = 'alt'
            if name in ['cmd', 'win']:
                name = 'meta'
            return name
        return None

    def _on_press(self, key) -> None:
        """Handle key press."""
        try:
            with self.pasting_lock:
                if self.is_pasting:
                    return

            key_name = self._get_key_name(key)
            if not key_name:
                return

            with self.key_states_lock:
                # Modifier key - update state and exit
                if key_name in self.key_states:
                    self.key_states[key_name] = True
                    return

                # Regular key - check for hotkey match
                pressed_modifiers: Set[str] = {
                    mod for mod, pressed in self.key_states.items() if pressed
                }

                for hotkey in self.config.get("hotkeys", []):
                    combo_lower = hotkey["combination"].lower()
                    parts = [p.strip() for p in combo_lower.split('+')]

                    main_key = parts[-1]
                    required_modifiers = set(parts[:-1])

                    if key_name == main_key and pressed_modifiers == required_modifiers:
                        logger.info(f"[{hotkey['combination']}: {hotkey['name']}] Activated")
                        self.queue.put({
                            "action": hotkey["name"],
                            "prompt": hotkey.get("prompt", "")
                        })
                        return

        except Exception as e:
            self._remember_error("Error in on_press", e)

    def _on_release(self, key) -> None:
        """Handle key release."""
        try:
            with self.pasting_lock:
                if self.is_pasting:
                    return

            key_name = self._get_key_name(key)
            if not key_name:
                return

            with self.key_states_lock:
                if key_name in self.key_states:
                    self.key_states[key_name] = False

        except Exception as e:
            self._remember_error("Error in on_release", e)

    def start(self) -> bool:
        """Start the listener."""
        with self.listener_lock:
            if self.listener and self.listener.is_alive():
                return False

            self._reset_key_states()
            self.last_error = ""

            try:
                self.listener = pkb.Listener(
                    on_press=self._on_press,
                    on_release=self._on_release
                )
                self.listener.daemon = True
                self.listener.start()
                logger.info("Hotkey listener started")
                return True
            except Exception as e:
                self.listener = None
                self._remember_error("Failed to start hotkey listener", e)
                return False

    def stop(self) -> None:
        """Stop the listener."""
        with self.listener_lock:
            listener = self.listener
            self.listener = None

        self._reset_key_states()
        self.set_pasting(False)

        if listener:
            try:
                listener.stop()
                listener.join(timeout=1.0)
                logger.info("Hotkey listener stopped")
            except Exception as e:
                self._remember_error("Failed to stop hotkey listener", e)

    def restart(self) -> bool:
        """Restart the listener."""
        self.stop()
        return self.start()

    def is_running(self) -> bool:
        """Return whether the underlying pynput listener is alive."""
        with self.listener_lock:
            return bool(self.listener and self.listener.is_alive())

    def set_pasting(self, is_pasting: bool) -> None:
        """Set pasting flag (to ignore hotkeys during paste)."""
        with self.pasting_lock:
            self.is_pasting = is_pasting

        # Copy/paste simulation can swallow key release events and leave modifiers
        # in a stuck state, so reset them whenever we take control of the keyboard.
        if is_pasting:
            self._reset_key_states()
