#!/usr/bin/env python3
"""
Job Search AI Agent System - Main Entry Point

This is the main script that runs the multi-agent job search system.
Students can run this file directly to get a comprehensive job search report.

Usage:
    python main.py

Or customize the search parameters at the top of this file!

Author: Claude Builder Club @ UC Irvine
Workshop: Intro to AI Agents (October 20, 2025)
"""

import argparse
import re
import sys
import warnings
from datetime import datetime
from pathlib import Path

# This project doesn't use CrewAI's checkpointing feature, so the callback
# functions on our Tasks not being serializable for that purpose is expected.
warnings.filterwarnings(
    "ignore",
    message="function callbacks cannot be serialized.*",
    category=UserWarning,
)

from crewai import Crew, Process
from crewai.events.utils.console_formatter import set_suppress_console_output
from crewai.events.listeners.tracing.utils import set_suppress_tracing_messages

# Import our custom modules
from src.config import (
    DEFAULT_JOB_ROLE,
    DEFAULT_LOCATION,
    DEFAULT_NUM_RESULTS,
    OUTPUT_DIR,
    validate_config,
    print_config,
    CREW_PROCESS,
)
from src.agents import create_all_agents
from src.tasks import create_all_tasks


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Job Search AI Agent System")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Show detailed configuration, agent reasoning, and tool call "
            "output. By default only a one-line checkmark per task is shown."
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
║         Multi-Agent Job Search Powered by CrewAI + Claude               ║
║                                                                          ║
║              UC Irvine Claude Builder Club                               ║
║              Intro to AI Agents Workshop                                 ║
║              October 20, 2025                                            ║
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


def create_run_output_dir(role: str, timestamp: str) -> Path:
    """
    Create a dedicated output folder for this run, named after the job
    role and timestamp so each run's files are kept together.

    Args:
        role: Job role being searched (used in the folder name)
        timestamp: Timestamp string shared by this run's files

    Returns:
        Path to the created run-specific output directory
    """
    safe_role = re.sub(r'[^\w\-]+', '_', role.strip()).strip('_').lower()
    run_dir = OUTPUT_DIR / f"{safe_role}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_final_report(crew_output, role: str, location: str, output_dir: Path) -> Path:
    """
    Save the final combined report from all agents in Markdown format.

    Args:
        crew_output: The output from crew.kickoff()
        role: Job role searched
        location: Location searched
        output_dir: This run's output directory

    Returns:
        Path to the saved report file
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"job_search_report_{timestamp}.md"
    filepath = output_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        # Write Markdown header
        f.write(f"# 🤖 Job Search AI Agent System - Final Report\n\n")

        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n")
        f.write(f"**Job Role:** {role}  \n")
        f.write(f"**Location:** {location}  \n\n")

        f.write("---\n\n")

        f.write("## Executive Summary\n\n")
        f.write(
            "This report was generated by a multi-agent AI system using **CrewAI** "
            "and **Claude**. Four specialized agents collaborated to provide:\n\n"
            "1. 🔍 **Job Search Results** - Current openings matching your criteria\n"
            "2. 📚 **Skills Development Roadmap** - What to learn and how\n"
            "3. 🎤 **Interview Preparation** - Questions and strategies\n"
            "4. 💼 **Career Strategy** - Resume, LinkedIn, and application advice\n\n"
        )

        f.write("---\n\n")
        f.write("## Full Report\n\n")

        # Write the crew output
        f.write(str(crew_output))

        f.write("\n\n---\n\n")
        f.write("*Generated with ❤️ by UC Irvine Claude Builder Club*\n")

    return filepath


def print_completion_message(report_path: Path, run_dir: Path, verbose: bool = False):
    """Print completion message with report location."""
    print(f"\n✅ Done — report saved to: {report_path}")
    if verbose:
        print(f"📂 All outputs for this run saved in: {run_dir}")
        print("\n🎉 Next Steps:")
        print("  1. Review the full report for comprehensive job search guidance")
        print("  2. Check individual agent outputs in the same run folder")
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
    2. Create agents
    3. Create tasks
    4. Create and run crew
    5. Save and display results
    """

    args = parse_args()
    verbose = args.verbose

    # By default, silence CrewAI's own panels (agent/task/tool status boxes)
    # and its tracing notice - our own one-line-per-task progress is enough.
    # --verbose restores the full detail for debugging/learning.
    set_suppress_console_output(not verbose)
    set_suppress_tracing_messages(True)

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
    # Step 3: Display search parameters and create this run's output folder
    # -------------------------------------------------------------------------

    print(f"🎯 {JOB_ROLE} | {LOCATION} | {NUM_RESULTS} results\n")
    if verbose:
        print_search_params(JOB_ROLE, LOCATION, NUM_RESULTS)

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = create_run_output_dir(JOB_ROLE, run_timestamp)
    if verbose:
        print(f"📂 Saving this run's outputs to: {run_dir}\n")

    # -------------------------------------------------------------------------
    # Step 4: Create agents
    # -------------------------------------------------------------------------

    agents_dict = create_all_agents(verbose=verbose)

    # -------------------------------------------------------------------------
    # Step 5: Create tasks
    # -------------------------------------------------------------------------

    tasks = create_all_tasks(
        agents=agents_dict,
        role=JOB_ROLE,
        location=LOCATION,
        num_results=NUM_RESULTS,
        output_dir=run_dir,
        verbose=verbose
    )

    # -------------------------------------------------------------------------
    # Step 6: Create crew
    # -------------------------------------------------------------------------

    crew = Crew(
        agents=list(agents_dict.values()),
        tasks=tasks,
        process=Process.sequential,  # Tasks run one after another
        verbose=verbose,
    )

    # -------------------------------------------------------------------------
    # Step 7: Run the crew!
    # -------------------------------------------------------------------------

    try:
        # This is where the magic happens!
        # The crew will execute all tasks in sequence, with each agent
        # doing their specialized work.
        crew_output = crew.kickoff()

    except KeyboardInterrupt:
        print("\n\n⚠️  Job search interrupted by user.")
        print(f"Partial results may be available in: {run_dir}")
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
    # Step 8: Save final report
    # -------------------------------------------------------------------------

    report_path = save_final_report(crew_output, JOB_ROLE, LOCATION, run_dir)

    # -------------------------------------------------------------------------
    # Step 9: Print completion message
    # -------------------------------------------------------------------------

    print_completion_message(report_path, run_dir, verbose)


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
