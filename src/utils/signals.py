"""Signal handling utilities."""

import signal
import asyncio
import sys
from typing import Callable, Optional

_cleanup_callback: Optional[Callable] = None

def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully."""
    print("\nShutting down...")
    if _cleanup_callback:
        asyncio.create_task(_cleanup_callback())
    sys.exit(0)

def setup_signal_handlers(cleanup_callback: Optional[Callable] = None) -> None:
    """Setup signal handlers for graceful shutdown."""
    global _cleanup_callback
    _cleanup_callback = cleanup_callback
    signal.signal(signal.SIGINT, signal_handler)