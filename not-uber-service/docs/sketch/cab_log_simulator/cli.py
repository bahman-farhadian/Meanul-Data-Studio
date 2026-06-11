"""Simple CLI entry point for the CabLogSimulator."""

import argparse
import sys
from pathlib import Path

from .simulator import CabLogSimulator


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="cab-log-sim",
        description="Generate synthetic cab ride logs from YAML configuration.",
    )
    parser.add_argument(
        "--config-dir",
        required=True,
        type=Path,
        help="Directory containing log_configuration_cab.yaml, "
        "log_keys_cab.yaml, and log_values_cab.yaml",
    )
    parser.add_argument(
        "-n",
        "--num-logs",
        type=int,
        default=100,
        help="Number of logs to generate (default: 100)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="File to write logs to (stdout if omitted)",
    )
    args = parser.parse_args(argv)

    cfg_dir = args.config_dir
    sim = CabLogSimulator(
        cfg_dir / "log_configuration_cab.yaml",
        cfg_dir / "log_keys_cab.yaml",
        cfg_dir / "log_values_cab.yaml",
    )
    records = sim.generate(args.num_logs)

    if args.output:
        args.output.write_text("\n".join(records) + "\n", encoding="utf-8")
    else:
        for rec in records:
            print(rec)


if __name__ == "__main__":
    sys.exit(main())
