from machine import Pin, I2C, PWM
import time
import ssd1306
import network
import espnow

# =========================================================
# DISPLAY CONFIG (Small 128x32 SSD1306, I2C)
# =========================================================
SCL_PIN = 15
SDA_PIN = 32
OLED_ADDRESS = 0x3C

OLED_WIDTH = 128
OLED_HEIGHT = 32  # small display

i2c = I2C(0, scl=Pin(SCL_PIN), sda=Pin(SDA_PIN), freq=100000)
oled = ssd1306.SSD1306_I2C(OLED_WIDTH, OLED_HEIGHT, i2c, addr=OLED_ADDRESS)

def safe_show():
    """Safely update the OLED; ignore transient I2C ENODEV errors."""
    try:
        oled.show()
    except OSError as e:
        print("OLED I2C error during show():", e)

# Optional splash
oled.fill(0)
oled.text("Lazy Susan", 0, 0)
oled.text("Spice UI", 0, 12)
safe_show()
time.sleep(1)

# =========================================================
# BUTTON CONFIG (PULL-UP: 1 = not pressed, 0 = pressed)
# UP = 33, DOWN = 14, SELECT = 13, BACK = 27
# =========================================================
BTN_UP_PIN = 33
BTN_DOWN_PIN = 14
BTN_SEL_PIN = 13   # SELECT
BTN_BACK_PIN = 27  # BACK

btn_up = Pin(BTN_UP_PIN, Pin.IN, Pin.PULL_UP)
btn_down = Pin(BTN_DOWN_PIN, Pin.IN, Pin.PULL_UP)
btn_sel = Pin(BTN_SEL_PIN, Pin.IN, Pin.PULL_UP)
btn_back = Pin(BTN_BACK_PIN, Pin.IN, Pin.PULL_UP)

prev_up = btn_up.value()
prev_down = btn_down.value()
prev_sel = btn_sel.value()
prev_back = btn_back.value()

def read_events():
    """
    Read button events.
    - UP/DOWN/SEL: edge-detected (1 -> 0).
    - BACK: level-detected (0 means pressed) to make it more robust.
    """
    global prev_up, prev_down, prev_sel, prev_back

    events = {"up": False, "down": False, "sel": False, "back": False}

    cur_up = btn_up.value()
    cur_down = btn_down.value()
    cur_sel = btn_sel.value()
    cur_back = btn_back.value()

    # Edge detection for up/down/select
    if prev_up == 1 and cur_up == 0:
        events["up"] = True
    if prev_down == 1 and cur_down == 0:
        events["down"] = True
    if prev_sel == 1 and cur_sel == 0:
        events["sel"] = True

    # BACK: treat as pressed whenever it is low
    if cur_back == 0:
        events["back"] = True

    prev_up, prev_down, prev_sel, prev_back = cur_up, cur_down, cur_sel, cur_back
    return events

# =========================================================
# SMALL UI HELPER
# =========================================================
def right_text(s, y):
    # SSD1306 font is ~8px wide per character
    x = OLED_WIDTH - (len(s) * 8)
    if x < 0:
        x = 0
    oled.text(s, x, y)

# =========================================================
# SPEAKER (Feather ESP32 V2, A0 = GPIO26)
# =========================================================
SPEAKER_PIN = 26  # A0 label on Feather ESP32 V2
speaker_pwm = PWM(Pin(SPEAKER_PIN), duty=0)

def play_tone(freq, duration_ms, duty=200):
    if freq <= 0:
        time.sleep_ms(duration_ms)
        return
    speaker_pwm.freq(freq)
    speaker_pwm.duty(duty)  # 0-1023
    time.sleep_ms(duration_ms)
    speaker_pwm.duty(0)

def play_finish_tune():
    # Short completion jingle
    notes = [
        (988, 120),
        (1319, 120),
        (1568, 120),
        (1976, 200),
        (0, 80),
        (1568, 120),
        (1976, 250),
    ]
    for f, d in notes:
        play_tone(f, d)

def play_error_beep():
    for _ in range(2):
        play_tone(220, 120)
        play_tone(0, 60)

def play_click():
    # Very short UI click
    play_tone(1200, 25, duty=120)

# =========================================================
# ESP-NOW (UI ESP)
#  - Receives: "{:.4f}weight", "{:.4f}dist", "{:.2f}hum" from motor ESP
#  - Sends:    "{:.2f}target", "<id>id" to motor ESP
# =========================================================

# Init WLAN in STA mode on channel 1 (must match sender)
sta = network.WLAN(network.STA_IF)
sta.active(True)
sta.disconnect()
sta.config(channel=1)

