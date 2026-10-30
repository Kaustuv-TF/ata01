"""ATA-01: paper-first, risk-gated trading pilot (INR 10,000 mandate)."""

__version__ = "0.1.0"


def main() -> None:
    from ata.cli import main as cli_main

    raise SystemExit(cli_main())
