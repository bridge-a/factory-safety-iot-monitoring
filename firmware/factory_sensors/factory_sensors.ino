#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_SHT31.h>
#include <BH1750.h>
#include <SensirionI2cScd4x.h>
#include <math.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

#if __has_include("config.local.h")
#include "config.local.h"
#else
#include "config.example.h"
#endif

constexpr char ENV_TOPIC[] = "factory/workshop1/env";
constexpr uint32_t SAMPLE_MS = 2000;
constexpr uint32_t CO2_MAX_AGE_MS = 15000;
constexpr uint32_t SENSOR_RETRY_MS = 30000;
constexpr uint32_t WIFI_RETRY_MS = 10000;
constexpr uint32_t MQTT_RETRY_MS = 5000;
constexpr uint32_t PACKET_MAX_AGE_MS = 6000;

Adafruit_SHT31 sht31;
BH1750 lightMeter;
SensirionI2cScd4x scd41;
WiFiClient network;
PubSubClient mqtt(network);

struct SensorPacket {
  uint32_t createdMs;
  char json[512];
};
QueueHandle_t packets = nullptr;
bool shtReady = false;
bool lightReady = false;
bool scdReady = false;
bool hasCO2 = false;
uint16_t lastCO2 = 0;
uint32_t lastCO2Ms = 0;
uint32_t scdStartedMs = 0;
uint32_t lastSensorRetryMs = 0;
uint32_t lastSampleMs = 0;

// All sensor and I2C operations belong to the Arduino loop task.
bool startCO2() {
  hasCO2 = false;
  scd41.wakeUp();
  scd41.stopPeriodicMeasurement();  // Driver includes the required stop delay.
  if (scd41.reinit() != 0 || scd41.startPeriodicMeasurement() != 0) {
    return false;
  }
  scdStartedMs = millis();
  return true;
}

void retrySensors() {
  if (!shtReady) shtReady = sht31.begin(0x44);
  if (!lightReady) {
    lightReady = lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, 0x23, &Wire);
  }
  if (!scdReady) scdReady = startCO2();
  Serial.printf("Sensors: SHT31=%d BH1750=%d SCD41=%d\n",
                shtReady, lightReady, scdReady);
}

void updateCO2() {
  if (!scdReady) return;
  bool ready = false;
  if (scd41.getDataReadyStatus(ready) != 0) {
    hasCO2 = scdReady = false;
    return;
  }
  if (ready) {
    uint16_t value = 0;
    float unusedTemp = 0, unusedHumidity = 0;
    if (scd41.readMeasurement(value, unusedTemp, unusedHumidity) != 0 || value == 0) {
      hasCO2 = scdReady = false;
      return;
    }
    lastCO2 = value;
    lastCO2Ms = millis();
    hasCO2 = true;
  }
  const uint32_t referenceMs = hasCO2 ? lastCO2Ms : scdStartedMs;
  if (static_cast<uint32_t>(millis() - referenceMs) >= CO2_MAX_AGE_MS) {
    hasCO2 = scdReady = false;
  }
}

void jsonNumber(char* output, size_t capacity, float value, bool valid) {
  if (valid) snprintf(output, capacity, "%.2f", value);
  else snprintf(output, capacity, "null");
}

void sampleSensors() {
  float temperature = NAN, humidity = NAN, lux = NAN;
  if (shtReady && !sht31.readBoth(&temperature, &humidity)) {
    shtReady = false;
  }
  if (lightReady) {
    lux = lightMeter.readLightLevel();
    if (!isfinite(lux) || lux < 0) lightReady = false;
  }
  updateCO2();

  const uint32_t now = millis();
  const bool temperatureValid = isfinite(temperature) && temperature >= -40 && temperature <= 125;
  const bool humidityValid = isfinite(humidity) && humidity >= 0 && humidity <= 100;
  const bool luxValid = isfinite(lux) && lux >= 0;
  const bool co2Valid = hasCO2 && lastCO2 > 0 && lastCO2 <= 40000 &&
                       static_cast<uint32_t>(now - lastCO2Ms) < CO2_MAX_AGE_MS;
  if (!temperatureValid && !humidityValid) shtReady = false;

  char tempText[24], humidityText[24], luxText[24], co2Text[24];
  jsonNumber(tempText, sizeof(tempText), temperature, temperatureValid);
  jsonNumber(humidityText, sizeof(humidityText), humidity, humidityValid);
  jsonNumber(luxText, sizeof(luxText), lux, luxValid);
  jsonNumber(co2Text, sizeof(co2Text), lastCO2, co2Valid);

  SensorPacket packet{};
  packet.createdMs = now;
  const int length = snprintf(packet.json, sizeof(packet.json),
    "{\"device\":\"esp32-s3-workshop1\",\"uptime_ms\":%lu,"
    "\"temp_c\":%s,\"temp_c_valid\":%s,\"rh\":%s,\"rh_valid\":%s,"
    "\"lux\":%s,\"co2_ppm\":%s,\"co2_valid\":%s,"
    "\"smoke_raw\":null,\"smoke_valid\":false}",
    static_cast<unsigned long>(now), tempText, temperatureValid ? "true" : "false",
    humidityText, humidityValid ? "true" : "false", luxText, co2Text,
    co2Valid ? "true" : "false");
  if (length < 0 || static_cast<size_t>(length) >= sizeof(packet.json)) {
    Serial.println("Sample encoding failed");
    return;
  }
  Serial.println(packet.json);
  // Keep only the newest sample. Reconnection must not replay an old backlog.
  if (packets) xQueueOverwrite(packets, &packet);
}

