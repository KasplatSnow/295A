"""
FFmpeg-based video reader using subprocess
"""
import cv2
import subprocess
import threading
import time
import os
import numpy as np
from typing import Tuple, Optional
from .base import IngestBackend
from ..common.timeutil import now_iso_utc
from ..common.log import setup_logger


class FFmpegReader(IngestBackend):
    """
    FFmpeg backend using subprocess to decode RTSP streams
    Falls back to OpenCV if subprocess approach fails
    """
    
    def __init__(self, camera_id: str, source: str, reconnect_delay: float = 5.0, 
                 width: int = 640, height: int = 480):
        super().__init__(camera_id, source)
        
        self.reconnect_delay = reconnect_delay
        self._backoff_delay = max(2.0, reconnect_delay)
        self._backoff_max = 60.0
        self._backoff_multiplier = 1.5
        self._capture_options = os.getenv(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS",
            "rtsp_transport;tcp|stimeout;5000000",
        ).strip()
        self.width = width
        self.height = height
        self.logger = setup_logger(f"FFmpegReader-{camera_id}")
        
        self._cap = None  # Fallback to OpenCV
        self._thread = None
        self._lock = threading.Lock()
        self._latest_frame = None
        self._latest_ts = None
        self._connected = False
        self._use_opencv_fallback = True  # Use OpenCV by default (simpler on Windows)
        self._frame_seq = 0
        self._prev_returned_seq = 0
        self._new_frame = threading.Event()
        self._stop_event = threading.Event()

    def _configure_capture(self, cap) -> None:
        if cap is None:
            return
        buffer_size_prop = getattr(cv2, "CAP_PROP_BUFFERSIZE", None)
        if buffer_size_prop is not None:
            try:
                cap.set(buffer_size_prop, 1)
            except Exception:
                pass
    
    def start(self):
        """Start the reader thread"""
        if self._running:
            return
        
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        self.logger.info(f"Started FFmpeg reader for {self.camera_id}")
    
    def stop(self):
        """Stop the reader thread"""
        self._running = False
        self._stop_event.set()
        self._new_frame.set()
        cap = self._cap
        self._cap = None
        if cap:
            try:
                cap.release()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._connected = False
        self.logger.info(f"Stopped FFmpeg reader for {self.camera_id}")
    
    def get_latest(self) -> Tuple[Optional[np.ndarray], Optional[str]]:
        """Get the latest frame (non-blocking)"""
        with self._lock:
            if self._latest_frame is not None and self._frame_seq != self._prev_returned_seq:
                self._prev_returned_seq = self._frame_seq
                return self._latest_frame, self._latest_ts
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
        """Establish connection using OpenCV with FFmpeg backend"""
        try:
            if not self._running or self._stop_event.is_set():
                return False
            if self._cap:
                self._cap.release()
            
            # Use OpenCV with FFmpeg backend (easier on Windows)
            if "://" in self.source and self._capture_options:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = self._capture_options
            self._cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
            self._configure_capture(self._cap)
            
            if self._cap.isOpened():
                ret, frame = self._cap.read()
                if ret and frame is not None:
                    if not self._running or self._stop_event.is_set():
                        self._cap.release()
                        self._cap = None
                        self._connected = False
                        return False
                    self._connected = True
                    self.logger.info(f"Connected to {self.source} via FFmpeg")
                    
                    with self._lock:
                        self._latest_frame = frame
                        self._latest_ts = now_iso_utc()
                        self._frame_seq += 1
                    self._new_frame.set()
                    self._backoff_delay = max(2.0, self.reconnect_delay)
                    
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
                    wait_s = self._backoff_delay
                    self.logger.warning(f"Connection failed, retrying in {wait_s:.1f}s")
                    if self._stop_event.wait(wait_s):
                        break
                    self._backoff_delay = min(self._backoff_delay * self._backoff_multiplier, self._backoff_max)
                    continue
            
            try:
                cap = self._cap
                if cap is None:
                    self._connected = False
                    continue

                ret, frame = cap.read()
                
                if not ret or frame is None:
                    self.logger.warning(f"Failed to read frame, reconnecting...")
                    self._connected = False
                    wait_s = self._backoff_delay
                    if self._stop_event.wait(wait_s):
                        break
                    self._backoff_delay = min(self._backoff_delay * self._backoff_multiplier, self._backoff_max)
                    continue
                
                with self._lock:
                    self._latest_frame = frame
                    self._latest_ts = now_iso_utc()
                    self._frame_seq += 1
                self._new_frame.set()
                self._backoff_delay = max(2.0, self.reconnect_delay)
                
                if self._stop_event.wait(0.001):
                    break
                
            except Exception as e:
                self.logger.error(f"Read error: {e}")
                self._connected = False
                wait_s = self._backoff_delay
                if self._stop_event.wait(wait_s):
                    break
                self._backoff_delay = min(self._backoff_delay * self._backoff_multiplier, self._backoff_max)
