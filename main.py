#!/usr/bin/env python3
"""
Job Search AI Agent System - Main Entry Point

This is the main script that runs the job search system. Students can
run this file directly to get a comprehensive job search report.

Usage:
    python main.py

Or customize the search parameters at the top of this file!

Note: this is a thin wrapper around src.core.legacy_run.run_legacy_flow,
the same function the `jobsearch run` CLI command calls. It stays here
(rather than being deleted alongside the old multi-agent framework) so
the documented "edit the constants at the top of main.py" workflow keeps
working; it's retired once the CLI fully replaces it.
"""

import argparse
import sys
from pathlib import Path

from src.config import (
    DEFAULT_JOB_ROLE,
    DEFAULT_LOCATION,
    DEFAULT_NUM_RESULTS,
    validate_config,
    print_config,
)
from src.core.legacy_run import run_legacy_flow


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Job Search AI Agent System")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Show detailed configuration and progress output. By default "
            "only a one-line checkmark per step is shown."
        ),
    )
    return parser.parse_args()


# =============================================================================
# JOB SEARCH PARAMETERS - CUSTOMIZE THESE!
# =============================================================================

# TODO: CUSTOMIZE - Change these values for your own job search!
# These default to the values in config.py, but you can override them here
JOB_ROLE = "Junior Civil Engineer"  # e.g., "Software Engineer", "Product Manager"
LOCATION = "New York"  # e.g., "San Francisco", "Remote", "New York"
NUM_RESULTS = DEFAULT_NUM_RESULTS  # Number of jobs to search for (1-50)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def print_banner(verbose: bool = False):
    """Print a welcoming banner (full ASCII art only in --verbose)."""
    if not verbose:
        print("🤖 Job Search AI Agent System\n")
        return

    banner = """
╔══════════════════════════════════════════════════════════════════════════╗
║                                                                          ║
║            🤖 JOB SEARCH AI AGENT SYSTEM 🤖                             ║
║                                                                          ║
║              Multi-Step Job Search Powered by Claude                    ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
    print(banner)


def print_search_params(role: str, location: str, num_results: int):
    """Print the search parameters being used."""
    print("\n" + "="*80)
    print("🎯 SEARCH PARAMETERS")
    print("="*80)
    print(f"  Job Role:        {role}")
    print(f"  Location:        {location}")
    print(f"  # of Results:    {num_results}")
    print("="*80 + "\n")


def print_completion_message(report_path: Path, run_dir: Path, verbose: bool = False):
    """Print completion message with report location."""
    print(f"\n✅ Done — report saved to: {report_path}")
    if verbose:
        print(f"📂 All outputs for this run saved in: {run_dir}")
        print("\n🎉 Next Steps:")
        print("  1. Review the full report for comprehensive job search guidance")
        print("  2. Check individual step outputs in the same run folder")
        print("  3. Customize the search parameters in main.py to find more jobs")
        print("  4. Use the insights to update your resume and LinkedIn profile")
        print("  5. Start applying with confidence! 🚀")


# =============================================================================
# MAIN FUNCTION
# =============================================================================

def main():
    """
    Main function that orchestrates the entire job search process.

    Process:
    1. Validate configuration
    2. Run the legacy job search flow
    3. Display results
    """

    args = parse_args()
    verbose = args.verbose

    # -------------------------------------------------------------------------
    # Step 1: Print banner and configuration
    # -------------------------------------------------------------------------

    print_banner(verbose)
    if verbose:
        print_config()

    # -------------------------------------------------------------------------
    # Step 2: Validate configuration
    # -------------------------------------------------------------------------

    is_valid, errors = validate_config()

    if not is_valid:
        print("\n❌ Configuration Error!\n")
        print("Please fix the following issues:\n")
        for i, error in enumerate(errors, 1):
            print(f"  {i}. {error}")
        print("\nSee SETUP.md for detailed setup instructions.")
        sys.exit(1)

    # -------------------------------------------------------------------------
    # Step 3: Display search parameters
    # -------------------------------------------------------------------------

    print(f"🎯 {JOB_ROLE} | {LOCATION} | {NUM_RESULTS} results\n")
    if verbose:
        print_search_params(JOB_ROLE, LOCATION, NUM_RESULTS)

    # -------------------------------------------------------------------------
    # Step 4: Run the job search flow
    # -------------------------------------------------------------------------

    try:
        result = run_legacy_flow(
            role=JOB_ROLE,
            location=LOCATION,
            num_results=NUM_RESULTS,
            verbose=verbose,
        )

    except KeyboardInterrupt:
        print("\n\n⚠️  Job search interrupted by user.")
        sys.exit(0)

    except Exception as e:
        print(f"\n\n❌ Error during job search: {str(e)}")
        print("\nTroubleshooting tips:")
        print("  1. Check your API keys in .env")
        print("  2. Verify internet connection")
        print("  3. Review error details above")
        print("  4. See TROUBLESHOOTING.md for common issues")
        sys.exit(1)

    # -------------------------------------------------------------------------
    # Step 5: Print completion message
    # -------------------------------------------------------------------------

    print_completion_message(result.report_path, result.run_dir, verbose)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")
        print("\nPlease report this issue with the error details above.")
        print("See TROUBLESHOOTING.md for help.")
        sys.exit(1)
