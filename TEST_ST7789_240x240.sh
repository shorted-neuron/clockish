#!/bin/bash
# ST7789 240x240 SQUARE PANEL TEST GUIDE
# =======================================
#
# This script documents the prep work done for testing the 240x240 ST7789 panel.
#
# FILES CREATED/MODIFIED:
# ✏️  Created: configs/display/st7789-240x240.yaml
#     - Hardware profile for Mini-PiTFT 1.3" (240x240)
#     - Default offsets: offset_left=0, offset_top=80
#     - Backlight on GPIO 22
#
# ✏️  Updated: configs/square.yaml (updated header comment only)
#     - Points to the new display profile
#
# TESTING CHECKLIST ON OTHER PI:
# ==============================
#
# 1. PULL THIS COMMIT
#    git pull
#
# 2. INSTALL/VERIFY DEPENDENCIES
#    pip install -e ".[dev]"    # ensures clockish installed + st7789 lib
#    pip list | grep st7789     # verify st7789 package installed
#
# 3. SINGLE-FRAME TEST (no loop, just render & exit)
#    Run with layout debug to see computed dimensions:
#    ./run-clockish.sh --debug-layout --display configs/display/st7789-240x240.yaml configs/square.yaml
#
#    This will:
#    - Print panel layout info to console
#    - Render ONE frame
#    - Exit (don't loop)
#    - Show any GPIO/SPI init errors
#
# 4. IF IMAGE APPEARS SHIFTED/CLIPPED:
#    The offsets likely need adjustment. Trial-and-error:
#
#    a. Edit configs/display/st7789-240x240.yaml:
#       - offset_left:  0    (try -40, 0, 40, 80, etc.)
#       - offset_top:   80   (try 0, 40, 53, 80, etc.)
#
#    b. Re-run step 3 after each adjustment
#
#    c. Document the working offsets & update the config
#
# 5. ROTATION TEST (optional, square panel supports 0/90/180/270)
#    Edit configs/display/st7789-240x240.yaml:
#      rotation: 90    # try 0, 90, 180, 270
#    Re-run step 3
#
# 6. LONG-RUN TEST (if image looks good)
#    ./run-clockish.sh --display configs/display/st7789-240x240.yaml configs/square.yaml
#    Ctrl-C to stop after ~10 sec
#
# 7. BACKLIGHT TEST (if GPIO22 is wired)
#    The backlight_pin: 22 is configured and should auto-enable on init.
#
# REFERENCE OFFSETS:
# ==================
# 240x135 (Adafruit): offset_left=40, offset_top=53
# 240x240 (Mini-PiTFT): offset_left=0, offset_top=80   <- initial guess
#
# Both panels use the same ST7789 controller (320x240 internal).
# Adjust as needed based on physical test.
#
# COMMIT & PUSH:
# ==============
# Once offsets are confirmed working, commit & push back:
#   git add -A
#   git commit -m "st7789: 240x240 square panel support (offsets: x=0, y=80)"
#   git push
#
