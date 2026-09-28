import click
import logging
from pathlib import Path

import numpy as np

from nano_ext.models import DetectionConfig, EventDirection
from nano_ext.pipeline import process_file
from nano_ext.outputs.csv_writer import write_events_to_csv
from nano_ext.outputs.visualize import plot_pipeline_result

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
    "--gmm-max-samples",
    type=int,
    default=100_000,
    help="Maximum number of samples used to fit the GMM (subsampled if signal is larger).",
)
@click.option(
    "--max-sublevel-depth",
    type=int,
    default=5,
    help="Maximum recursion depth for sub-level analysis.",
)
@click.option(
    "--auto-tune",
    is_flag=True,
    help="Auto-tune min_event_duration and merge_gap from signal noise characteristics.",
)
@click.option(
    "--control",
    "control_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to a negative control (analyte-free) file for noise characterisation.",
)
@click.option(
    "--analysis-range",
    "analysis_range",
    nargs=2,
    type=float,
    default=None,
    metavar="START END",
    help=(
        "Restrict analysis to a single time window [START, END) in seconds. "
        "Cannot be combined with --exclude-range."
    ),
)
@click.option(
    "--exclude-range",
    "exclude_ranges",
    nargs=2,
    type=float,
    multiple=True,
    metavar="START END",
    help=(
        "Exclude an artifact window [START, END) in seconds from analysis. "
        "May be specified multiple times for multiple artifact regions. "
        "Cannot be combined with --analysis-range."
    ),
)
@click.option(
    "--hmm",
    "hmm_analysis",
    is_flag=True,
    help=(
        "Fit a Gaussian HMM to each detected event after sub-level analysis. "
        'Requires:  pip install "nano_ext[hmm]"'
    ),
)
@click.option(
    "--hmm-max-states",
    type=int,
    default=5,
    help="Maximum number of HMM states to test per event (BIC selects the best).",
)
@click.option(
    "--hmm-method",
    type=click.Choice(["bic", "sticky_hdp"]),
    default="bic",
    help=(
        "HMM state-count selection: 'bic' (hmmlearn EM + BIC) or 'sticky_hdp' "
        "(sticky HDP-HMM Gibbs sampler; infers the number of states)."
    ),
)
@click.option(
    "--threshold-method",
    type=click.Choice(["gmm", "dpgmm"]),
    default="gmm",
    help=(
        "Threshold model: 'gmm' (GMM for k=1..K, select by --bic-criterion) or "
        "'dpgmm' (Dirichlet-process GMM; number of components inferred)."
    ),
)
@click.option(
    "--sublevel-method",
    type=click.Choice(["gmm", "dpgmm", "bocpd"]),
    default="gmm",
    help=(
        "Sub-level method: 'gmm' (GMM + BIC), 'dpgmm' (Dirichlet-process GMM) "
        "or 'bocpd' (Bayesian online change-point detection)."
    ),
)
@click.option(
    "--dp-concentration",
    type=float,
    default=None,
    help="Dirichlet-process concentration for all 'dpgmm' methods (smaller = fewer components).",
)
@click.option(
    "--cluster",
    "cluster_events",
    is_flag=True,
    help=(
        "Cluster events into populations (relative depth x log dwell time) with a "
        "Dirichlet-process GMM. Adds cluster_id / cluster_prob to the events CSV "
        "and writes <stem>_clusters.csv."
    ),
)
@click.option(
    "--max-clusters",
    type=int,
    default=10,
    help="Upper bound on the number of event populations for --cluster.",
)
@click.option(
    "--bayes-stats",
    is_flag=True,
    help=(
        "Write <stem>_bayes_stats.json: capture-rate posterior and dwell-time "
        "mixture-of-exponentials posterior (per population with --cluster)."
    ),
)
@click.option(
    "--jobs",
    "-j",
    type=int,
    default=-1,
    show_default=True,
    help="Worker processes for per-event analysis (-1 = all CPUs, 1 = no parallelism).",
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
    gmm_max_samples,
    auto_tune,
    control_path,
    analysis_range,
    exclude_ranges,
    hmm_analysis,
    hmm_max_states,
    hmm_method,
    threshold_method,
    sublevel_method,
    dp_concentration,
    cluster_events,
    max_clusters,
    bayes_stats,
    jobs,
    plot,
):
    """Analyze a nanopore data file to detect events."""
    filepath = Path(filepath)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if auto_tune:
        # Load signal first to derive noise-aware defaults, then apply CLI overrides
        from nano_ext.detection.autotune import suggest_config
        if file_format == "abf" or (file_format is None and filepath.suffix.lower() == ".abf"):
            from nano_ext.io.abf_reader import read_abf
            _signal_data = read_abf(str(filepath), channel=channel)
        else:
            if sampling_rate is None:
                raise click.UsageError("--sampling-rate is required for binary files with --auto-tune.")
            from nano_ext.io.binary_reader import read_binary
            _signal_data = read_binary(str(filepath), sampling_rate=sampling_rate, dtype=dtype)
        config = suggest_config(
            _signal_data,
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
            gmm_max_samples=gmm_max_samples,
        )
        click.echo("Auto-tune enabled: noise-aware defaults applied.")
    else:
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
            gmm_max_samples=gmm_max_samples,
        )
    config.hmm_analysis = hmm_analysis
    config.hmm_max_states = hmm_max_states
    config.hmm_method = hmm_method
    config.threshold_method = threshold_method
    config.sublevel_method = sublevel_method
    config.dp_concentration = dp_concentration
    config.cluster_events = cluster_events
    config.max_clusters = max_clusters
    config.n_jobs = jobs

    if analysis_range is not None and exclude_ranges:
        raise click.UsageError("--analysis-range and --exclude-range cannot be combined.")

    click.echo(f"Analyzing {filepath.name}...")
    if analysis_range is not None:
        click.echo(f"  Analysis range: [{analysis_range[0]:.3f}, {analysis_range[1]:.3f}) s")
    if exclude_ranges:
        for t0, t1 in exclude_ranges:
            click.echo(f"  Exclude range: [{t0:.3f}, {t1:.3f}) s")
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
            control_path=control_path,
            analysis_range=tuple(analysis_range) if analysis_range is not None else None,
            exclude_ranges=[tuple(r) for r in exclude_ranges] if exclude_ranges else None,
        )
        click.echo(result.summary())

        # Save events to CSV
        csv_path = output_dir / f"{filepath.stem}_events.csv"
        write_events_to_csv(result.events, csv_path, result.signal_data.sampling_rate)
        click.echo(f"Events saved to {csv_path}")

        if result.cluster_result is not None:
            clusters_path = output_dir / f"{filepath.stem}_clusters.csv"
            result.cluster_result.summary_table().to_csv(clusters_path, index=False)
            click.echo(f"Event populations saved to {clusters_path}")

        if bayes_stats and result.events:
            import json
            from nano_ext.analysis.bayes_stats import summarize_event_statistics
            stats = summarize_event_statistics(
                result.events, observation_time=result.signal_data.duration_sec,
            )
            rate = stats["all"]["capture_rate"]
            click.echo(
                f"Capture rate:    {rate['mean']:.3f} /s "
                f"(95% CrI {rate['lower']:.3f} – {rate['upper']:.3f})"
            )
            if "dwell_time" in stats["all"]:
                dw = stats["all"]["dwell_time"]
                taus = ", ".join(f"{t * 1e3:.3f}" for t in dw["tau_mean"])
                click.echo(f"Dwell-time τ:    {taus} ms ({dw['n_components']} component(s))")
            stats_path = output_dir / f"{filepath.stem}_bayes_stats.json"
            stats_path.write_text(json.dumps(stats, indent=2))
            click.echo(f"Bayesian statistics saved to {stats_path}")

        # Plot result
        if plot:
            plot_path = output_dir / f"{filepath.stem}_analysis.png"
            plot_pipeline_result(result, plot_path)
            click.echo(f"Analysis plot saved to {plot_path}")

    except Exception as e:
        logger.error(f"Error during analysis: {e}", exc_info=True)
        click.echo(f"Error: {e}")
        exit(1)

