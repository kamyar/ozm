#!/usr/bin/env python3
"""Simple test script."""

import os

name = os.environ.get("USER", "world")
for i in range(3):
    print(f"hello {name}! ({i + 1})")
