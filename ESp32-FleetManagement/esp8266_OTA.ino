#include <ArduinoOTA.h>
#include <ESP8266WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Updater.h>
#include <ESP8266HTTPClient.h>

// ===== CONFIGURATION =====
const char* WIFI_SSID     = "";
const char* WIFI_PASSWORD = "";

const char* MQTT_BROKER   = "";  // IP or hostname of Mosquitto
const int   MQTT_PORT     = 1883;
const char* MQTT_USER     = "";                 // leave empty if anonymous
const char* MQTT_PASS     = "";

// Device identity — set these per device
const char* DEVICE_NAME   = "ESP8266-Garage-001-REAL";
const char* FW_VERSION    = "1.0.2";

// ===== EDGE AI (threshold verdict engine, P0-E) =====
#define HAS_SENSORS 0
float EDGE_TEMP_SLOPE = 0.3;    // C per beat
float EDGE_TEMP_MAX   = 70.0;   // C
float EDGE_SIG_SLOPE  = -0.5;   // dBm per beat
const char* EDGE_MODEL_VERSION = "threshold-pack-v1";

// Rolling windows (ring buffers, no heap churn)
#define EDGE_WIN 12
float edgeTempWin[EDGE_WIN]; int edgeTempIdx = 0; int edgeTempN = 0;
float edgeSigWin[EDGE_WIN];  int edgeSigIdx = 0;  int edgeSigN = 0;

// Cargo bay state
float cargoTemp = 3.0, cargoHum = 68.0;
bool  cargoDoor = false;
float lastShockG = 0.2;

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

// Forward declarations
void startOtaUpdate();
void registerDevice();

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
    const char* url  = doc["firmware_url"];
    const char* hash = doc["sha256_hash"];

    Serial.printf("OTA command received: url=%s hash=%s\n", url ? url : "", hash ? hash : "");

    if (url) otaFirmwareUrl = String(url);
    if (hash) otaExpectedHash = String(hash);
    
    // Use deployment_id from payload if provided, else generate
    otaDeploymentId = doc["deployment_id"] | String(random(0xFFFF), HEX);

    // Start OTA in a non-blocking way (flag-based)
    startOtaUpdate();

  } else if (topicStr.endsWith("/command/config")) {
    Serial.println("Remote config received:");
    serializeJsonPretty(doc, Serial);
    Serial.println();

    if (doc["config"]["log_level"]) {
      String level = doc["config"]["log_level"].as<String>();
      Serial.printf("  -> Setting log level to: %s\n", level.c_str());
    }
  } else if (topicStr.endsWith("/command/edge")) {
    if (!doc["temp_slope"].isNull())   EDGE_TEMP_SLOPE = doc["temp_slope"];
    if (!doc["temp_max"].isNull())     EDGE_TEMP_MAX   = doc["temp_max"];
    if (!doc["sig_slope"].isNull())    EDGE_SIG_SLOPE  = doc["sig_slope"];
    if (!doc["model_version"].isNull()) EDGE_MODEL_VERSION = doc["model_version"].as<const char*>();
    Serial.printf("Edge thresholds updated: temp_slope=%.2f temp_max=%.1f sig_slope=%.2f v=%s\n",
                  EDGE_TEMP_SLOPE, EDGE_TEMP_MAX, EDGE_SIG_SLOPE, EDGE_MODEL_VERSION);
  } else {
    Serial.println("not proper syntax:\n");
  }
}

