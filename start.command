#!/bin/zsh
# Double-click in Finder to start PixelCrew from a checkout.
cd "$(dirname "$0")"
exec python3 pixelcrew.py "$@"
