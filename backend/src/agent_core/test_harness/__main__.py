"""Entry point for running the CLI test harness as a module.

Usage:
    python -m agent_core.test_harness check
    python -m agent_core.test_harness interactive
    python -m agent_core.test_harness batch
    python -m agent_core.test_harness snapshots
"""

from agent_core.test_harness.cli_runner import cli

if __name__ == "__main__":
    cli()
