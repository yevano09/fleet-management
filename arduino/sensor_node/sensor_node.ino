/*
 * Fleet Commander — Arduino sensor node (P0-E edge track)
 *
 * Role: dumb I2C sensor front-end for the ESP32 gateway. The Uno/Nano
 * (2KB RAM) runs NO network and NO ML: it samples a DHT22 (temp/humidity)
 * and an MPU6050 (accel) and streams compact JSON lines over Serial @115200.
 * The ESP32 reads these lines, runs the threshold verdict engine, and
 * publishes to MQTT. Wire: Arduino TX->ESP32 RX2, RX->TX2, common GND.
 *
 * Line protocol (one JSON object per line, ~2 Hz):
 *   {"t":26.4,"h":61.2,"ax":0.02,"ay":-0.01,"az":1.01,"g":0.03}
 * Requires: DHT sensor library, Adafruit MPU6050, ArduinoJson (v6).
 */
#include <ArduinoJson.h>
#include <DHT.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Wire.h>

#define DHT_PIN   2
#define DHT_TYPE  DHT22
#define SAMPLE_MS 500

DHT dht(DHT_PIN, DHT_TYPE);
Adafruit_MPU6050 mpu;
bool hasMpu = false;

void setup() {
  Serial.begin(115200);
  dht.begin();
  if (mpu.begin()) {
    hasMpu = true;
    mpu.setAccelerometerRange(MPU6050_RANGE_16_G);
  }
  // Announce capabilities once for the gateway log.
  Serial.println(F("{\"node\":\"arduino-sensor\",\"dht\":true,\"mpu\":true}"));
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
  serializeJson(doc, Serial);
  Serial.println();
}
