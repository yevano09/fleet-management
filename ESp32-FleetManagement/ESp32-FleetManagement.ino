#include <ArduinoOTA.h>

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Update.h>
#include <HTTPClient.h>

// ===== CONFIGURATION =====
const char* WIFI_SSID     = "";
const char* WIFI_PASSWORD = "";

const char* MQTT_BROKER   = "192.168.0.135";  // IP or hostname of Mosquitto
const int   MQTT_PORT     = 1883;
const char* MQTT_USER     = "";                // leave empty if anonymous
const char* MQTT_PASS     = "";

// Device identity — set these per device
const char* DEVICE_NAME   = "ESP32-Garage-001-REAL";
const char* FW_VERSION    = "1.0.2";

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
  String topicStr = String(topic);
  Serial.printf("Topic is : %s\n", topicStr.c_str());
  
  StaticJsonDocument<512> doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    Serial.printf("MQTT JSON parse error: %s\n", err.c_str());
    return;
  }
  
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
  } else {
    Serial.println("not proper syntax:");
    Serial.println();
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
  mqtt.loop();  // flush the "downloading" status to the broker

  HTTPClient http;
  http.setTimeout(30000);
  http.begin(otaFirmwareUrl);
  int httpCode = http.GET();

  if (httpCode != 200) {
    Serial.printf("OTA download failed: HTTP %d\n", httpCode);
    reportOtaStatus("failed", "HTTP download error");
    mqtt.loop();
    http.end();
    return;
  }

  int contentLength = http.getSize();
  if (contentLength <= 0) {
    Serial.println("OTA: invalid content length");
    reportOtaStatus("failed", "Invalid content length");
    mqtt.loop();
    http.end();
    return;
  }

  bool canBegin = Update.begin(contentLength);
  if (!canBegin) {
    Serial.println("OTA: not enough space");
    reportOtaStatus("failed", "Insufficient flash space");
    mqtt.loop();
    http.end();
    return;
  }

  WiFiClient* stream = http.getStreamPtr();
  size_t written = Update.writeStream(*stream);

  if (written != contentLength) {
    Serial.printf("OTA: wrote %d of %d bytes\n", written, contentLength);
    reportOtaStatus("failed", "Partial write");
    mqtt.loop();
    http.end();
    return;
  }

  if (!Update.end()) {
    Serial.printf("OTA: Update.end error: %s\n", Update.errorString());
    reportOtaStatus("failed", Update.errorString());
    mqtt.loop();
    http.end();
    return;
  }

  if (!Update.isFinished()) {
    Serial.println("OTA: Update not finished");
    reportOtaStatus("failed", "Update not finished");
    mqtt.loop();
    http.end();
    return;
  }

  reportOtaStatus("success");
  mqtt.loop();  // flush "success" before restart

  http.end();

  Serial.println("OTA success! Rebooting in 3 seconds...");
  delay(3000);
  ESP.restart();
}

// ===== SETUP =====
void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("\n\nFleet Commander ESP32 Client - via OTA update");

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
  mqtt.setBufferSize(512);
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