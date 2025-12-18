from hx711 import HX711
from time import sleep
import network
import espnow
from machine import Pin
from hcsr04 import HCSR04
from uln2003 import Stepper, HALF_STEP, FULL_ROTATION
import dht

# =========================================================
# ESP32 SENSORS
# =========================================================
sensor = HX711(dout=15, pd_sck=5)                      # load cell
ultrasonic = HCSR04(trigger_pin=7, echo_pin=8, echo_timeout_us=10000)
humid_temp = dht.DHT11(Pin(32))                        # humidity + temp

# =========================================================
# ESP32 MOTORS (ULN2003)
# s1: carousel (select spice)
# s2: dispenser (tilt/pour)
# =========================================================
s1 = Stepper(HALF_STEP, 13, 12, 27, 33, delay=1)       # carousel motor
s2 = Stepper(HALF_STEP, 26, 14, 25, 4,  delay=1)       # dispensing motor

# One full rotation with HALF_STEP mode:
# FULL_ROTATION is provided by uln2003 lib.
# With 12 containers around the circle:
SLOTS = 12
STEPS_PER_SLOT = 128   # ~1/12 revolution per slot

# =========================================================
# ESP-NOW SETUP
# =========================================================
sta = network.WLAN(network.STA_IF)
sta.active(True)
sta.disconnect()
sta.config(channel=1)

e = espnow.ESPNow()
e.active(True)

# MAC address of UI ESP32 (14:2b:2f:af:95:d8)
UI_MAC = b'\x14\x2b\x2f\xaf\x95\xd8'
e.add_peer(UI_MAC)
e.send(UI_MAC, "Starting...")
print("Sender/motor ready.")

# =========================================================
# LOAD CELL TARE
# =========================================================
offset_val = sensor.get_value()
sensor.set_offset(offset_val)

# =========================================================
# STATE VARIABLES
# =========================================================
current_slot = 0       # which container we are currently under (0–11)
target_weight = 0.0    # grams requested from UI
spice_id = 0           # current target spice (0–11)

def motor_sequence(sp_id, tar_wt):
    """
    1) Rotate carousel (s1) from current_slot to sp_id.
    2) Run dispenser motor (s2) once.
       (You can expand this later to use the load cell loop.)
    """
    global current_slot, spice_id

    sp_id = int(sp_id) % SLOTS

    # ---- 1. Move carousel to the requested slot ----
    diff = sp_id - current_slot
    
    if diff == 0:
        print("Already at container", sp_id)
    else:
        # Decide direction and number of slots to move
        direction = 1
        if diff < 0:
            direction = -1
            diff = -diff

        steps = diff * STEPS_PER_SLOT
        print("Moving from slot", current_slot, "to", sp_id,
              "-> slots:", diff, "steps:", steps, "dir:", direction)
        s1.step(int(steps), direction)

        current_slot = sp_id
        print("Now at container", current_slot)

    # ---- 2. Simple dispense action with s2 ----
    print("Running dispenser motor (s2) once...")   # rotate forward to pour (tune this)
    #sleep(0.5)
    weight_raw = sensor.get_value()
    weight_g_local = -weight_raw / 1000.0

    # Here is a VERY simple check: if we overshot tar_wt, back off.
    # In a real implementation, you'd do a feedback loop around weight_g.
    while weight_g_local < tar_wt:
        s2.step(FULL_ROTATION)
        weight_raw = sensor.get_value()
        weight_g_local = -weight_raw / 1000.0
        humidity_loc = humid_temp.humidity()
        e.send(UI_MAC, "{:.4f}".format(weight_g_local) + "weight")
        e.send(UI_MAC, "{:.2f}".format(humidity_loc) + "hum")
        print("Weight after dispense:", weight_g_local, "g (target:", tar_wt, ")")

    #if weight_g_local > tar_wt:
    print("Over target, reversing dispenser slightly...")
    #s2.step(FULL_ROTATION/2, -1)   # small reverse (tune this)

    print("Dispense sequence complete.")

# =========================================================
# MAIN LOOP
# =========================================================
while True:
    # 1. Receive commands from UI ESP
    host, msg = e.irecv(0)
    if msg:
        try:
            message_data = msg.decode("utf-8").strip()
            # Examples from UI:
            #   "5.00target"
            #   "3id"
            if message_data.endswith("target"):
                value_str = message_data[:-6]
                target_weight = float(value_str)
                print("Received target_weight:", target_weight)
            elif message_data.endswith("id"):
                value_str = message_data[:-2]
                spice_id = int(value_str)
                print("Received spice_id:", spice_id)

                # When we get an ID, we know target_weight was just sent.
                # Run the sequence: rotate + dispense.
                #spice_id, target_weight
                motor_sequence(spice_id, target_weight)
        except Exception as ex:
            print("Error parsing command:", ex)

    # 2. Read sensors and send telemetry back to UI
    weight_raw = sensor.get_value()
    distance = ultrasonic.distance_cm()
    humid_temp.measure()
    temperature = humid_temp.temperature()
    humidity = humid_temp.humidity()

    weight_g = -weight_raw / 1000.0

    # Send to UI
    e.send(UI_MAC, "{:.4f}".format(weight_g) + "weight")
    e.send(UI_MAC, "{:.4f}".format(distance) + "dist")
    e.send(UI_MAC, "{:.2f}".format(humidity) + "hum")

    # Debug prints
    print("Spice ID:", spice_id, "Target Weight:", target_weight,
          "Weight_g:", weight_g, "Dist:", distance, "Hum:", humidity)

    sleep(0.05)