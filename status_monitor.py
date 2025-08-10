import serial
import psutil
import time
import os
import json

BOT_NAME = "bot.py"
COM_PORT = "COM5"
BAUD_RATE = 9600

STATUS_FILE = "bot_status.json"
downtime_start = None
prev_command = None

# Connect to Arduino
try:
    arduino = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
    time.sleep(2)
    print(f"Connected to Arduino via {COM_PORT}")
except Exception as e:
    print(f"Failed to connect to Arduino: {e}")
    exit()

def is_bot_running():
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        cmdline = proc.info.get('cmdline')
        if cmdline and isinstance(cmdline, list):
            if BOT_NAME in " ".join(cmdline):
                return True
    return False

def send_command(cmd):
    try:
        arduino.write((cmd + "\n").encode())
        print(f"Sent command to Arduino: {cmd}")
    except Exception as e:
        print(f"Failed to send command: {e}")

def read_status():
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error reading status: {e}")
    return {}

def write_status(status):
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f)
    except Exception as e:
        print(f"Error writing status: {e}")

def clear_flash_both(status):
    if "flash_both" in status:
        del status["flash_both"]
        write_status(status)
        print("Cleared flash_both flag from status.")

def clear_error(status):
    if "error" in status:
        del status["error"]
        write_status(status)
        print("Cleared error flag from status.")

def check_ping_status():
    status = read_status()

    if status.get("flash_both", False):
        print("[FLASH_BOTH] Detected request to flash both LEDs.")
        send_command("flash_both")
        clear_flash_both(status)
        send_command("green_on")

    elif status.get("ping", False):
        print("[PING] Bot pinged, flashing both LEDs.")
        send_command("flash_both")
        time.sleep(5)
        status["ping"] = False
        write_status(status)
        send_command("green_on")

def check_error():
    status = read_status()
    if status.get("error", False):
        print("[ERROR] Detected error flag in status.")
        send_command("error")
        clear_error(status)
        # Uncomment the next line if you want to immediately turn green on after error signal
        send_command("green_on")

try:
    while True:
        if is_bot_running():
            send_command("green_on")
            downtime_start = None
        else:
            if downtime_start is None:
                downtime_start = time.time()
                send_command("red_on")
            elif time.time() - downtime_start >= 30:
                print("Downtime >30s detected, flashing red until bot restarts...")
                while True:
                    send_command("flash_red")
                    if is_bot_running():
                        break
                send_command("green_on")
                downtime_start = None  # Reset downtime

        check_ping_status()
        check_error()
        time.sleep(2)

except KeyboardInterrupt:
    print("Stopped monitor.")
except Exception as e:
    print(f"An error occurred: {e}")
finally:
    send_command("red_on")
