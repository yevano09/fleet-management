/*
 * Fleet Commander — UNO Q sensor bridge (P0-E edge track)
 *
 * Runs on the STM32 MCU half of the Arduino UNO Q. Samples a DHT22
 * (temp/humidity) and an MPU6050 (accel) and streams compact JSON lines
 * over Serial1 @115200 to the QRB Linux SoC, which runs the edge gateway.
 *
 * No network, no ML on the STM32 — pure sensor front-end.
 *
 * Line protocol (one JSON object per line, ~2 Hz):
 *   {"t":26.4,"h":61.2,"ax":0.02,"ay":-0.01,"az":1.01,"g":0.03}
 *
 * Wiring:
 *   DHT22 data pin -> D2 (with 10k pull-up to 3.3V)
 *   MPU6050 SDA    -> SDA, SCL -> SCL (I2C share with DHT22 bus)
 *   Serial1 (TX/RX) is the UART bridge to the Linux SoC — no USB needed.
 *
 * Requires: DHT sensor library, Adafruit MPU6050, ArduinoJson (v6).
 *
 * WOKWI QUICK-START (no physical UNO Q needed):
 *   1. Open https://wokwi.com, create a new Arduino Uno project.
 *   2. Replace the default code with this file.
 *   3. Add a DHT22 sensor (pin 2) and MPU6050 (I2C SDA/SCL) to the diagram.
 *   4. Click Play — the Serial Monitor will show the same JSON lines the
 *      real STM32 produces.
 *   The WOKWI define below switches Serial1 (UNO Q UART bridge) to Serial
 *   (USB monitor) so the exact same sketch compiles on both.
 */
#include <ArduinoJson.h>
#include <DHT.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Wire.h>

#if defined(WOKWI)
  #define UART Serial
#else
  #define UART Serial1
#endif

#define DHT_PIN   2
#define DHT_TYPE  DHT22
#define SAMPLE_MS 500

DHT dht(DHT_PIN, DHT_TYPE);
Adafruit_MPU6050 mpu;
bool hasMpu = false;

void setup() {
  UART.begin(115200);
  dht.begin();
  if (mpu.begin()) {
    hasMpu = true;
    mpu.setAccelerometerRange(MPU6050_RANGE_16_G);
  }
  UART.println(F("{\"node\":\"uno-q-stm32\",\"dht\":true,\"mpu\":true}"));
}

void loop() {
  static unsigned long last = 0;
  unsigned long now = millis();
  if (now - last < SAMPLE_MS) return;
  last = now;

  float t = dht.readTemperature();
  float h = dht.readHumidity();
  float ax = 0, ay = 0, az = 1.0, g = 0.0;
  if (hasMpu) {
    sensors_event_t a, gyr, tmp;
    mpu.getEvent(&a, &gyr, &tmp);
    ax = a.acceleration.x / 9.81;
    ay = a.acceleration.y / 9.81;
    az = a.acceleration.z / 9.81;
    g = sqrt(ax * ax + ay * ay + (az - 1.0) * (az - 1.0));
  }

  StaticJsonDocument<160> doc;
  if (!isnan(t)) doc["t"] = t;
  if (!isnan(h)) doc["h"] = h;
  doc["ax"] = ax;
  doc["ay"] = ay;
  doc["az"] = az;
  doc["g"] = g;
  serializeJson(doc, UART);
  UART.println();
}
