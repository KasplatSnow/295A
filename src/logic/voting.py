"""
K-of-N temporal voting logic with time-decay.

Votes expire after ``max_age_s`` seconds to prevent stale observations
from contributing (e.g. if processing stalls then resumes).
"""
import time
from collections import deque
from typing import Dict, Tuple


class KofNVoter:
    """
    K-of-N temporal confirmation with time decay.

    Keeps timestamped votes; a vote only counts if it arrived within
    ``max_age_s`` (default 15 s) of the current evaluation time.
    """
    
    def __init__(self, k: int = 3, n: int = 5, max_age_s: float = 15.0):
        self.k = k
        self.n = n
        self.max_age_s = max_age_s
        self.buffer: deque = deque(maxlen=n)
    
    def vote(self, trigger: bool) -> Tuple[bool, int]:
        """
        Add a new observation and check if threshold is met.

        Args:
            trigger: Whether current frame triggered
        Returns:
            (confirmed, hits) - confirmed is True if K-of-N threshold met
        """
        now = time.monotonic()
        self.buffer.append((trigger, now))

        # Count only recent and True votes
        cutoff = now - self.max_age_s
        hits = sum(1 for t, ts in self.buffer if t and ts >= cutoff)
        confirmed = hits >= self.k
        return confirmed, hits
    
    def reset(self):
        """Clear the buffer"""
        self.buffer.clear()
    
    def get_stats(self) -> Dict[str, object]:
        """Get current voting statistics"""
        now = time.monotonic()
        cutoff = now - self.max_age_s
        active_hits = sum(1 for t, ts in self.buffer if t and ts >= cutoff)
        return {
            "k": self.k,
            "n": self.n,
            "hits": active_hits,
            "buffer_size": len(self.buffer),
            "max_age_s": self.max_age_s,
        }
