import sys
import os
import asyncio
import logging
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "nodes"))

from pipeline import run_pipeline
from nodes.poller import run_poller
from nodes.trending_poller import poll_trending
from nodes.cleanup import run_cleanup
from state import scheduled_job_lock

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "job",
        choices=["discover", "trending-week", "trending-month", "cleanup"],
    )
    args = parser.parse_args()

    with scheduled_job_lock():
        if args.job == "discover":
            candidates = run_poller()
            await run_pipeline(candidates)

        elif args.job == "trending-week":
            candidates = poll_trending("week")
            await run_pipeline(candidates)

        elif args.job == "trending-month":
            candidates = poll_trending("month")
            await run_pipeline(candidates)

        elif args.job == "cleanup":
            run_cleanup()


if __name__ == "__main__":
    asyncio.run(main())
