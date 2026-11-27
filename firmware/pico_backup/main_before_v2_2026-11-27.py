from machine import UART, I2C, Pin
import time
import ujson

# ======================
# Initialize Interfaces
# ======================

# Sensor 1 - UART0 (GP16 TX, GP17 RX)
uart1 = UART(0, baudrate=115200, tx=Pin(16), rx=Pin(17))

# Sensor 2 - UART1 (GP8 TX, GP9 RX)
uart2 = UART(1, baudrate=115200, tx=Pin(8), rx=Pin(9))

# Sensor 3 - I2C (GP4 SDA, GP5 SCL)
i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400000)
TF_LUNA_ADDR = 0x10  # Default I2C address
uart_buffers = {}

# ======================
# Helper Functions
# ======================


def read_uart_lidar(uart):
    """Read distance from TF-Luna in UART mode"""
    if not uart.any():
        return None, None
    data = uart.read(uart.any())
    key = id(uart)
    if key not in uart_buffers:
        uart_buffers[key] = bytearray()
    uart_buffers[key].extend(data)
    while len(uart_buffers[key]) >= 9:
        if uart_buffers[key][0] != 0x59 or uart_buffers[key][1] != 0x59:
            uart_buffers[key] = uart_buffers[key][1:]
            continue
        packet = bytes(uart_buffers[key][:9])
        uart_buffers[key] = uart_buffers[key][9:]
        checksum = sum(packet[:8]) & 0xFF
        if checksum != packet[8]:
            continue
        distance = packet[2] + (packet[3] << 8)  # cm
        strength = packet[4] + (packet[5] << 8)
        return distance, strength
    return None, None


def read_i2c_lidar():
    """Read distance from TF-Luna in I2C mode"""
    try:
        # Read 2 bytes starting from register 0x00 (Distance)
        data = i2c.readfrom_mem(TF_LUNA_ADDR, 0x00, 2)
        distance = data[0] + (data[1] << 8)  # cm
        return distance
    except:
        return None


# ======================
# Main Loop
# ======================

print("Starting TF-Luna reading...")
time.sleep(1)  # Give sensors time to boot

while True:
    # Read Sensor 1 (UART0)
    dist1, str1 = read_uart_lidar(uart1)

    # Read Sensor 2 (UART1)
    dist2, str2 = read_uart_lidar(uart2)

    # Read Sensor 3 (I2C)
    dist3 = read_i2c_lidar()

    # JSONL over the Pico W USB serial/COM port.
    sensors = [
        {"id": 1, "distance_cm": dist1, "distance_mm": dist1 * 10 if dist1 is not None else None, "strength": str1},
        {"id": 2, "distance_cm": dist2, "distance_mm": dist2 * 10 if dist2 is not None else None, "strength": str2},
        {"id": 3, "distance_cm": dist3, "distance_mm": dist3 * 10 if dist3 is not None else None, "strength": 0},
    ]
    print(ujson.dumps({
        "type": "tf_luna",
        "t": time.ticks_ms(),
        "sensors": sensors,
    }))

    time.sleep(0.2)  # Adjust reading speed as needed