@main.command()
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--output-dir", "-o",
    type=click.Path(file_okay=False),
    default=".",
    help="Directory to save output files.",
)
@click.option(
    "--file-format",
    type=click.Choice(["abf", "binary"]),
    help="Input file format. Auto-detected from extension if not specified.",
)
@click.option("--channel", type=int, default=0, help="Channel number for ABF files.")
@click.option("--sampling-rate", type=float, help="Sampling rate in Hz (binary files only).")
@click.option("--dtype", type=str, default="float32", help="Data type for binary files.")
@click.option(
    "--control",
    "control_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Analyte-free control file for overlaid comparison.",
)
@click.option(
    "--segment-sec",
    type=float,
    default=1.0,
    help="Welch segment length in seconds.",
)
@click.option("--plot", is_flag=True, help="Save a PNG of the PSD plot.")
def spectrum(
    filepath,
    output_dir,
    file_format,
    channel,
    sampling_rate,
    dtype,
    control_path,
    segment_sec,
    plot,
):
    """Compute and display the Power Spectral Density of a recording."""
    from nano_ext.analysis.spectrum import compute_psd, make_psd_figure

    filepath = Path(filepath)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load signal
    if file_format is None:
        file_format = "abf" if filepath.suffix.lower() == ".abf" else "binary"
    if file_format == "abf":
        from nano_ext.io.abf_reader import read_abf
        signal_data = read_abf(str(filepath), channel=channel)
    else:
        if sampling_rate is None:
            raise click.UsageError("--sampling-rate is required for binary files.")
        from nano_ext.io.binary_reader import read_binary
        signal_data = read_binary(str(filepath), sampling_rate=sampling_rate, dtype=dtype)

    click.echo(f"Computing PSD for {filepath.name} ...")
    psd = compute_psd(signal_data, segment_sec=segment_sec)

    # Load and compute control PSD if provided
    ctrl_psd = None
    if control_path is not None:
        ctrl_path = Path(control_path)
        ctrl_fmt = "abf" if ctrl_path.suffix.lower() == ".abf" else "binary"
        if ctrl_fmt == "abf":
            from nano_ext.io.abf_reader import read_abf
            ctrl_data = read_abf(str(ctrl_path), channel=channel)
        else:
            if sampling_rate is None:
                raise click.UsageError("--sampling-rate is required for binary control files.")
            from nano_ext.io.binary_reader import read_binary
            ctrl_data = read_binary(str(ctrl_path), sampling_rate=sampling_rate, dtype=dtype)
        ctrl_psd = compute_psd(ctrl_data, segment_sec=segment_sec)
        click.echo(f"Control: {ctrl_path.name}")

    click.echo(f"Sampling rate:  {signal_data.sampling_rate:.0f} Hz")
    click.echo(f"Duration:       {signal_data.duration_sec:.3f} s")
    click.echo(f"PSD units:      {psd.units}")
    if psd.f_3db_estimate is not None:
        click.echo(f"Est. -3 dB cutoff: {psd.f_3db_estimate:.1f} Hz")
    else:
        click.echo("Est. -3 dB cutoff: not detected")

    if plot:
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 4))
        mask = (psd.frequencies > 0) & (psd.psd > 0)
        ax.loglog(psd.frequencies[mask], psd.psd[mask], label="Sample", color="#4C72B0")
        if ctrl_psd is not None:
            cm = (ctrl_psd.frequencies > 0) & (ctrl_psd.psd > 0)
            ax.loglog(ctrl_psd.frequencies[cm], ctrl_psd.psd[cm],
                      label="Control", color="#DD8452", linestyle="--")
        if psd.f_3db_estimate is not None:
            ax.axvline(psd.f_3db_estimate, color="#C44E52", linestyle=":",
                       label=f"-3 dB ≈ {psd.f_3db_estimate:.0f} Hz")
        ax.set_xlabel("Frequency (Hz)")
        ax.set_ylabel(psd.units)
        ax.set_title("Power Spectral Density")
        ax.legend()
        ax.grid(True, which="both", ls=":", alpha=0.4)
        fig.tight_layout()
        plot_path = output_dir / f"{filepath.stem}_psd.png"
        fig.savefig(plot_path, dpi=150)
        plt.close(fig)
        click.echo(f"PSD plot saved to {plot_path}")


if __name__ == "__main__":
    main()