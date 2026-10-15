// 单文件完整固件：在下面填写 Wi-Fi 和树莓派 IP，然后编译上传。
// 填入真实密码的版本只保存在 firmware/local/，不要上传到公开仓库。
// 需要原有传感器库；无需 config.local.h 或 alarm_logic.h 配套文件。
#include <Arduino.h>

// ===== 网络配置与 I2C 引脚 =====
constexpr char WIFI_SSID[] = "";
constexpr char WIFI_PASSWORD[] = "";
constexpr char MQTT_HOST[] = "";  // Raspberry Pi LAN IPv4 address
constexpr uint16_t MQTT_PORT = 1883;
constexpr char MQTT_USERNAME[] = "";  // Leave empty for an anonymous LAN broker.
constexpr char MQTT_PASSWORD[] = "";
constexpr int SDA_GPIO = 8;
constexpr int SCL_GPIO = 9;
// ===== 以下为采集和告警程序 =====

#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_SHT31.h>
#include <BH1750.h>
#include <SensirionI2cScd4x.h>
#include <SensirionCore.h>
#include <esp_system.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <string.h>

namespace factory_alarm {
enum Level : uint8_t { NORMAL = 0, WARNING = 1, ALARM = 2 };
constexpr uint32_t RECOVERY_MS = 15000;
constexpr uint32_t MAX_SAMPLE_GAP_MS = 6000;
constexpr uint32_t REMOTE_TTL_MS = 10000;
constexpr size_t TOKEN_LENGTH = 25;  // boot random (16 hex), '-', sample number (8 hex)
struct Rule { float minimum, maximum, yellow, red; };
// Keep aligned with alert_rule.py. Demonstration thresholds, not industrial limits.
constexpr Rule RULES[] = {{-40, 125, 35, 45}, {0, 100, 75, 85},
                          {0, 40000, 1000, 1500}, {0, 4095, 1200, 2000}};
inline const char* name(Level level);
inline Level maximum(Level a, Level b);
inline const char* name(Level level) {
  return level == ALARM ? "red" : level == WARNING ? "yellow" : "green";
}
inline Level maximum(Level a, Level b) { return a > b ? a : b; }
struct Channel {
  Level level = NORMAL, candidate = NORMAL;
  bool pending = false, valid = false, seen = false;
  uint32_t since = 0, last = 0;
  void update(float value, bool suppliedValid, const Rule& rule, uint32_t now) {
    valid = suppliedValid && isfinite(value) && value >= rule.minimum && value <= rule.maximum;
    if (!valid) { pending = false; return; }
    const Level next = value >= rule.red ? ALARM : value >= rule.yellow ? WARNING : NORMAL;
    if (next >= level) { level = next; pending = false; }
    else if (!pending || candidate != next || !seen || uint32_t(now - last) > MAX_SAMPLE_GAP_MS) {
      candidate = next; since = now; pending = true;
    } else if (uint32_t(now - since) >= RECOVERY_MS) {
      level = next; pending = false;
    }
    seen = true; last = now;
  }
};
struct Remote {
  Level level = NORMAL;
  bool received = false;
  uint32_t at = 0;
  char token[TOKEN_LENGTH + 1] = {};
  bool active(uint32_t now) const { return received && uint32_t(now - at) < REMOTE_TTL_MS; }
};
// Echo the latest published fresh token to reject old retained/replayed commands.
inline bool parseCommand(const uint8_t* payload, size_t length, const char* expected,
                         uint32_t sampleAt, uint32_t now, Level& level);
inline bool parseCommand(const uint8_t* payload, size_t length, const char* expected,
                         uint32_t sampleAt, uint32_t now, Level& level) {
  if (strlen(expected) != TOKEN_LENGTH || uint32_t(now - sampleAt) >= MAX_SAMPLE_GAP_MS ||
      length < TOKEN_LENGTH + 4 || length > TOKEN_LENGTH + 7 ||
      memcmp(payload, expected, TOKEN_LENGTH) != 0 || payload[TOKEN_LENGTH] != '|') return false;
  const uint8_t* value = payload + TOKEN_LENGTH + 1;
  const size_t count = length - TOKEN_LENGTH - 1;
  if (count == 3 && memcmp(value, "red", 3) == 0) level = ALARM;
  else if (count == 6 && memcmp(value, "yellow", 6) == 0) level = WARNING;
  else if (count == 5 && memcmp(value, "green", 5) == 0) level = NORMAL;
  else return false;
  return true;
}
struct Outputs { bool led, buzzer; };
inline Outputs outputs(Level level, bool missing, uint32_t elapsed);
inline Outputs outputs(Level level, bool missing, uint32_t elapsed) {
  if (level == ALARM) return {elapsed % 1000 < 500, elapsed % 1000 < 500};
  if (level == WARNING) return {elapsed % 2000 < 1000, elapsed % 4000 < 200};
  return {missing && elapsed % 2000 < 100, false};
}
}  // namespace factory_alarm

