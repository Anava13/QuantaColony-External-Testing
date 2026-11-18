"""Developer mode: what a person did in Colony Detection and Cropping, with times (Qt-free).

Developer mode (`quantacolony --developer`) records how long each image took, where that
time went, and what automatic detection found before any person changed it. The records
are written into the project file, in tables of their own (data/store/developer.py), in the
same transaction as the result they describe. A standard launch creates no recorder and
writes nothing.

Recorders keep raw, timestamped events. Nothing is summed here, so an analysis can choose
its idle cutoff afterwards (quantacolony.benchmark).

Times: `t_ms` counts milliseconds from the moment a session opened, on a monotonic clock.
`opened_at` and `finished_at` are wall-clock times (UTC, ISO 8601).

Detection session events (kind: data):
    mode            mode: the mode entered
    detection       trigger ('initial', 'settings', 'shape', 'estimate'), params, compute_ms, colonies
    region_preview  roi (x, y, width, height in crop pixels), params, compute_ms, candidates
    edit            kind ('Detection', 'Deletion', 'Addition'), count, colony_ids, mode
    undo, redo      kind, count
    select          how ('click', 'rectangle'), count
    draw            circles: hand-drawn circles waiting to be added
    setting         field: a settings control being changed (kept at most once per second)
    pointer         (kept at most once every two seconds: the person is working on the image)
    focus           active: the program window became active (True) or inactive (False)

Cropping events (kind: data):
    opened, left    the photo was shown, or another photo replaced it
    layout          final: a layout drag (kept at most once per second while dragging)
    rotate, fit, count, cut_removed, pick, unused, focus
"""

from __future__ import annotations

import os
import platform
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import numpy as np

from .autoparams import estimate_with_extent
from .colony_set import ColonySet
from .detection import perform_detection
from .params import DetectionParams

BENCHMARK_VERSION = 1             # layout of the developer tables (stored in the project setting 'developer')
POINTER_EVERY_MS = 2000           # pointer movement is kept at most this often
SETTING_EVERY_MS = 1000           # a settings control being dragged is kept at most this often


def wall_clock() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def params_data(params: DetectionParams) -> dict:
    """Detection settings as plain values for an event."""
    return asdict(params)


@dataclass(frozen=True)
class Event:
    seq: int
    t_ms: int
    kind: str
    data: dict