esp = espnow.ESPNow()
esp.active(True)

# MAC of MOTOR/SENSOR ESP (14:2b:2f:af:6f:74)
MOTOR_MAC = b'\x14\x2b\x2f\xaf\x6f\x74'

esp.add_peer(MOTOR_MAC)
esp.send(MOTOR_MAC, "Starting UI...")
print("UI ESP ready, peer added:", MOTOR_MAC)

# Globals updated from ESP-NOW messages
current_weight_g = 0.0
current_distance_cm = 0.0

# Humidity starts as "no data"
current_humidity = None      # None means "no humidity data yet"

# Threshold (cm) below which we consider the cup "present"
CUP_DIST_THRESHOLD_CM = 10.0  # adjust based on your physical setup

def poll_espnow():
    """Poll ESP-NOW for new messages and update sensor globals."""
    global current_weight_g, current_distance_cm, current_humidity

    host, msg = esp.irecv(0)  # non-blocking
    if msg:
        try:
            message_data = msg.decode("utf-8").strip()
            # Example payloads from sender:
            #   "12.3456weight"
            #   "15.6789dist"
            #   "55.23hum"
            if message_data.endswith("weight"):
                value_str = message_data[:-6]  # remove "weight"
                current_weight_g = float(value_str)
            elif message_data.endswith("dist"):
                value_str = message_data[:-4]  # remove "dist"
                current_distance_cm = float(value_str)
            elif message_data.endswith("hum"):
                value_str = message_data[:-3]  # remove "hum"
                current_humidity = float(value_str)
        except Exception as e:
            # Ignore malformed messages and keep previous values
            print("Error parsing ESP-NOW message:", e)

# =========================================================
# APP DATA
# =========================================================
SPICES = [
    "Salt", "Pepper", "Paprika", "Cumin",
    "Turmeric", "Cinnamon", "Oregano", "Basil",
    "Thyme", "Chili", "Garlic", "Ginger"
]

HUMIDITY_WARNING = 60.0  # % threshold for "MOLD RISK"

# States: "HOME", "SPICE", "AMOUNT", "DISPENSING"
state = "HOME"
spice_idx = 0

# Amount selection in grams
target_g = 5.0       # default 5 g
g_step = 0.5         # step 0.5 g
g_min = 0.1          # min 0.1 g
g_max = 10.0         # max 10 g

# Dispense tracking
dispense_target_g = 0.0
dispense_spice_id = 0
DISPENSE_TOLERANCE_G = 0.1   # how close to target before we consider it "done"

# Require weight to be at/above target for some time
dispense_done_counter = 0
DISPENSE_DONE_LOOPS = 20     # 20 * 0.05s = ~1 second of stable "done"

# =========================================================
# SENSOR / ACTION FUNCTIONS (using ESP-NOW data)
# =========================================================
def get_humidity():
    """Return the last humidity value received via ESP-NOW, or None if none."""
    return current_humidity

def get_cup_present():
    """
    Use ultrasonic distance (from ESP-NOW) to decide if the cup is present.
    True if distance is below a threshold and > 0.
    """
    return current_distance_cm > 0 and current_distance_cm < CUP_DIST_THRESHOLD_CM

def start_dispense(spice_id, amount_g):
    """
    1) Send commands to the motor/sensor ESP:
       - target grams
       - spice id (0–11)
    2) Show initial 'Dispensing...' screen.
    """
    global dispense_target_g, dispense_spice_id, dispense_done_counter

    dispense_target_g = amount_g
    dispense_spice_id = spice_id
    dispense_done_counter = 0  # reset stability counter

    oled.fill(0)
    oled.text("Disp:", 0, 0)
    oled.text(SPICES[spice_id][:10], 40, 0)
    oled.text("T:{:.2f}g".format(amount_g), 0, 12)
    safe_show()

    # Send command to motor ESP
    try:
        esp.send(MOTOR_MAC, "{:.2f}".format(amount_g) + "target")
        esp.send(MOTOR_MAC, str(spice_id) + "id")
        print("Sent to motor:", amount_g, "g, id:", spice_id)
    except Exception as ex:
        print("Error sending command:", ex)

def finish_dispense():
    """Show 'Done' and play the finish tune."""
    oled.fill(0)
    oled.text("Done!", 0, 0)
    oled.text(SPICES[dispense_spice_id][:10], 0, 12)
    oled.text("F:{:.2f}g".format(current_weight_g), 0, 24)
    safe_show()
    play_finish_tune()
    time.sleep(0.7)

