"""`python -m orchestrator.humanio` — run the Slack Socket Mode gate listener."""

import asyncio

from orchestrator.humanio.slack_listener import main

if __name__ == "__main__":
    asyncio.run(main())
