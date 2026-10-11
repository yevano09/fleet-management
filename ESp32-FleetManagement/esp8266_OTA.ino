#include <ArduinoOTA.h>
#include <ESP8266WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Updater.h>
#include <ESP8266HTTPClient.h>
#include <time.h>

// ESP8266 core has no UPDATE_SIZE_UNKNOWN (ESP32-only). Map to max size_t.
#ifndef UPDATE_SIZE_UNKNOWN
#define UPDATE_SIZE_UNKNOWN ((size_t)-1)
#endif

// ===== CONFIGURATION =====
const char* WIFI_SSID     = "XXXX";
const char* WIFI_PASSWORD = "XXXX";

const char* MQTT_BROKER   = "0.0.0.0";  // IP or hostname of Mosquitto
const int   MQTT_PORT     = 1883;
const char* MQTT_USER     = "";                 // leave empty if anonymous
const char* MQTT_PASS     = "";

// Device identity — set these per device
const char* DEVICE_NAME   = "ESP8266-Garage-001-REAL";
const char* FW_VERSION    = "1.0.0";

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
  
  StaticJsonDocument<1024> doc;
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

// ===== REAL OTA UPDATE (ESP8266 HTTP/HTTPS FLASH) =====
void startOtaUpdate() {
  Serial.printf("OTA start: url=%s\n", otaFirmwareUrl.c_str());
  reportOtaStatus("downloading");
  mqtt.loop();

  if (otaFirmwareUrl.length() == 0) {
    Serial.println("OTA: empty firmware URL");
    reportOtaStatus("failed", "Empty firmware URL");
    mqtt.loop();
    return;
  }

  // Free heap for BearSSL: disconnect MQTT during flash download.
  // (ESP-12E has ~40KB heap; MQTT + TLS simultaneously causes OOM/handshake fail.)
  mqtt.disconnect();
  delay(100);

  WiFiClient plainClient;
  WiFiClientSecure secureClient;
  secureClient.setInsecure();
  // Large buffers needed for fast servers; keeps BearSSL from stalling.
  secureClient.setBufferSizes(4096, 1024);

  HTTPClient http;
  http.setTimeout(30000);
  // Manual redirect handling below (auto-follow can't switch http<->https
  // clients, so a http->https 301 comes back as a hard "HTTP 301" failure).
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  http.setReuse(false);

  String url = otaFirmwareUrl;
  url.trim();
  int httpCode = -1;
  const int MAX_REDIRECTS = 5;
  for (int r = 0; r <= MAX_REDIRECTS; r++) {
    bool useHttps = url.startsWith("https://");
    const char* hdrKeys[] = {"Location"};
    http.collectHeaders(hdrKeys, 1);
    bool begun = useHttps ? http.begin(secureClient, url)
                          : http.begin(plainClient, url);
    if (!begun) {
      Serial.println("OTA: http.begin failed (bad URL?)");
      reportOtaStatus("failed", "http.begin failed");
      connectMqtt();
      return;
    }
    httpCode = http.GET();
    if (httpCode == HTTP_CODE_MOVED_PERMANENTLY || httpCode == HTTP_CODE_FOUND ||
        httpCode == HTTP_CODE_SEE_OTHER || httpCode == HTTP_CODE_TEMPORARY_REDIRECT ||
        httpCode == HTTP_CODE_PERMANENT_REDIRECT) {
      String loc = http.header("Location");
      Serial.printf("OTA redirect %d -> %s\n", httpCode, loc.c_str());
      http.end();
      if (loc.length() == 0 || r == MAX_REDIRECTS) break;
      loc.trim();
      if (loc.startsWith("/")) {
        int schemeEnd = url.indexOf("://");
        int pathStart = url.indexOf('/', schemeEnd + 3);
        String base = (pathStart > 0) ? url.substring(0, pathStart) : url;
        url = base + loc;
      } else if (loc.startsWith("http")) {
        url = loc;
      } else {
        url = loc; // best effort
      }
      Serial.printf("OTA follow -> %s\n", url.c_str());
      continue;
    }
    break; // got final response (200 or real error)
  }

  if (httpCode != HTTP_CODE_OK) {
    Serial.printf("OTA download failed: HTTP %d %s\n", httpCode, http.errorToString(httpCode).c_str());
    char errMsg[64];
    snprintf(errMsg, sizeof(errMsg), "HTTP error %d", httpCode);
    // Must reconnect before reporting so publish actually goes out
    http.end();
    connectMqtt();
    reportOtaStatus("failed", errMsg);
    mqtt.loop();
    return;
  }

  int contentLength = http.getSize();
  Serial.printf("OTA: contentLength=%d\n", contentLength);
  Serial.printf("OTA: freeHeap=%u freeSketch=%u\n", ESP.getFreeHeap(), ESP.getFreeSketchSpace());

  if (contentLength == 0) {
    Serial.println("OTA: invalid content length 0");
    http.end();
    connectMqtt();
    reportOtaStatus("failed", "Invalid content length");
    mqtt.loop();
    return;
  }

  // contentLength == -1 means chunked transfer (no Content-Length header).
  bool canBegin;
  if (contentLength > 0) {
    if ((size_t)contentLength > ESP.getFreeSketchSpace()) {
      Serial.printf("OTA: bin %d > freeSketch %u, check Flash Size menu (need 4M)\n",
                    contentLength, ESP.getFreeSketchSpace());
    }
    canBegin = Update.begin((size_t)contentLength, U_FLASH);
  } else {
    Serial.println("OTA: chunked/unknown length, using UPDATE_SIZE_UNKNOWN");
    canBegin = Update.begin(UPDATE_SIZE_UNKNOWN, U_FLASH);
  }
  if (!canBegin) {
    Serial.printf("OTA: Update.begin failed, error %d\n", Update.getError());
    char errMsg[64];
    snprintf(errMsg, sizeof(errMsg), "Update.begin failed err=%d", Update.getError());
    http.end();
    connectMqtt();
    reportOtaStatus("failed", errMsg);
    mqtt.loop();
    return;
  }

  WiFiClient* stream = http.getStreamPtr();
  // Manual WDT-safe loop. Update.writeStream() blocks for 420KB+ and trips
  // the ESP8266 watchdog (no Serial progress, looks hung after "downloading").
  uint8_t buff[1460];
  size_t written = 0;
  size_t total = (contentLength > 0) ? (size_t)contentLength : UPDATE_SIZE_UNKNOWN;
  unsigned long lastData = millis();
  unsigned long lastLog = millis();
  while (written < total) {
    size_t avail = stream->available();
    if (avail) {
      size_t toRead = avail > sizeof(buff) ? sizeof(buff) : avail;
      int n = stream->readBytes(buff, toRead);
      if (n <= 0) break;
      if (Update.write(buff, (size_t)n) != (size_t)n) {
        Serial.printf("OTA: flash write failed at %u, err=%d\n", (unsigned int)written, Update.getError());
        break;
      }
      written += (size_t)n;
      lastData = millis();
      if (millis() - lastLog > 2000) {
        lastLog = millis();
        if (contentLength > 0) {
          Serial.printf("OTA: %u / %d bytes (heap %u)\n", (unsigned int)written, contentLength, ESP.getFreeHeap());
        } else {
          Serial.printf("OTA: %u bytes (heap %u)\n", (unsigned int)written, ESP.getFreeHeap());
        }
      }
    } else {
      if (millis() - lastData > 20000UL) {
        Serial.println("OTA: 20s stall, aborting");
        break;
      }
      delay(1);
    }
    yield();
    ESP.wdtFeed();
    // Exit chunked stream cleanly once server closes connection
    if (contentLength < 0 && !http.connected() && stream->available() == 0) break;
  }
  Serial.printf("OTA: written %u bytes\n", (unsigned int)written);

  if (contentLength > 0 && written != (size_t)contentLength) {
    Serial.printf("OTA: wrote %u of %d bytes\n", (unsigned int)written, contentLength);
    Update.end();
    http.end();
    connectMqtt();
    reportOtaStatus("failed", "Partial write or timeout");
    mqtt.loop();
    return;
  }

  if (!Update.end()) {
    Serial.printf("OTA: Update.end error %d\n", Update.getError());
    char errMsg[64];
    snprintf(errMsg, sizeof(errMsg), "Update.end err=%d", Update.getError());
    http.end();
    connectMqtt();
    reportOtaStatus("failed", errMsg);
    mqtt.loop();
    return;
  }

  if (!Update.isFinished()) {
    Serial.println("OTA: Update not finished");
    http.end();
    connectMqtt();
    reportOtaStatus("failed", "Update not finished");
    mqtt.loop();
    return;
  }

  // Reconnect so "success" is actually delivered before reboot
  http.end();
  connectMqtt();
  reportOtaStatus("success");
  mqtt.loop();
  delay(200);

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

  // Connect WiFi
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWiFi connected: %s\n", WiFi.localIP().toString().c_str());

  // Configure NTP time sync for secure TLS handshakes
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  Serial.print("Waiting for NTP time sync");
  while (time(nullptr) < 1700000000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nTime synchronized via NTP!");

  deviceId = getDeviceId();
  Serial.printf("Device ID: %s\n", deviceId.c_str());
  Serial.printf("Device Name: %s\n", DEVICE_NAME);
  Serial.printf("Firmware: %s\n", FW_VERSION);

  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setBufferSize(1024);
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