// ===== MQTT CONNECT & RECONNECT =====
void connectMqtt() {
  while (!mqtt.connected()) {
    Serial.print("Connecting to MQTT...");
    String clientId = "esp8266-" + deviceId;

    if (mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASS)) {
      Serial.println(" connected");

      String otaTopic    = "iot/fleet/" + deviceId + "/command/ota";
      String configTopic = "iot/fleet/" + deviceId + "/command/config";
      String edgeTopic   = "iot/fleet/" + deviceId + "/command/edge";
      
      mqtt.subscribe(otaTopic.c_str(), 1);
      mqtt.subscribe(configTopic.c_str(), 1);
      mqtt.subscribe(edgeTopic.c_str(), 1);
      
      Serial.printf("  Subscribed to: %s\n", otaTopic.c_str());
      Serial.printf("  Subscribed to: %s\n", configTopic.c_str());

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
  doc["device_id"]        = deviceId;
  doc["name"]             = DEVICE_NAME;
  doc["firmware_version"] = FW_VERSION;
  doc["ip_address"]       = WiFi.localIP().toString();

  char buffer[256];
  size_t n = serializeJson(doc, buffer);
  
  mqtt.publish("iot/fleet/register", (const uint8_t*)buffer, n, false);
  Serial.printf("Registered: %s\n", buffer);
}

// ===== HEARTBEAT =====
void sendHeartbeat() {
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
  
  mqtt.publish(topic.c_str(), (const uint8_t*)buffer, n, false);
  Serial.printf("OTA status: %s\n", status);
}

// ===== REAL OTA UPDATE (ESP8266 FLASH) =====
void startOsUpdate() {} // Placeholder if needed

void startOtaUpdate() {
  reportOtaStatus("downloading");
  mqtt.loop();

  HTTPClient http;
  http.setTimeout(30000);
  
  // ESP8266 requires passing the WiFiClient instance directly into begin()
  http.begin(wifiClient, otaFirmwareUrl);
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

  if (written != (size_t)contentLength) {
    Serial.printf("OTA: wrote %u of %d bytes\n", (unsigned int)written, contentLength);
    reportOtaStatus("failed", "Partial write");
    mqtt.loop();
    http.end();
    return;
  }

  if (!Update.end()) {
    Serial.printf("OTA: Update.end error\n");
    reportOtaStatus("failed", "Update end failed");
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
  mqtt.loop();

  http.end();

  Serial.println("OTA success! Rebooting in 3 seconds...");
  delay(3000);
  ESP.restart();
}

// ===== EDGE AI ENGINE (P0-E, threshold verdicts) =====
float edgeSlope(float* w, int n) {
  if (n < 2) return 0.0f;
  float sx = 0, sy = 0, sxx = 0, sxy = 0;
  for (int i = 0; i < n; i++) { sx += i; sy += w[i]; sxx += i * i; sxy += i * w[i]; }
  float den = n * sxx - sx * sx;
  return den == 0 ? 0.0f : (n * sxy - sx * sy) / den;
}

void edgePush(float* w, int* idx, int* n, float v) {
  w[*idx] = v;
  *idx = (*idx + 1) % EDGE_WIN;
  if (*n < EDGE_WIN) (*n)++;
}

void edgeReadSensors(float* tempC, float* sigDbm, float* shockG) {
#if HAS_SENSORS
  *tempC = 25.0f; *sigDbm = (float)WiFi.RSSI(); *shockG = 0.2f;
#else
  static float t = 25.0f;
  t += ((float)random(-5, 6)) / 10.0f;
  t = constrain(t, 15.0f, 45.0f);
  *tempC = t;
  *sigDbm = (float)constrain(WiFi.RSSI(), -100, -30);
  *shockG = ((float)random(10, 80)) / 100.0f;
#endif
  cargoTemp += ((float)random(-3, 4)) / 10.0f;
  cargoTemp = constrain(cargoTemp, -5.0f, 15.0f);
  if (random(1000) < 20) cargoDoor = !cargoDoor;
  lastShockG = *shockG;
}

void edgeScoreAndPublish() {
  float temp, sig, shock;
  edgeReadSensors(&temp, &sig, &shock);
  edgePush(edgeTempWin, &edgeTempIdx, &edgeTempN, temp);
  edgePush(edgeSigWin, &edgeSigIdx, &edgeSigN, sig);

  float tslope = edgeSlope(edgeTempWin, edgeTempN);
  float sslope = edgeSlope(edgeSigWin, edgeSigN);
  
  const char* riskType = nullptr;
  float risk = 0.0f;
  
  if (tslope > EDGE_TEMP_SLOPE && temp > EDGE_TEMP_MAX - 10.0f)      { riskType = "thermal"; risk = 0.8f; }
  else if (temp > EDGE_TEMP_MAX)                                     { riskType = "thermal"; risk = 0.9f; }
  else if (sslope < EDGE_SIG_SLOPE && sig < -70.0f)                  { riskType = "signal_degradation"; risk = 0.7f; }
  else if (shock > 5.0f)                                             { riskType = "shock"; risk = 0.85f; }
  
  if (!riskType) return;

  StaticJsonDocument<256> doc;
  doc["risk_type"]     = riskType;
  doc["risk_score"]    = risk;
  doc["model_version"] = String(EDGE_MODEL_VERSION) + "+esp8266";
  doc["replayed"]      = false;
  
  char buffer[256];
  size_t n = serializeJson(doc, buffer);
  String topic = "iot/fleet/" + deviceId + "/edge";
  mqtt.publish(topic.c_str(), (const uint8_t*)buffer, n, false);
  Serial.printf("Edge verdict: %s %.2f\n", riskType, risk);
}

void publishCargoFrame() {
  float tts = max(0.0f, (4.0f - cargoTemp) * 45.0f);
  const char* level = (tts < 60) ? "HIGH" : ((tts < 180) ? "MEDIUM" : "LOW");

  StaticJsonDocument<384> doc;
  doc["source"] = "esp8266";
  JsonObject sensors = doc.createNestedObject("sensors");
  sensors["temperature_celsius"] = cargoTemp;
  sensors["humidity_pct"]        = cargoHum;
  sensors["door_open"]           = cargoDoor;
  doc["shock_g"] = lastShockG;
  
  JsonObject ai = doc.createNestedObject("ai_inference");
  ai["spoilage_risk_level"]   = level;
  ai["predicted_tts_minutes"] = tts;
  ai["last_impact_event"]     = lastShockG > 5 ? "HARD_DROP" : "NORMAL_ROAD_BUMP";
  
  char buffer[384];
  size_t n = serializeJson(doc, buffer);
  String topic = "iot/fleet/" + deviceId + "/cargo";
  mqtt.publish(topic.c_str(), (const uint8_t*)buffer, n, false);
}

// ===== SETUP =====
void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("\n\nFleet Commander ESP8266 Client - via OTA update");

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWiFi connected: %s\n", WiFi.localIP().toString().c_str());

  deviceId = getDeviceId();
  Serial.printf("Device ID: %s\n", deviceId.c_str());
  Serial.printf("Device Name: %s\n", DEVICE_NAME);
  Serial.printf("Firmware: %s\n", FW_VERSION);

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
    edgeScoreAndPublish(); 
    publishCargoFrame();    
  }
}