#if !defined(CONFIG_IDF_TARGET_ESP32S3)
#error "Select the ESP32-S3 board used for the sensor and alarm tests."
#endif


using namespace factory_alarm;
constexpr char ENV_TOPIC[] = "factory/workshop1/env";
constexpr char CONTROL_TOPIC[] = "factory/workshop1/control/alarm";
constexpr int MQ2_PIN = 4, LED_PIN = 5, BUZZER_PIN = 6;
constexpr uint32_t SAMPLE_MS = 2000, CO2_MAX_AGE_MS = 15000;
constexpr uint32_t SENSOR_RETRY_MS = 30000, WIFI_RETRY_MS = 10000, MQTT_RETRY_MS = 5000;
constexpr unsigned MQ2_SAMPLES = 32;
static_assert(SDA_GPIO != MQ2_PIN && SDA_GPIO != LED_PIN && SDA_GPIO != BUZZER_PIN &&
              SCL_GPIO != MQ2_PIN && SCL_GPIO != LED_PIN && SCL_GPIO != BUZZER_PIN && SDA_GPIO != SCL_GPIO,
              "I2C pins must not overlap GPIO4/5/6.");

Adafruit_SHT31 sht31;
BH1750 lightMeter;
SensirionI2cScd4x scd41;
WiFiClient network;
PubSubClient mqtt(network);
struct SensorPacket {
  uint32_t createdMs;
  char token[TOKEN_LENGTH + 1];
  char json[1024];
};
struct RemoteMessage { Level level; uint32_t receivedMs; char token[TOKEN_LENGTH + 1]; };
QueueHandle_t packets = nullptr, commands = nullptr;
SensorPacket lastPublished{};  // Owned by the network task, like the MQTT client.
Channel channels[4];
Remote remoteAlarm;
Level currentLevel = NORMAL;
bool missingReadings = true, previousMissing = true, ledOn = false, buzzerOn = false;
uint32_t patternStartedMs = 0;
bool shtReady = false, lightReady = false, scdReady = false, hasCO2 = false;
uint16_t lastCO2 = 0;
uint32_t lastCO2Ms = 0, scdStartedMs = 0, lastSensorRetryMs = 0, lastSampleMs = 0;
uint32_t bootRandom1 = 0, bootRandom2 = 0, sequence = 0;

Level localLevel();
void serviceAlarm();
void scdError(const char* operation, int16_t code);
bool startCO2();
void retrySensors();
void updateCO2();
void jsonNumber(char* output, size_t capacity, float value, bool valid);
void sampleSensors();
void onCommand(char* topic, uint8_t* payload, unsigned int length);
void networkTask(void*);
void setup();
void loop();

Level localLevel() {
  Level level = NORMAL;
  for (const auto& channel : channels) level = maximum(level, channel.level);
  return level;
}

void serviceAlarm() {
  const uint32_t now = millis();
  RemoteMessage message{};
  if (commands && xQueueReceive(commands, &message, 0) == pdTRUE &&
      uint32_t(now - message.receivedMs) < REMOTE_TTL_MS) {
    remoteAlarm.received = true; remoteAlarm.at = message.receivedMs; remoteAlarm.level = message.level;
    memcpy(remoteAlarm.token, message.token, sizeof(remoteAlarm.token));
  }
  const Level next = maximum(localLevel(), remoteAlarm.active(now) ? remoteAlarm.level : NORMAL);
  if (next != currentLevel || missingReadings != previousMissing) {
    currentLevel = next; previousMissing = missingReadings; patternStartedMs = now;
    Serial.printf("ALARM: %s; local=%s; remote=%s; missing=%d\n", name(currentLevel),
                  name(localLevel()), remoteAlarm.active(now) ? name(remoteAlarm.level) : "inactive", missingReadings);
  }
  const Outputs output = outputs(currentLevel, missingReadings, uint32_t(now - patternStartedMs));
  ledOn = output.led; buzzerOn = output.buzzer;
  digitalWrite(LED_PIN, ledOn ? HIGH : LOW);
  digitalWrite(BUZZER_PIN, buzzerOn ? HIGH : LOW);
}

