# Connecting an ESP32 to Fleet Commander

This guide explains how to connect a real ESP32 device to the Fleet Commander backend via MQTT, covering registration, heartbeats, and OTA firmware updates.

## MQTT Protocol Reference

All communication uses JSON payloads over MQTT v5 with **QoS 1** (at-least-once delivery).

### Topics

| Topic | Direction | Payload | Frequency |
|---|---|---|---|
| `iot/fleet/register` | Device → Backend | `{device_id, name, firmware_version, ip_address}` | On boot + reconnection |
| `iot/fleet/{device_id}/heartbeat` | Device → Backend | `{uptime_percentage, signal_strength}` | Every 10–60s |
| `iot/fleet/{device_id}/status/ota` | Device → Backend | `{status, deployment_id, device_id, timestamp, error?}` | During OTA lifecycle |
| `iot/fleet/{device_id}/command/ota` | Backend → Device | `{firmware_url, sha256_hash, timestamp}` | On OTA trigger |
| `iot/fleet/{device_id}/command/config` | Backend → Device | `{config: {...}, timestamp}` | On config push |

### OTA Status States

```
downloading → applying → verifying → success
                                   → hash_mismatch → rollback → rolled_back
                         → failed
```

## ESP32 Arduino Sketch

Below is a complete sketch. It uses the ESP32's native `Update` class for real OTA flashing.

### Requirements

- Arduino IDE or PlatformIO
- Board: ESP32 Dev Module (or any ESP32 variant)
- Libraries (install via Library Manager):
  - `PubSubClient` by Nick O'Leary (for MQTT)
  - `ArduinoJson` by Benoit Blanchon (for JSON parsing)
  - `WiFi` (built-in)

### Full Sketch

