import click
import logging
from pathlib import Path

import numpy as np

from nano_ext.models import DetectionConfig, EventDirection
from nano_ext.pipeline import process_file
from nano_ext.output.csv_writer import write_events_to_csv
from nano_ext.output.visualize import plot_pipeline_result

logger = logging.getLogger(__name__)

@click.group()
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose logging.",
)
def main(verbose):
    """Nanopore event detection and analysis tool."""
    if verbose:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

@main.command()
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(file_okay=False),
    default=".",
    help="Directory to save output files (CSV, plots).",
)
@click.option(
    "--file-format",
    type=click.Choice(["abf", "binary"]),
    help="Input file format. Auto-detected if not specified.",
)
@click.option(
    "--channel",
    type=int,
    default=0,
    help="Channel number for multi-channel ABF files.",
)
@click.option(
    "--sampling-rate",
    type=float,
    help="Sampling rate in Hz (required for binary files).",
)
@click.option(
    "--dtype",
    type=str,
    default="float32",
    help="Data type for binary files (e.g., 'int16', 'float32').",
)
@click.option(
    "--no-filter",
    is_flag=True,
    help="Skip low-pass filtering.",
)
@click.option(
    "--pre-filter-cutoff",
    type=float,
    help="Cutoff frequency of pre-applied low-pass filter (used if --no-filter is set).",
)
@click.option(
    "--no-sublevel-analysis",
    is_flag=True,
    help="Disable sub-level (multi-step) event analysis.",
)
@click.option(
    "--detrend-method",
    type=click.Choice(["polynomial", "linear", "spline", "none"]),
    default="polynomial",
    help="Global baseline detrending method.",
)
@click.option(
    "--detrend-order",
    type=int,
    default=3,
    help="Polynomial order for detrending.",
)
@click.option(
    "--baseline-window-sec",
    type=float,
    default=5.0,
    help="Window size in seconds for local baseline estimation.",
)
@click.option(
    "--baseline-iterations",
    type=int,
    default=3,
    help="Number of iterations for local baseline refinement.",
)
@click.option(
    "--baseline-percentile",
    type=float,
    default=90.0,
    help="Percentile for local baseline estimation (e.g., 90 means 90th percentile).",
)
@click.option(
    "--baseline-n-sigma",
    type=float,
    default=5.0,
    help="Number of noise stds for event exclusion during baseline iteration.",
)
@click.option(
    "--event-direction",
    type=click.Choice([e.value for e in EventDirection]),
    default=EventDirection.DOWN.value,
    help="Direction of events to detect: 'down' (blockades), 'up', or 'both'.",
)
@click.option(
    "--min-event-duration-sec",
    type=float,
    default=0.0001,
    help="Minimum event duration in seconds to be considered valid.",
)
@click.option(
    "--merge-gap-sec",
    type=float,
    default=0.00005,
    help="Maximum gap in seconds between events to merge them.",
)
@click.option(
    "--gmm-max-components",
    type=int,
    default=10,
    help="Maximum number of GMM components for threshold determination.",
)
@click.option(
    "--bic-criterion",
    type=click.Choice(["bic", "aic"]),
    default="bic",
    help="Information criterion for GMM selection: 'bic' or 'aic'.",
)
@click.option(
    "--max-sublevel-depth",
    type=int,
    default=5,
    help="Maximum recursion depth for sub-level analysis.",
)
@click.option(
    "--plot",
    is_flag=True,
    help="Generate and save a plot of the analysis result.",
)
def analyze(
    filepath,
    output_dir,
    file_format,
    channel,
    sampling_rate,
    dtype,
    no_filter,
    pre_filter_cutoff,
    no_sublevel_analysis,
    detrend_method,
    detrend_order,
    baseline_window_sec,
    baseline_iterations,
    baseline_percentile,
    baseline_n_sigma,
    event_direction,
    min_event_duration_sec,
    merge_gap_sec,
    gmm_max_components,
    bic_criterion,
    max_sublevel_depth,
    plot,
):
    """Analyze a nanopore data file to detect events."""
    filepath = Path(filepath)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = DetectionConfig(
        apply_filter=not no_filter,
        pre_applied_filter_cutoff=pre_filter_cutoff,
        detrend_method=detrend_method,
        detrend_order=detrend_order,
        baseline_window_sec=baseline_window_sec,
        baseline_iterations=baseline_iterations,
        baseline_percentile=baseline_percentile,
        baseline_n_sigma=baseline_n_sigma,
        event_direction=EventDirection(event_direction),
        min_event_duration_sec=min_event_duration_sec,
        merge_gap_sec=merge_gap_sec,
        gmm_max_components=gmm_max_components,
        bic_criterion=bic_criterion,
        max_sublevel_depth=max_sublevel_depth,
    )

    click.echo(f"Analyzing {filepath.name}...")
    try:
        result = process_file(
            filepath=filepath,
            config=config,
            file_format=file_format,
            channel=channel,
            sampling_rate=sampling_rate,
            dtype=dtype,
            analyze_sublevel=not no_sublevel_analysis,
            verbose=True,
        )
        click.echo(result.summary())

        # Save events to CSV
        csv_path = output_dir / f"{filepath.stem}_events.csv"
        write_events_to_csv(result.events, csv_path, result.signal_data.sampling_rate)
        click.echo(f"Events saved to {csv_path}")

        # Plot result
        if plot:
            plot_path = output_dir / f"{filepath.stem}_analysis.png"
            plot_pipeline_result(result, plot_path)
            click.echo(f"Analysis plot saved to {plot_path}")

    except Exception as e:
        logger.error(f"Error during analysis: {e}", exc_info=True)
        click.echo(f"Error: {e}")
        exit(1)

if __name__ == "__main__":
    main()