void scdError(const char* operation, int16_t code) {
  char error[96]; errorToString(code, error, sizeof(error));
  Serial.printf("SCD41 %s failed: %s\n", operation, error);
}
bool startCO2() {
  hasCO2 = false; lastCO2Ms = 0;
  scd41.wakeUp(); scd41.stopPeriodicMeasurement();
  int16_t error = scd41.reinit();
  if (error) { scdError("reinit", error); return false; }
  error = scd41.startPeriodicMeasurement();
  if (error) { scdError("start", error); return false; }
  scdStartedMs = millis();
  return true;
}
void retrySensors() {
  if (!shtReady) shtReady = sht31.begin(0x44);
  if (!lightReady) lightReady = lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, 0x23, &Wire);
  if (!scdReady) scdReady = startCO2();
  Serial.printf("Sensors: SHT31=%d BH1750=%d SCD41=%d\n", shtReady, lightReady, scdReady);
}
void updateCO2() {
  if (!scdReady) return;
  bool ready = false;
  int16_t error = scd41.getDataReadyStatus(ready);
  if (error) { scdError("data-ready", error); hasCO2 = scdReady = false; return; }
  if (ready) {
    uint16_t value = 0; float unusedTemp = 0, unusedHumidity = 0;
    error = scd41.readMeasurement(value, unusedTemp, unusedHumidity);
    if (error) { scdError("read", error); hasCO2 = scdReady = false; return; }
    if (value > 0 && value <= 40000) {
      lastCO2 = value; lastCO2Ms = millis(); hasCO2 = true;
    } else {
      // Allow the next measurement after a zero; the 15s timeout bounds retries.
      hasCO2 = false; Serial.println("SCD41 invalid measurement; waiting for a valid value");
    }
  }
  const uint32_t referenceMs = lastCO2Ms ? lastCO2Ms : scdStartedMs;
  if (uint32_t(millis() - referenceMs) >= CO2_MAX_AGE_MS) {
    hasCO2 = scdReady = false; Serial.println("SCD41 stale; retry scheduled");
  }
}
void jsonNumber(char* output, size_t capacity, float value, bool valid) {
  if (valid) snprintf(output, capacity, "%.2f", value);
  else snprintf(output, capacity, "null");
}