class EventLog:
    """Timestamped events of one session. `every_ms` thins an event kind that fires continuously:
    one is kept, then none of that kind until every_ms has passed."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._start = clock()
        self.opened_at = wall_clock()
        self.events: list[Event] = []
        self._last: dict[str, int] = {}

    def elapsed_ms(self) -> int:
        return int(round(1000 * (self._clock() - self._start)))

    def record(self, event: str, every_ms: int = 0, **data) -> None:
        t = self.elapsed_ms()
        if every_ms and event in self._last and t - self._last[event] < every_ms:
            return
        self._last[event] = t
        self.events.append(Event(len(self.events), t, event, data))


# ----- Colony Detection ---------------------------------------------------------------------
@dataclass(frozen=True)
class InitialDetection:
    """Automatic detection with no person involved (see reference_start)."""

    params: DetectionParams
    extent: str                   # how much the estimate covered: 'full', 'level only' or 'none'
    colonies: ColonySet           # crop pixels
    compute_ms: float


def reference_start(image: np.ndarray, mask: np.ndarray, defaults: DetectionParams) -> InitialDetection:
    """Detection at settings estimated from this image, starting from the project defaults.

    Settings the estimate does not cover (sensitivity, detection type, splitting) come from
    the project defaults, never from another image, so the result depends on this image alone."""
    params, extent = estimate_with_extent(image, mask, defaults)
    started = time.perf_counter()
    colonies = perform_detection(image, params, region_mask=mask)
    return InitialDetection(params, extent, colonies, 1000 * (time.perf_counter() - started))


@dataclass(frozen=True)
class DetectionBenchmark:
    """One image session in Colony Detection, from opening the image to "Finish image"."""

    index_id: int
    opened_at: str
    finished_at: str
    duration_ms: int
    start_source: str             # where the starting settings came from: 'saved', 'last image', 'estimate', 'defaults'
    start_params: DetectionParams
    final_params: DetectionParams
    image_shape: tuple[int, int]  # crop rows, columns
    region_px: int                # pixels inside the region
    px_per_mm: float
    initial: InitialDetection | None
    events: tuple[Event, ...]


class DetectionRecorder:
    """Records one image session. The controller calls record() at each event and finish() on
    "Finish image"; a session left without finishing is dropped with its recorder."""

    def __init__(self, index_id: int, image_shape: tuple[int, int], region_px: int, px_per_mm: float,
                 start_source: str, start_params: DetectionParams, initial: InitialDetection | None,
                 clock=time.monotonic):
        self.log = EventLog(clock)
        self.index_id = index_id
        self.image_shape = (int(image_shape[0]), int(image_shape[1]))
        self.region_px = int(region_px)
        self.px_per_mm = float(px_per_mm) if px_per_mm else 0.0      # 0: the photo has no scale
        self.start_source = start_source
        self.start_params = start_params
        self.initial = initial

    def record(self, event: str, every_ms: int = 0, **data) -> None:
        self.log.record(event, every_ms, **data)

    def finish(self, final_params: DetectionParams) -> DetectionBenchmark:
        return DetectionBenchmark(self.index_id, self.log.opened_at, wall_clock(), self.log.elapsed_ms(),
                                  self.start_source, self.start_params, final_params, self.image_shape,
                                  self.region_px, self.px_per_mm, self.initial, tuple(self.log.events))


# ----- Cropping -------------------------------------------------------------------------------
@dataclass(frozen=True)
class CropBenchmark:
    """One photo in Cropping, from its first opening until it was saved ("Done with photo").
    The photo may have been left and opened again in between ('left' and 'opened' events)."""

    photo_id: int
    opened_at: str
    done_at: str
    duration_ms: int
    visits: int
    section_count: int
    layout_kind: str
    events: tuple[Event, ...]


class CropRecorder:
    """Keeps an event log per photo; events go to the photo shown."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._logs: dict[int, EventLog] = {}
        self.current: int | None = None

    def open(self, photo_id: int) -> None:
        if photo_id == self.current:
            return
        if self.current is not None and self.current in self._logs:
            self._logs[self.current].record("left")
        self.current = photo_id
        self._logs.setdefault(photo_id, EventLog(self._clock)).record("opened")

    def record(self, event: str, every_ms: int = 0, **data) -> None:
        if self.current is not None:
            self._logs.setdefault(self.current, EventLog(self._clock)).record(event, every_ms, **data)

    def done(self, photo_id: int, section_count: int, layout_kind: str) -> CropBenchmark | None:
        """The record of a photo being saved. Call forget() once the save worked."""
        log = self._logs.get(photo_id)
        if log is None:
            return None
        visits = sum(1 for event in log.events if event.kind == "opened")
        return CropBenchmark(photo_id, log.opened_at, wall_clock(), log.elapsed_ms(), visits, int(section_count),
                             layout_kind, tuple(log.events))

    def forget(self, photo_id: int) -> None:
        """The photo was saved: a later opening starts a new record."""
        self._logs.pop(photo_id, None)
        if self.current == photo_id:
            self.current = None


# ----- the computer -----------------------------------------------------------------------------
def environment() -> dict:
    """The computer and software a benchmark ran on (compute times depend on them)."""
    from .. import __version__
    return {"recorded_at": wall_clock(), "software_version": __version__, "benchmark_version": BENCHMARK_VERSION,
            "python": platform.python_version(), "os": platform.platform(), "machine": platform.machine(),
            "cpu": _cpu_name(), "cores": os.cpu_count(), "ram_gb": _ram_gb()}


def _cpu_name() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as info:
            for line in info:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def _ram_gb() -> float | None:
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return round(status.ullTotalPhys / 1024 ** 3, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3, 1)
    except (AttributeError, OSError, ValueError):
        return None
