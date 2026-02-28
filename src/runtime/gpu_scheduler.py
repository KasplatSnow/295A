"""
GPU Scheduler — single point of arbitration for GPU inference.

Priority queue with preemption:
  Priority 0: RT-DETR (primary detector) — NEVER dropped
  Priority 1: fire/smoke YOLO
  Priority 2: temporal verifier (X3D/VideoSwin) when armed
  Priority 3: AnyAnomaly / AnomalyCLIP

Policies:
  - RT-DETR always preempts lower-priority tasks
  - When queue near full: drop AnyAnomaly first, then AnomalyCLIP
  - Never drop RT-DETR; instead reduce sampling via auto-throttle
  - Per-lane budget enforcement (max_latency_ms, max_runs_per_cam_per_min, drop_if_busy)
"""

import time
import threading
import heapq
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional
from concurrent.futures import Future

from ..common.log import setup_logger


# Lane → default priority
LANE_PRIORITY = {
    "rt_detr": 0,
    "yolov8_fallback": 0,
    "fire_smoke_yolo": 1,
    "fire_smoke": 1,
    "person_zone": 1,
    "temporal_verifier": 2,
    "anyanomaly": 3,
    "anomalyclip": 3,
}

# Lane name → budget config key
LANE_TO_BUDGET_KEY = {
    "rt_detr": "rtdetr",
    "yolov8_fallback": "rtdetr",
    "fire_smoke_yolo": "fire_smoke",
    "fire_smoke": "fire_smoke",
    "temporal_verifier": "temporal_verifier",
    "anyanomaly": "anyanomaly",
    "anomalyclip": "anyanomaly",
    "person_zone": "fire_smoke",
}


@dataclass(order=True)
class GPUTask:
    """A GPU inference task with priority ordering."""
    priority: int
    submit_time: float = field(compare=False)
    lane: str = field(compare=False)
    camera_id: str = field(compare=False)
    fn: Callable = field(compare=False, repr=False)
    args: tuple = field(compare=False, default=(), repr=False)
    kwargs: dict = field(compare=False, default_factory=dict, repr=False)
    future: Future = field(compare=False, default_factory=Future, repr=False)
    _seq: int = field(default=0)  # tie-breaker for same priority


