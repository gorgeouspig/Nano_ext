import matplotlib.pyplot as plt
from nano_ext.pipeline import PipelineResult
from pathlib import Path

def plot_pipeline_result(result: PipelineResult, path: Path):
    """Plot the pipeline result."""
    signal = result.signal_data.signal
    time = result.signal_data.time
    
    plt.figure(figsize=(12, 6))
    plt.plot(time, signal, label="Raw Signal", color="gray", alpha=0.5)
    plt.plot(time, result.filtered_signal, label="Filtered Signal", color="blue")
    plt.plot(time, result.baseline_result.local_baseline, label="Baseline", color="red")
    
    for event in result.events:
        plt.axvspan(event.start_time, event.end_time, color="orange", alpha=0.3)
        
    plt.legend()
    plt.savefig(path)
    plt.close()
