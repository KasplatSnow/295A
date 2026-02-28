"""
OpenCV-based video reader (supports RTSP and local files)
"""
import cv2
import threading
import time
import numpy as np
from typing import Tuple, Optional
from .base import IngestBackend
from ..common.timeutil import now_iso_utc
from ..common.log import setup_logger


class OpenCVReader(IngestBackend):
    """OpenCV VideoCapture backend with reconnect logic"""
    
    def __init__(self, camera_id: str, source: str, reconnect_delay: float = 5.0):
        super().__init__(camera_id, source)
        self.reconnect_delay = reconnect_delay
        self.logger = setup_logger(f"OpenCVReader-{camera_id}")
        
        self._cap = None
        self._thread = None
        self._lock = threading.Lock()
        self._latest_frame = None
        self._latest_ts = None
        self._connected = False
        self._frame_seq = 0              # monotonic frame counter
        self._prev_returned_seq = 0      # last seq returned by get_latest
        self._new_frame = threading.Event()  # signalled on new frame
    
    def start(self):
        """Start the reader thread"""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        self.logger.info(f"Started OpenCV reader for {self.camera_id}")
    
    def stop(self):
        """Stop the reader thread"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        if self._cap:
            self._cap.release()
        self.logger.info(f"Stopped OpenCV reader for {self.camera_id}")
    
    def get_latest(self) -> Tuple[Optional[np.ndarray], Optional[str]]:
        """Get the latest frame (non-blocking).

        Returns a COPY to avoid race with the reader thread.
        Returns (None, None) if no frame or the same frame was already
        returned (caller should sleep/wait).
        """
        with self._lock:
            if self._latest_frame is not None and self._frame_seq != self._prev_returned_seq:
                self._prev_returned_seq = self._frame_seq
                return self._latest_frame.copy(), self._latest_ts
            return None, None

    def wait_for_frame(self, timeout: float = 0.5) -> bool:
        """Block until the reader thread has a new frame (or timeout)."""
        got = self._new_frame.wait(timeout=timeout)
        self._new_frame.clear()
        return got
    
    def is_connected(self) -> bool:
        """Check if connected"""
        return self._connected
    
    def _connect(self) -> bool:
        """Establish connection to video source"""
        try:
            if self._cap:
                self._cap.release()
            
            # Try to open with CAP_FFMPEG backend
            self._cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
            
            if not self._cap.isOpened():
                # Fallback to default backend
                self._cap = cv2.VideoCapture(self.source)
            
            if self._cap.isOpened():
                # Test read
                ret, frame = self._cap.read()
                if ret and frame is not None:
                    self._connected = True
                    self.logger.info(f"Connected to {self.source}")
                    
                    # Store first frame
                    with self._lock:
                        self._latest_frame = frame
                        self._latest_ts = now_iso_utc()
                    
                    return True
            
            self._connected = False
            return False
            
        except Exception as e:
            self.logger.error(f"Connection error: {e}")
            self._connected = False
            return False
    
    def _read_loop(self):
        """Main read loop with reconnect logic"""
        while self._running:
            if not self._connected:
                self.logger.info(f"Attempting to connect to {self.source}...")
                if self._connect():
                    self.logger.info(f"Successfully connected")
                else:
                    self.logger.warning(f"Connection failed, retrying in {self.reconnect_delay}s")
                    time.sleep(self.reconnect_delay)
                    continue
            
            try:
                ret, frame = self._cap.read()
                
                if not ret or frame is None:
                    self.logger.warning(f"Failed to read frame, reconnecting...")
                    self._connected = False
                    time.sleep(self.reconnect_delay)
                    continue
                
                # Update latest frame — store directly (cap.read() already
                # returns a fresh array each call, so no copy needed here).
                with self._lock:
                    self._latest_frame = frame
                    self._latest_ts = now_iso_utc()
                    self._frame_seq += 1
                self._new_frame.set()  # wake consumers
                
                # Minimal yield — cap.read() itself blocks sufficiently for
                # RTSP/file sources.  1 ms keeps CPU usage negligible while
                # cutting per-frame idle latency vs the former 10 ms sleep.
                time.sleep(0.001)
                
            except Exception as e:
                self.logger.error(f"Read error: {e}")
                self._connected = False
                time.sleep(self.reconnect_delay)