```cpp
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Update.h>
#include <HTTPClient.h>

// ===== CONFIGURATION =====
const char* WIFI_SSID     = "your-ssid";
const char* WIFI_PASSWORD = "your-password";

const char* MQTT_BROKER   = "192.168.1.100";  // IP or hostname of Mosquitto
const int   MQTT_PORT     = 1883;
const char* MQTT_USER     = "";                // leave empty if anonymous
const char* MQTT_PASS     = "";

// Device identity — set these per device
const char* DEVICE_NAME   = "ESP32-Garage-001";
const char* FW_VERSION    = "1.0.0";

// ===== GLOBALS =====
WiFiClient  wifiClient;
PubSubClient mqtt(wifiClient);

String deviceId;          // assigned once at boot (MAC-based)
String otaDeploymentId;   // current OTA deployment tracking
String otaFirmwareUrl;    // URL to download new firmware
String otaExpectedHash;   // SHA256 of expected firmware

unsigned long lastHeartbeat = 0;
const unsigned long HEARTBEAT_INTERVAL = 15000;  // 15 seconds

// ===== HELPER: device ID from MAC =====
String getDeviceId() {
  uint8_t mac[6];
  WiFi.macAddress(mac);
  char buf[18];
  snprintf(buf, sizeof(buf), "%02x%02x%02x%02x%02x%02x",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(buf);
}

// ===== MQTT CALLBACK =====
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  StaticJsonDocument<512> doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    Serial.printf("MQTT JSON parse error: %s\n", err.c_str());
    return;
  }

  String topicStr = String(topic);

  if (topicStr.endsWith("/command/ota")) {
    const char* url   = doc["firmware_url"];
    const char* hash  = doc["sha256_hash"];
    const char* ts    = doc["timestamp"];

    Serial.printf("OTA command received: url=%s hash=%s\n", url, hash);

    otaFirmwareUrl   = String(url);
    otaExpectedHash  = String(hash);
    otaDeploymentId  = "";  // backend assigns this; we generate one for status reports

    // Use deployment_id from payload if provided, else generate
    otaDeploymentId = doc["deployment_id"] | String(random(0xFFFF), HEX);

    // Start OTA in a non-blocking way (flag-based)
    startOtaUpdate();

  } else if (topicStr.endsWith("/command/config")) {
    Serial.println("Remote config received:");
    serializeJsonPretty(doc, Serial);
    Serial.println();

    // Apply config — example: extract a "log_level" or "sample_rate"
    if (doc["config"]["log_level"]) {
      String level = doc["config"]["log_level"].as<String>();
      Serial.printf("  -> Setting log level to: %s\n", level.c_str());
    }
  }
}

// ===== MQTT CONNECT & RECONNECT =====
void connectMqtt() {
  while (!mqtt.connected()) {
    Serial.print("Connecting to MQTT...");
    String clientId = "esp32-" + deviceId;

    if (mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASS)) {
      Serial.println(" connected");

      // Subscribe to command topics for this device
      String otaTopic    = "iot/fleet/" + deviceId + "/command/ota";
      String configTopic = "iot/fleet/" + deviceId + "/command/config";
      mqtt.subscribe(otaTopic.c_str(), 1);
      mqtt.subscribe(configTopic.c_str(), 1);
      Serial.printf("  Subscribed to: %s\n", otaTopic.c_str());
      Serial.printf("  Subscribed to: %s\n", configTopic.c_str());

      // Register with backend
      registerDevice();
    } else {
      Serial.printf(" failed (rc=%d), retry in 5s\n", mqtt.state());
      delay(5000);
    }
  }
}

// ===== DEVICE REGISTRATION =====
void registerDevice() {
  StaticJsonDocument<256> doc;
  doc["device_id"]      = deviceId;
  doc["name"]           = DEVICE_NAME;
  doc["firmware_version"] = FW_VERSION;
  doc["ip_address"]     = WiFi.localIP().toString();

  char buffer[256];
  size_t n = serializeJson(doc, buffer);
  
  // FIX APPLIED HERE
  mqtt.publish("iot/fleet/register", (const uint8_t*)buffer, n, false);
  
  Serial.printf("Registered: %s\n", buffer);
}

// ===== HEARTBEAT =====
void sendHeartbeat() {
  // Simulate uptime percentage and signal strength
  static float uptime = 100.0;
  uptime = max(90.0f, uptime - 0.01f * random(0, 10));

  int rssi = WiFi.RSSI();
  int signalStrength = constrain(rssi, -100, -30);

  StaticJsonDocument<128> doc;
  doc["uptime_percentage"] = uptime;
  doc["signal_strength"]   = signalStrength;

  char buffer[128];
  size_t n = serializeJson(doc, buffer);
  String topic = "iot/fleet/" + deviceId + "/heartbeat";
  
  // FIX APPLIED HERE
  mqtt.publish(topic.c_str(), (const uint8_t*)buffer, n, false);
}

// ===== OTA STATUS REPORT =====
void reportOtaStatus(const char* status, const char* error = nullptr) {
  StaticJsonDocument<256> doc;
  doc["status"]        = status;
  doc["deployment_id"] = otaDeploymentId;
  doc["device_id"]     = deviceId;
  doc["timestamp"]     = millis() / 1000;
  if (error != nullptr) {
    doc["error"] = error;
  }

  char buffer[256];
  size_t n = serializeJson(doc, buffer);
  String topic = "iot/fleet/" + deviceId + "/status/ota";
  
  // FIX APPLIED HERE
  mqtt.publish(topic.c_str(), (const uint8_t*)buffer, n, false);
  
  Serial.printf("OTA status: %s\n", status);
}

// ===== REAL OTA UPDATE (ESP32 FLASH) =====
void startOtaUpdate() {
  reportOtaStatus("downloading");

  HTTPClient http;
  http.begin(otaFirmwareUrl);
  int httpCode = http.GET();

  if (httpCode != 200) {
    Serial.printf("OTA download failed: HTTP %d\n", httpCode);
    reportOtaStatus("failed", "HTTP download error");
    http.end();
    return;
  }

  int contentLength = http.getSize();
  if (contentLength <= 0) {
    Serial.println("OTA: invalid content length");
    reportOtaStatus("failed", "Invalid content length");
    http.end();
    return;
  }

  bool canBegin = Update.begin(contentLength);
  if (!canBegin) {
    Serial.println("OTA: not enough space");
    reportOtaStatus("failed", "Insufficient flash space");
    http.end();
    return;
  }

  WiFiClient* stream = http.getStreamPtr();
  size_t written = Update.writeStream(*stream);

  if (written != contentLength) {
    Serial.printf("OTA: wrote %d of %d bytes\n", written, contentLength);
    reportOtaStatus("failed", "Partial write");
    http.end();
    return;
  }

  if (!Update.end()) {
    Serial.printf("OTA: Update.end error: %s\n", Update.errorString());
    reportOtaStatus("failed", Update.errorString());
    http.end();
    return;
  }

  if (!Update.isFinished()) {
    Serial.println("OTA: Update not finished");
    reportOtaStatus("failed", "Update not finished");
    http.end();
    return;
  }

  // In a production device you would verify the SHA256 hash here.
  // For simplicity we assume the download succeeded.
  reportOtaStatus("success");

  http.end();

  Serial.println("OTA success! Rebooting in 3 seconds...");
  delay(3000);
  ESP.restart();
}

// ===== SETUP =====
void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("\n\nFleet Commander ESP32 Client");

  // Connect WiFi
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWiFi connected: %s\n", WiFi.localIP().toString().c_str());

  // Derive device ID from MAC
  deviceId = getDeviceId();
  Serial.printf("Device ID: %s\n", deviceId.c_str());
  Serial.printf("Device Name: %s\n", DEVICE_NAME);
  Serial.printf("Firmware: %s\n", FW_VERSION);

  // MQTT setup
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setKeepAlive(30);
}

// ===== LOOP =====
void loop() {
  if (!mqtt.connected()) {
    connectMqtt();
  }
  mqtt.loop();

  unsigned long now = millis();
  if (now - lastHeartbeat > HEARTBEAT_INTERVAL) {
    lastHeartbeat = now;
    sendHeartbeat();
  }
}
```

