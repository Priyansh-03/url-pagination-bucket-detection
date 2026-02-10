#!/bin/bash
# Script to monitor Chrome browser processes during pagination detection

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║           Chrome Process Monitor - Press Ctrl+C to stop       ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

while true; do
    # Count chrome and chromedriver processes
    CHROME_COUNT=$(ps aux | grep -E 'chrome|chromedriver' | grep -v grep | wc -l)
    
    # Get current timestamp
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    
    # Clear line and print status
    echo -ne "\r[$TIMESTAMP] Chrome processes: $CHROME_COUNT    "
    
    # Sleep for 1 second
    sleep 1
done