// PubSubClient connect() can block. Keep all Wi-Fi/MQTT work in a separate task
// so an unreachable broker does not stop sensor acquisition.
void networkTask(void*) {
  char clientId[40];
  const uint64_t mac = ESP.getEfuseMac();
  snprintf(clientId, sizeof(clientId), "factory-%04lx%08lx",
           static_cast<unsigned long>(mac >> 32), static_cast<unsigned long>(mac));
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t lastWifiAttemptMs = millis();
  uint32_t lastMqttAttemptMs = millis() - MQTT_RETRY_MS;

  for (;;) {
    const uint32_t now = millis();
    if (WiFi.status() != WL_CONNECTED) {
      if (mqtt.connected()) mqtt.disconnect();
      if (static_cast<uint32_t>(now - lastWifiAttemptMs) >= WIFI_RETRY_MS) {
        WiFi.reconnect();
        lastWifiAttemptMs = millis();
      }
    } else if (!mqtt.connected()) {
      if (static_cast<uint32_t>(now - lastMqttAttemptMs) >= MQTT_RETRY_MS) {
        const bool connected = MQTT_USERNAME[0]
          ? mqtt.connect(clientId, MQTT_USERNAME, MQTT_PASSWORD)
          : mqtt.connect(clientId);
        lastMqttAttemptMs = millis();
        Serial.println(connected ? "MQTT connected" : "MQTT unavailable; will retry");
      }
    } else {
      mqtt.loop();
      SensorPacket packet{};
      if (xQueueReceive(packets, &packet, 0) == pdTRUE &&
          static_cast<uint32_t>(millis() - packet.createdMs) < PACKET_MAX_AGE_MS) {
        // Non-retained samples: the backend timestamps their receipt.
        Serial.println(mqtt.publish(ENV_TOPIC, packet.json, false) ? "PUB OK" : "PUB FAILED");
      }
    }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);  // Never wait indefinitely for a serial monitor.
  Serial.println("Factory sensors: SHT31 + BH1750 + SCD41; smoke not connected");
  Wire.begin(SDA_GPIO, SCL_GPIO);
  Wire.setClock(100000);
  Wire.setTimeOut(50);
  scd41.begin(Wire, SCD41_I2C_ADDR_62);
  retrySensors();
  lastSensorRetryMs = millis();
  lastSampleMs = millis();  // Let BH1750 complete its first measurement.

  if (!WIFI_SSID[0] || !MQTT_HOST[0]) {
    Serial.println("Network disabled: configure config.local.h; serial samples remain available");
    return;
  }
  network.setConnectionTimeout(2000);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setSocketTimeout(2);
  mqtt.setKeepAlive(15);
  if (!mqtt.setBufferSize(768)) {
    Serial.println("MQTT buffer allocation failed");
    return;
  }
  packets = xQueueCreate(1, sizeof(SensorPacket));
  if (!packets) {
    Serial.println("Sample queue allocation failed");
    return;
  }
  if (xTaskCreate(networkTask, "factory-mqtt", 8192, nullptr, 1, nullptr) != pdPASS) {
    Serial.println("MQTT task creation failed");
    vQueueDelete(packets);
    packets = nullptr;
  }
}

void loop() {
  const uint32_t now = millis();
  if (static_cast<uint32_t>(now - lastSampleMs) >= SAMPLE_MS) {
    lastSampleMs = now;
    sampleSensors();
  }
  if (static_cast<uint32_t>(now - lastSensorRetryMs) >= SENSOR_RETRY_MS) {
    lastSensorRetryMs = now;
    if (!shtReady || !lightReady || !scdReady) retrySensors();
  }
  delay(10);
}