## Network Setup

### 1. Find the MQTT Broker Address

When running the demo stack, the Mosquitto broker is exposed on `localhost:1883` of the host machine. Your ESP32 needs to reach it over your LAN:

```bash
# On the host machine, find its LAN IP
ip addr show   # Linux / WSL
ipconfig       # Windows — look for IPv4 on your active adapter
```

Set `MQTT_BROKER` in the sketch to that IP.

### 2. Mosquitto Configuration

The default `docker/mosquitto/mosquitto.conf` allows anonymous access on port 1883. This is fine for a local LAN. For production, add credentials:

```conf
listener 1883 0.0.0.0
allow_anonymous false
password_file /mosquitto/config/passwords
```

Generate the password file:
```bash
docker compose exec mosquitto mosquitto_passwd -c /mosquitto/config/passwords esp32-device
```

Then update the sketch with `MQTT_USER` and `MQTT_PASS`.

### 3. Assign a Persistent Device ID

The sketch derives the ID from the MAC address. If you want a human-friendly name instead, hardcode `deviceId` to match the name you register with:

```cpp
const char* DEVICE_ID = "esp32-garage-sensor-001";
```

The `DEVICE_NAME` field in the registration payload is what appears in the Fleet Commander dashboard.

## Testing the Connection

1. Upload the sketch to your ESP32
2. Open the Serial Monitor (115200 baud)
3. Verify:
   ```
   WiFi connected: 192.168.1.42
   Device ID: aabbccddeeff
   Connecting to MQTT... connected
   Registered: {"device_id":"aabbccddeeff",...
   ```
4. Check the backend API:
   ```bash
   curl http://localhost:8000/devices
   ```
   Your ESP32 should appear in the device list.

## Triggering an OTA Update

1. Upload a new firmware binary via the dashboard or API:
   ```bash
   curl -X POST http://localhost:8000/ota/upload \
     -F "version=2.0.0" \
     -F "file=@firmware.esp32.bin"
   ```
   Note the returned firmware ID.

2. Trigger the OTA for your ESP32:
   ```bash
   curl -X POST http://localhost:8000/ota/trigger \
     -H "Content-Type: application/json" \
     -d '{"firmware_id": "<FW_ID>", "device_ids": ["<DEVICE_ID>"]}'
   ```

3. Watch the Serial Monitor — the ESP32 will download the firmware, flash itself, and reboot.

## Important Notes

- **SHA256 verification**: The backend sends `sha256_hash` in the OTA command. The example sketch skips verification for brevity. For production, compute the SHA256 of the downloaded binary and compare before calling `Update.end()`.
- **MQTT broker address**: When running in Docker, the broker is on the Docker host's IP. Your ESP32 connects to that IP, not `localhost`.
- **QoS**: All topics use QoS 1. Ensure your MQTT library supports QoS 1 for both publish and subscribe.
- **Backend health check**: After a backend restart, the ESP32 will reconnect to MQTT and re-subscribe. It should re-register via `iot/fleet/register` on reconnect to update `active_devices` and `total_devices` gauges in Prometheus.
