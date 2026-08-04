#!/usr/bin/env python3
"""Entry point. All logic lives in the exitlag_auto package."""
import sys

from exitlag_auto.cli import main

if __name__ == "__main__":
    sys.exit(main())