void sampleSensors() {
  float temperature = NAN, humidity = NAN, lux = NAN;
  if (shtReady && !sht31.readBoth(&temperature, &humidity)) shtReady = false;
  if (lightReady) {
    lux = lightMeter.readLightLevel();
    if (!isfinite(lux) || lux < 0) lightReady = false;
  }
  updateCO2();
  uint32_t rawSum = 0; uint16_t rawMin = 4095, rawMax = 0;
  for (unsigned i = 0; i < MQ2_SAMPLES; ++i) {
    const uint16_t value = analogRead(MQ2_PIN); rawSum += value;
    if (value < rawMin) rawMin = value;
    if (value > rawMax) rawMax = value;
    serviceAlarm(); delay(2);
  }
  const float smoke = rawSum / float(MQ2_SAMPLES);
  const uint32_t now = millis();
  const bool temperatureValid = shtReady && isfinite(temperature) && temperature >= -40 && temperature <= 125;
  const bool humidityValid = shtReady && isfinite(humidity) && humidity >= 0 && humidity <= 100;
  const bool luxValid = lightReady && isfinite(lux) && lux >= 0;
  const bool co2Valid = hasCO2 && uint32_t(now - lastCO2Ms) < CO2_MAX_AGE_MS;
  // Valid = ADC sample available, NOT connection proof, warm-up or ppm calibration.
  const bool smokeValid = isfinite(smoke) && smoke >= 0 && smoke <= 4095;
  if (!temperatureValid && !humidityValid) shtReady = false;
  const float values[4] = {temperature, humidity, float(lastCO2), smoke};
  const bool valid[4] = {temperatureValid, humidityValid, co2Valid, smokeValid};
  missingReadings = false;
  for (unsigned i = 0; i < 4; ++i) {
    channels[i].update(values[i], valid[i], RULES[i], now);
    missingReadings = missingReadings || !channels[i].valid;
  }
  serviceAlarm();
  char tempText[24], humidityText[24], luxText[24], co2Text[24], smokeText[24];
  jsonNumber(tempText, sizeof(tempText), temperature, temperatureValid);
  jsonNumber(humidityText, sizeof(humidityText), humidity, humidityValid);
  jsonNumber(luxText, sizeof(luxText), lux, luxValid);
  jsonNumber(co2Text, sizeof(co2Text), lastCO2, co2Valid);
  jsonNumber(smokeText, sizeof(smokeText), smoke, smokeValid);
  SensorPacket packet{}; packet.createdMs = now;
  snprintf(packet.token, sizeof(packet.token), "%08lx%08lx-%08lx",
           static_cast<unsigned long>(bootRandom1), static_cast<unsigned long>(bootRandom2),
           static_cast<unsigned long>(++sequence));
  const bool remoteActive = remoteAlarm.active(now);
  const char* localText = localLevel() == NORMAL && missingReadings ? "gray" : name(localLevel());
  const char* effectiveText = currentLevel == NORMAL && missingReadings ? "gray" : name(currentLevel);
  const int length = snprintf(packet.json, sizeof(packet.json),
    "{\"device\":\"esp32-s3-workshop1\",\"firmware\":\"factory-integrated-v1\",\"uptime_ms\":%lu,"
    "\"temp_c\":%s,\"temp_c_valid\":%s,\"rh\":%s,\"rh_valid\":%s,\"lux\":%s,"
    "\"co2_ppm\":%s,\"co2_valid\":%s,\"smoke_raw\":%s,\"smoke_valid\":%s,"
    "\"smoke_basis\":\"adc_relative\",\"smoke_min\":%u,\"smoke_max\":%u,\"alarm_token\":\"%s\","
    "\"sound_light\":{\"enabled\":true,\"local_level\":\"%s\",\"effective_level\":\"%s\","
    "\"remote_active\":%s,\"led_on\":%s,\"buzzer_on\":%s,\"command_token\":\"%s\"}}",
    static_cast<unsigned long>(now), tempText, temperatureValid ? "true" : "false",
    humidityText, humidityValid ? "true" : "false", luxText, co2Text, co2Valid ? "true" : "false",
    smokeText, smokeValid ? "true" : "false", unsigned(rawMin), unsigned(rawMax), packet.token,
    localText, effectiveText, remoteActive ? "true" : "false", ledOn ? "true" : "false",
    buzzerOn ? "true" : "false", remoteActive ? remoteAlarm.token : "");
  if (length < 0 || size_t(length) >= sizeof(packet.json)) { Serial.println("Sample encoding failed"); return; }
  Serial.println(packet.json);
  if (rawMin == 0 || rawMax >= 4090) Serial.println("MQ2 ADC rail reading: check divider/power; not smoke ppm");
  if (packets) xQueueOverwrite(packets, &packet);
}

void onCommand(char* topic, uint8_t* payload, unsigned int length) {
  Level level; const uint32_t now = millis();
  if (!commands || strcmp(topic, CONTROL_TOPIC) != 0 ||
      !parseCommand(payload, length, lastPublished.token, lastPublished.createdMs, now, level)) return;
  RemoteMessage command{}; command.level = level; command.receivedMs = now;
  memcpy(command.token, lastPublished.token, sizeof(command.token));
  xQueueOverwrite(commands, &command);
}