def flash_message(line1, line2="", line3=""):
    oled.fill(0)
    oled.text(line1[:16], 0, 0)
    if line2:
        oled.text(line2[:16], 0, 12)
    if line3:
        oled.text(line3[:16], 0, 24)
    safe_show()
    time.sleep(0.7)

def go_back():
    """Back button: AMOUNT -> SPICE, SPICE -> HOME, HOME -> no change."""
    global state
    if state == "AMOUNT":
        state = "SPICE"
    elif state == "SPICE":
        state = "HOME"
    # In DISPENSING we handle BACK separately in the main loop.

# =========================================================
# DRAW FUNCTIONS (compressed for 128x32)
# =========================================================
def draw_home():
    h = get_humidity()
    cup = get_cup_present()
    spice = SPICES[spice_idx]

    oled.fill(0)

    # Line 1: humidity + status in short form
    if h is None:
        hum_str = "H:ERR"
        status = "NO DATA"
    else:
        hum_str = "H:{:.1f}%".format(h)
        status = "RISK" if h >= HUMIDITY_WARNING else "OK"
    line1 = hum_str + " " + status
    oled.text(line1[:21], 0, 0)

    # Line 2: spice
    oled.text("Sp: " + spice[:12], 0, 12)

    # Line 3: cup + hint
    cup_str = "Cup:" + ("Y" if cup else "N")
    oled.text(cup_str, 0, 24)
    right_text("SEL Menu", 24)

    safe_show()

def draw_spice_select():
    oled.fill(0)
    oled.text("Select Spice", 0, 0)
    curr_name = SPICES[spice_idx]
    oled.text("> " + curr_name[:14], 0, 12)
    oled.text("UP/DN SEL=Amt", 0, 24)
    safe_show()

def draw_amount():
    name = SPICES[spice_idx]

    oled.fill(0)
    oled.text("Amt " + name[:10], 0, 0)
    oled.text("T:{:.2f}g".format(target_g), 0, 12)
    oled.text("UP/DN SEL=Disp", 0, 24)
    safe_show()

def draw_dispensing():
    """Show live dispensing screen with current weight vs target."""
    name = SPICES[dispense_spice_id]

    oled.fill(0)
    oled.text("Disp " + name[:10], 0, 0)
    oled.text("T:{:.2f}g".format(dispense_target_g), 0, 12)
    oled.text("N:{:.2f}g".format(current_weight_g), 0, 24)
    safe_show()

# =========================================================
# MAIN LOOP
# =========================================================
while True:
    # 1. Poll ESP-NOW for fresh sensor data (weight, dist, hum)
    poll_espnow()

    # 2. Handle button events
    events = read_events()

    # Debug: print when BACK is detected at all
    if events["back"]:
        print("BACK pressed, state:", state)

    # Play click for any button event (except in DISPENSING if you want silence)
    if state != "DISPENSING":
        if events["up"] or events["down"] or events["sel"] or events["back"]:
            play_click()

    # BACK behavior in HOME / SPICE / AMOUNT
    if state in ("HOME", "SPICE", "AMOUNT") and events["back"]:
        go_back()

    if state == "HOME":
        if events["sel"]:
            state = "SPICE"
        draw_home()

    elif state == "SPICE":
        if events["up"]:
            spice_idx = (spice_idx - 1) % len(SPICES)
        if events["down"]:
            spice_idx = (spice_idx + 1) % len(SPICES)
        if events["sel"]:
            state = "AMOUNT"
        draw_spice_select()

    elif state == "AMOUNT":
        if events["up"]:
            target_g = min(g_max, target_g + g_step)
        if events["down"]:
            target_g = max(g_min, target_g - g_step)

        if events["sel"]:
            if get_cup_present():
                # Start dispensing and move to DISPENSING state
                start_dispense(spice_idx, target_g)
                state = "DISPENSING"
            else:
                flash_message("NO CUP", "Place cup")
                play_error_beep()

        draw_amount()

    elif state == "DISPENSING":
        # Keep showing live weight vs target
        draw_dispensing()

        # Allow cancel with BACK here: stop UI, show canceled
        if events["back"]:
            flash_message("Canceled")
            state = "HOME"

        # Check if we've reached (or slightly exceeded) the target
        # and stayed there for a little while
        if current_weight_g >= (dispense_target_g - DISPENSE_TOLERANCE_G):
            dispense_done_counter += 1
        else:
            dispense_done_counter = 0

        if dispense_done_counter >= DISPENSE_DONE_LOOPS:
            finish_dispense()
            state = "HOME"

    time.sleep(0.05)