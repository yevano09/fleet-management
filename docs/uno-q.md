# Fleet Commander — Arduino UNO Q Edge Device (P0-E)

The UNO Q replaces the Arduino Uno/Nano + ESP32 hub pair with a single
board. Its STM32 MCU half samples sensors; its QRB Linux SoC half runs the
full ONNX IsolationForest edge gateway — the same model contract as the
cloud, unlike the ESP32 (thresholds only).

```
[DHT22+MPU6050] --I2C--> [STM32 MCU] --Serial1 115200--> [QRB Linux SoC]
                          (uno_q_bridge.ino)              (uno_q_gateway.py)
                                                                │
                                                           WiFi → MQTT broker
                                                                │
                                                     iot/fleet/{id}/edge verdicts
```

## Files

| File | Runs on | Role |
|---|---|---|
| `arduino/uno-q/uno_q_bridge.ino` | STM32 MCU | DHT22+MPU6050 @2Hz, JSON over Serial1 |
| `edge/uno_q_gateway.py` | QRB Linux SoC | UART → IsolationForest ONNX → MQTT verdicts |
| `Dockerfile.uno-q` | QRB Linux SoC | Container image for the gateway |

## Wiring

| Signal | STM32 Pin | Notes |
|---|---|---|
| DHT22 data | D2 | 10kΩ pull-up to 3.3V |
| MPU6050 SDA | SDA | I2C bus shared |
| MPU6050 SCL | SCL | I2C bus shared |
| Serial1 TX → Linux RX | TX1 | UART bridge to QRB SoC |
| Serial1 RX ← Linux TX | RX1 | UART bridge to QRB SoC |
| GND | GND | Common ground |

## Setup

### 1. Flash the STM32

Open `arduino/uno-q/uno_q_bridge.ino` in Arduino IDE, select board
"Arduino UNO Q (STM32)", install libraries (DHT, Adafruit MPU6050,
ArduinoJson v6), upload. The STM32 streams JSON lines over Serial1.

### 2. Prepare the edge model pack

```bash
python scripts/export_onnx.py          # produces data/models/edge/mvp-iforest-v1/
# Copy to the UNO Q:
scp -r data/models/edge uno-q:/app/edge-pack
```

### 3. Run the gateway on the QRB SoC

```bash
pip install paho-mqtt pyserial onnxruntime
export EDGE_BROKER_HOST=192.168.1.100   # your Mosquitto broker IP
export UNO_Q_DEVICE_ID=uno-q-001        # this device's fleet id
export EDGE_MODEL_DIR=/app/edge-pack/mvp-iforest-v1
python edge/uno_q_gateway.py
```

Or with Docker:

```bash
docker build -f Dockerfile.uno-q -t fleet-uno-q .
docker run --device=/dev/ttyHS1 \
  -e EDGE_BROKER_HOST=192.168.1.100 \
  -e UNO_Q_DEVICE_ID=uno-q-001 \
  -v ./data/models:/app/edge-pack \
  fleet-uno-q
```

## Advantages over the ESP32 hub

| Capability | ESP32 hub | UNO Q |
|---|---|---|
| Model | Threshold pack only | Full IsolationForest ONNX |
| RAM | ~320KB | ~1GB DDR |
| Cargo frames | Yes (simulated) | Yes (via UART from STM32) |
| OTA firmware update | Yes | Model OTA via MOTA-01 (future) |
| Offline buffer | sqlite outbox | sqlite outbox (same) |
| Contract | `+/edge` verdicts | `+/edge` verdicts (identical) |

## Simulated fallback

If the UART device is unavailable (STM32 not flashed or wiring issue), the
gateway falls back to a slow thermal drift simulation so demos and tests
still produce verdicts. The `source` field in each point is `uno-q-sim`
instead of `uno-q-stm32` so the cloud can distinguish real sensor data.
