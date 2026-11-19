"""Allow running `python3 -m linkedin_scrapper`."""

import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main())