class GPUScheduler:
    """
    Priority-queue GPU scheduler with per-lane budgets and backpressure.
    """

    def __init__(self, runtime_cfg: Dict[str, Any]):
        self.logger = setup_logger("GPUScheduler")

        gpu_cfg = runtime_cfg.get("gpu", {})
        self.max_queue = gpu_cfg.get("max_queue", 64)
        self.max_inflight = gpu_cfg.get("max_inflight", 2)   # allow overlap

        self.budgets = runtime_cfg.get("budgets", {})

        # Priority queue (min-heap)
        self._queue: list = []
        self._queue_lock = threading.Lock()
        self._seq = 0

        # Condition variable — replaces polling sleep for zero-latency wakeup
        self._cond = threading.Condition(self._queue_lock)

        # Execution
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        self._running = False
        self._exec_thread: Optional[threading.Thread] = None

        # Per-lane rate tracking: lane → camera_id → deque of timestamps
        self._run_timestamps: Dict[str, Dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=120))
        )

        # Stats
        self.stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "total_runs": 0,
            "dropped_count": 0,
            "total_ms": 0.0,
            "latencies": [],  # last 100 latencies for p95 calc
        })

    # ------------------------------------------------------------------
    def start(self):
        """Start the GPU execution loop."""
        self._running = True
        self._exec_thread = threading.Thread(target=self._execution_loop, daemon=True)
        self._exec_thread.start()
        self.logger.info(f"GPU scheduler started (max_queue={self.max_queue}, max_inflight={self.max_inflight})")

    def stop(self):
        """Stop the GPU execution loop."""
        self._running = False
        if self._exec_thread:
            self._exec_thread.join(timeout=5.0)
        self.logger.info("GPU scheduler stopped")

    # ------------------------------------------------------------------
    def submit(self, lane: str, camera_id: str,
               fn: Callable, *args, **kwargs) -> Optional[Future]:
        """
        Submit a GPU inference task.
        Returns a Future, or None if the task was dropped by budget policy.
        """
        priority = LANE_PRIORITY.get(lane, 3)

        # Check per-lane budget before queueing
        if not self._check_budget(lane, camera_id):
            self.stats[lane]["dropped_count"] += 1
            self.logger.debug(f"Dropped {lane}/{camera_id} — budget exceeded")
            return None

        # Backpressure: if queue full, drop lowest priority (highest number)
        with self._queue_lock:
            if len(self._queue) >= self.max_queue:
                if not self._evict_lowest_priority(priority):
                    self.stats[lane]["dropped_count"] += 1
                    self.logger.debug(f"Dropped {lane}/{camera_id} — queue full, can't evict")
                    return None

            self._seq += 1
            future = Future()
            task = GPUTask(
                priority=priority,
                submit_time=time.time(),
                lane=lane,
                camera_id=camera_id,
                fn=fn,
                args=args,
                kwargs=kwargs,
                future=future,
                _seq=self._seq,
            )
            heapq.heappush(self._queue, task)
            self._cond.notify()   # wake execution loop immediately

        return future

    # ------------------------------------------------------------------
    def _check_budget(self, lane: str, camera_id: str) -> bool:
        """Check per-lane budget limits."""
        budget_key = LANE_TO_BUDGET_KEY.get(lane, lane)
        budget = self.budgets.get(budget_key, {})

        # Check max_runs_per_cam_per_min
        max_rpm = budget.get("max_runs_per_cam_per_min")
        if max_rpm is not None:
            now = time.time()
            stamps = self._run_timestamps[lane][camera_id]
            # Pop expired timestamps from front (monotonic order)
            cutoff = now - 60.0
            while stamps and stamps[0] < cutoff:
                stamps.popleft()
            if len(stamps) >= max_rpm:
                return False

        # Check drop_if_busy
        if budget.get("drop_if_busy", False):
            with self._inflight_lock:
                if self._inflight >= self.max_inflight:
                    return False

        return True

    # ------------------------------------------------------------------
    def _evict_lowest_priority(self, incoming_priority: int) -> bool:
        """
        Evict the lowest-priority (highest number) task to make room.
        Only evicts if that task has strictly lower priority than incoming.
        Never evicts RT-DETR (priority 0).
        """
        if not self._queue:
            return False

        # Find worst (highest priority number) task
        worst_idx = -1
        worst_prio = -1
        for i, task in enumerate(self._queue):
            if task.priority > worst_prio:
                worst_prio = task.priority
                worst_idx = i

        if worst_prio > incoming_priority and worst_prio > 0:
            evicted = self._queue.pop(worst_idx)
            heapq.heapify(self._queue)
            evicted.future.cancel()
            self.stats[evicted.lane]["dropped_count"] += 1
            self.logger.debug(f"Evicted {evicted.lane}/{evicted.camera_id} (prio={evicted.priority})")
            return True

        return False

    # ------------------------------------------------------------------
    def _execution_loop(self):
        """Main GPU execution loop — pops tasks in priority order.

        Uses a Condition variable for zero-latency wakeup instead of
        polling sleep.  Supports max_inflight > 1 so multiple CUDA
        streams can overlap compute and memory transfers.
        """
        while self._running:
            task = None
            with self._cond:  # acquires _queue_lock
                # Wait until there is a task AND an inflight slot
                while self._running:
                    can_run = False
                    if self._queue:
                        with self._inflight_lock:
                            if self._inflight < self.max_inflight:
                                can_run = True
                    if can_run:
                        break
                    self._cond.wait(timeout=0.1)  # re-check periodically

                if not self._running:
                    break

                if self._queue:
                    with self._inflight_lock:
                        if self._inflight < self.max_inflight:
                            task = heapq.heappop(self._queue)
                            self._inflight += 1

            if task is None:
                continue

            # Execute
            t0 = time.perf_counter()
            try:
                result = task.fn(*task.args, **task.kwargs)
                task.future.set_result(result)
            except Exception as e:
                task.future.set_exception(e)
                self.logger.error(f"GPU task error ({task.lane}): {e}")
            finally:
                dt_ms = (time.perf_counter() - t0) * 1000
                with self._inflight_lock:
                    self._inflight -= 1
                # Notify waiting threads that a slot freed up
                with self._cond:
                    self._cond.notify()

                # Record stats
                stats = self.stats[task.lane]
                stats["total_runs"] += 1
                stats["total_ms"] += dt_ms
                stats["latencies"].append(dt_ms)
                if len(stats["latencies"]) > 100:
                    stats["latencies"] = stats["latencies"][-100:]

                # Record run timestamp for rate limiting
                self._run_timestamps[task.lane][task.camera_id].append(time.time())

                # Budget enforcement: warn if over max_latency_ms
                budget_key = LANE_TO_BUDGET_KEY.get(task.lane, task.lane)
                budget = self.budgets.get(budget_key, {})
                max_lat = budget.get("max_latency_ms")
                if max_lat and dt_ms > max_lat:
                    self.logger.warning(
                        f"{task.lane} latency {dt_ms:.0f}ms > budget {max_lat}ms"
                    )

    # ------------------------------------------------------------------
    @property
    def queue_length(self) -> int:
        with self._queue_lock:
            return len(self._queue)

    @property
    def inflight_count(self) -> int:
        with self._inflight_lock:
            return self._inflight

    # ------------------------------------------------------------------
    def get_metrics(self) -> Dict[str, Any]:
        """Return per-lane metrics for /metrics endpoint."""
        metrics = {
            "gpu_queue_length": self.queue_length,
            "gpu_inflight": self.inflight_count,
            "lanes": {},
        }
        for lane, s in self.stats.items():
            lats = s["latencies"]
            avg_ms = s["total_ms"] / max(s["total_runs"], 1)
            p95_ms = 0.0
            if lats:
                sorted_lats = sorted(lats)
                p95_idx = int(len(sorted_lats) * 0.95)
                p95_ms = sorted_lats[min(p95_idx, len(sorted_lats) - 1)]

            # Compute runs/min from timestamps
            now = time.time()
            runs_min = 0.0
            for cam_stamps in self._run_timestamps.get(lane, {}).values():
                recent = [t for t in cam_stamps if now - t < 60.0]
                runs_min += len(recent)

            metrics["lanes"][lane] = {
                "avg_ms": round(avg_ms, 1),
                "p95_ms": round(p95_ms, 1),
                "runs_min": round(runs_min, 1),
                "dropped_count": s["dropped_count"],
            }
        return metrics
