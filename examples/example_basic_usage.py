"""
Basic usage example for nano_ext.

This example demonstrates how to:
1. Create a synthetic nanopore signal with known events.
2. Process the signal using the nano_ext pipeline.
3. Print a summary of the results.
4. Save detected events to a CSV file.
5. Save a plot of the analysis.

Note: This example uses matplotlib's 'Agg' backend to avoid requiring a GUI.
"""

import os
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt

from nano_ext import SignalData, run_pipeline
from nano_ext.models import DetectionConfig
from nano_ext.output.csv_writer import write_events_to_csv
from nano_ext.output.visualize import plot_pipeline_result


def main():
    # Create a synthetic nanopore signal
    duration = 1.0  # second
    sampling_rate = 10000.0  # Hz
    n_samples = int(duration * sampling_rate)
    t = np.arange(n_samples) / sampling_rate
    
    # Baseline current
    baseline = 100.0
    signal = baseline * np.ones(n_samples)
    
    # Add two events: downward blockades
    # Event 1: from 0.2s to 0.3s, current drops to 50 pA
    event1_start = int(0.2 * sampling_rate)
    event1_end = int(0.3 * sampling_rate)
    signal[event1_start:event1_end] = 50.0
    
    # Event 2: from 0.5s to 0.6s, current drops to 20 pA
    event2_start = int(0.5 * sampling_rate)
    event2_end = int(0.6 * sampling_rate)
    signal[event2_start:event2_end] = 20.0
    
    # Add Gaussian noise
    np.random.seed(42)  # For reproducibility
    noise = np.random.normal(0, 5, n_samples)
    signal += noise
    
    # Create SignalData object
    signal_data = SignalData(
        signal=signal,
        sampling_rate=sampling_rate,
        units="pA"
    )
    
    # Create detection config (use defaults, but we can adjust)
    config = DetectionConfig()
    # Adjust parameters if needed for this synthetic signal
    config.baseline_window_sec = 0.2  # Smaller window for faster baseline estimation
    config.min_event_duration_sec = 0.001  # 1 ms minimum event duration
    config.merge_gap_sec = 0.005  # 5 ms gap for merging events
    
    # Run the pipeline
    print("Running nanopore event detection pipeline...")
    result = run_pipeline(
        signal_data=signal_data,
        config=config,
        analyze_sublevel=True,
        verbose=True
    )
    
    # Print summary
    print("\n" + "="*50)
    print("PIPELINE SUMMARY")
    print("="*50)
    print(result.summary())
    
    # Save events to CSV
    output_dir = "example_output"
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "events.csv")
    write_events_to_csv(result.events, csv_path, result.signal_data.sampling_rate)
    print(f"\nEvents saved to: {csv_path}")
    
    # Save plot
    plot_path = os.path.join(output_dir, "analysis.png")
    plot_pipeline_result(result, plot_path)
    print(f"Analysis plot saved to: {plot_path}")
    
    # Print details of each event
    if result.events:
        print("\nDETECTED EVENTS:")
        print("-"*50)
        for i, event in enumerate(result.events):
            print(f"Event {i+1}:")
            print(f"  Time: {event.start_time:.3f} - {event.end_time:.3f} s")
            print(f"  Duration: {event.duration*1000:.3f} ms")
            print(f"  Depth: {event.depth:.2f} pA")
            print(f"  Relative depth: {event.relative_depth:.2%}")
            print(f"  Area: {event.area:.2f} pA*s")
            print(f"  Type: {event.event_type.value}")
            if event.is_multilevel:
                print(f"  Sub-levels: {event.n_levels}")
                for j, sublevel in enumerate(event.sublevels):
                    print(f"    Sub-level {j+1}: {sublevel.duration*1000:.3f} ms, "
                          f"current: {sublevel.mean_current:.2f} pA")
            print()
    else:
        print("\nNo events detected.")
    
    print("Example completed successfully!")


if __name__ == "__main__":
    main()