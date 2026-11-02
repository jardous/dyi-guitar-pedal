#!/usr/bin/env python3
import time
import mido
from gpiozero import Button
from signal import pause

# --- Configuration ---
# We search for this name in the destinations list
TARGET_PORT_NAME = "PiPedal:in"
HOLD_THRESHOLD = 1.0

# --- Global State ---
outport = None
start_times = {27: 0, 26: 0}

# --- Hardware Setup ---
button1 = Button(27, pull_up=True, bounce_time=0.05)
button2 = Button(26, pull_up=True, bounce_time=0.05)

# --- Connection Manager ---
def ensure_connection():
    global outport
    if outport is not None:
        return True

    try:
        # CRITICAL: We use get_output_names() because we want to SEND data.
        # This list contains all devices that are waiting to RECEIVE data (Destinations).
        destinations = mido.get_output_names()

        # Fuzzy match: Look for "PiPedal:in" inside the list of destinations
        matches = [p for p in destinations if TARGET_PORT_NAME in p]

        if matches:
            full_name = matches[0]
            # Open an output connection TO the target
            outport = mido.open_output(full_name)
            print(f"SUCCESS: Connected to destination '{full_name}'")
            return True
        else:
            # If you are debugging, uncomment this to see what IS available:
            # print(f"Searching for '{TARGET_PORT_NAME}'... Found: {destinations}")
            return False

    except Exception as e:
        print(f"ERROR: Connection failed: {e}")
        return False

# --- Trigger Logic ---
def trigger_midi(note_val):
    global outport

    # 1. Lazy Connect
    if not ensure_connection():
        print(f"Skipping Note {note_val} (Target device not found)")
        return

    # 2. Send Message
    try:
        print(f"Sending MIDI Note: {note_val}")
        outport.send(mido.Message('note_on', note=note_val, velocity=127))
        time.sleep(0.05)
        outport.send(mido.Message('note_off', note=note_val, velocity=0))

    except (IOError, OSError) as e:
        print(f"LOST CONNECTION: {e}")
        try:
            outport.close()
        except:
            pass
        outport = None

# --- Timer Logic (Standard) ---
def start_timer(btn):
    start_times[btn.pin.number] = time.time()

def end_timer(btn, short_note, long_note):
    elapsed = time.time() - start_times[btn.pin.number]
    print(f"Button {btn.pin.number} released after {elapsed:.2f}s")

    if elapsed < HOLD_THRESHOLD:
        trigger_midi(short_note)
    else:
        trigger_midi(long_note)

# --- Link Events ---
button1.when_pressed = lambda: start_timer(button1)
button2.when_pressed = lambda: start_timer(button2)

button1.when_released = lambda: end_timer(button1, 10, 20)
button2.when_released = lambda: end_timer(button2, 30, 40)

print(f"System Ready. Searching destinations for '{TARGET_PORT_NAME}'...")
ensure_connection()

try:
    pause()
finally:
    if outport:
        outport.close()
