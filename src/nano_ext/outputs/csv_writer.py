import pandas as pd
from typing import List
from nano_ext.models import Event

def write_events_to_csv(events: List[Event], path: str, sampling_rate: float):
    """Save detected events to a CSV file."""
    data = []
    for ev in events:
        data.append({
            "start_idx": ev.start_idx,
            "end_idx": ev.end_idx,
            "start_time": ev.start_time,
            "end_time": ev.end_time,
            "duration": ev.duration,
            "mean_current": ev.mean_current,
            "std_current": ev.std_current,
            "baseline_current": ev.baseline_current,
            "depth": ev.depth,
            "relative_depth": ev.relative_depth,
            "area": ev.area,
            "n_levels": ev.n_levels,
            "event_type": ev.event_type.value,
        })
    df = pd.DataFrame(data)
    df.to_csv(path, index=False)