// Network waits must not stop sensor sampling or local alarm processing.
void networkTask(void*) {
  char clientId[40]; const uint64_t mac = ESP.getEfuseMac();
  snprintf(clientId, sizeof(clientId), "factory-%04lx%08lx",
           static_cast<unsigned long>(mac >> 32), static_cast<unsigned long>(mac));
  WiFi.mode(WIFI_STA); WiFi.setAutoReconnect(true); WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t lastWifiAttemptMs = millis(), lastMqttAttemptMs = millis() - MQTT_RETRY_MS;
  bool wifiReported = false;
  for (;;) {
    const uint32_t now = millis();
    if (WiFi.status() != WL_CONNECTED) {
      if (wifiReported) Serial.println("Wi-Fi offline; local monitoring continues");
      wifiReported = false; lastPublished.token[0] = '\0';
      if (mqtt.connected()) mqtt.disconnect();
      if (uint32_t(now - lastWifiAttemptMs) >= WIFI_RETRY_MS) { WiFi.reconnect(); lastWifiAttemptMs = millis(); }
    } else {
      if (!wifiReported) { Serial.println("Wi-Fi connected"); wifiReported = true; }
      if (!mqtt.connected()) {
        lastPublished.token[0] = '\0';
        if (uint32_t(now - lastMqttAttemptMs) >= MQTT_RETRY_MS) {
          const bool connected = MQTT_USERNAME[0]
            ? mqtt.connect(clientId, MQTT_USERNAME, MQTT_PASSWORD) : mqtt.connect(clientId);
          lastMqttAttemptMs = millis();
          if (connected && mqtt.subscribe(CONTROL_TOPIC, 0)) Serial.println("MQTT connected; alarm control subscribed");
          else { Serial.printf("MQTT unavailable (state=%d); will retry\n", mqtt.state()); mqtt.disconnect(); }
        }
      } else {
        mqtt.loop();  // Receive replies before replacing the latest sample token.
        SensorPacket packet{};
        if (mqtt.connected() && xQueueReceive(packets, &packet, 0) == pdTRUE &&
            uint32_t(millis() - packet.createdMs) < MAX_SAMPLE_GAP_MS) {
          if (mqtt.publish(ENV_TOPIC, packet.json, false)) { lastPublished = packet; Serial.println("PUB OK"); }
          else Serial.println("PUB FAILED");
        }
      }
    }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

void setup() {
  pinMode(LED_PIN, OUTPUT); pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW); digitalWrite(BUZZER_PIN, LOW);
  Serial.begin(115200); delay(300);
  Serial.println("Factory integrated v1: SHT31 + BH1750 + SCD41 + MQ2 + LED5 + buzzer6");
  Serial.println("MQ2 is relative ADC only; demonstration thresholds are not calibrated safety limits");
  bootRandom1 = esp_random(); bootRandom2 = esp_random();
  Wire.begin(SDA_GPIO, SCL_GPIO); Wire.setClock(100000); Wire.setTimeOut(50);
  scd41.begin(Wire, SCD41_I2C_ADDR_62);
  pinMode(MQ2_PIN, INPUT); analogReadResolution(12); analogSetPinAttenuation(MQ2_PIN, ADC_11db);
  retrySensors(); lastSensorRetryMs = lastSampleMs = millis();
  if (!WIFI_SSID[0] || !MQTT_HOST[0]) {
    Serial.println("Network disabled: fill Wi-Fi and MQTT settings at the top of this sketch; local sensors and alarms remain active"); return;
  }
  network.setConnectionTimeout(2000); mqtt.setServer(MQTT_HOST, MQTT_PORT); mqtt.setCallback(onCommand);
  mqtt.setSocketTimeout(2); mqtt.setKeepAlive(15);
  if (!mqtt.setBufferSize(1280)) { Serial.println("MQTT buffer allocation failed"); return; }
  packets = xQueueCreate(1, sizeof(SensorPacket)); commands = xQueueCreate(1, sizeof(RemoteMessage));
  if (!packets || !commands) {
    if (packets) vQueueDelete(packets);
    if (commands) vQueueDelete(commands);
    packets = commands = nullptr; Serial.println("Queue allocation failed; local alarms remain active"); return;
  }
  if (xTaskCreate(networkTask, "factory-mqtt", 8192, nullptr, 1, nullptr) != pdPASS) {
    vQueueDelete(packets); vQueueDelete(commands); packets = commands = nullptr;
    Serial.println("MQTT task creation failed; local alarms remain active");
  }
}

void loop() {
  serviceAlarm(); const uint32_t now = millis();
  if (uint32_t(now - lastSampleMs) >= SAMPLE_MS) { lastSampleMs = now; sampleSensors(); }
  if (uint32_t(now - lastSensorRetryMs) >= SENSOR_RETRY_MS) {
    lastSensorRetryMs = now;
    if (!shtReady || !lightReady || !scdReady) retrySensors();
  }
  delay(5);
}
