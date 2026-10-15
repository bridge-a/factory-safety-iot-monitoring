// Standalone wiring/readout check. No Wi-Fi, MQTT, simulated values or alarms.
// MQ-2 AO must reach GPIO4 through a 10k/10k divider; DO is unused.
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_SHT31.h>
#include <BH1750.h>
#include <SensirionI2cScd4x.h>
#include <math.h>

#if !defined(CONFIG_IDF_TARGET_ESP32S3)
#error "Select an ESP32-S3 board; these pins and ADC settings target ESP32-S3."
#endif

constexpr int SDA_PIN = 8;
constexpr int SCL_PIN = 9;
constexpr int MQ2_PIN = 4;
constexpr uint32_t SAMPLE_MS = 2000;
constexpr uint32_t RETRY_MS = 30000;
constexpr uint32_t CO2_MAX_AGE_MS = 15000;
constexpr unsigned MQ2_SAMPLES = 32;
constexpr float DIVIDER_RATIO = 2.0f;  // 10k from AO to pin, 10k from pin to GND.

Adafruit_SHT31 sht31;
BH1750 lightMeter;
SensirionI2cScd4x scd41;
bool shtReady = false, lightReady = false, scdReady = false;
bool hasCO2 = false;
uint16_t lastCO2 = 0;
uint32_t lastCO2Ms = 0, scdStartedMs = 0;
uint32_t lastSampleMs = 0, lastRetryMs = 0;

void printScdError(const char* operation, int16_t error) {
  char message[96];
  errorToString(error, message, sizeof(message));
  Serial.printf("SCD41 [FAIL] %s: %s (code %d)\n", operation, message, error);
}

void scanI2C() {
  Serial.println("I2C scan: expected BH1750=0x23, SHT31=0x44, SCD41=0x62");
  unsigned count = 0;
  for (uint8_t address = 0x08; address < 0x78; ++address) {
    Wire.beginTransmission(address);
    const uint8_t error = Wire.endTransmission();
    if (error == 0) {
      Serial.printf("  FOUND 0x%02X\n", address);
      ++count;
    } else if (error != 2) {
      Serial.printf("  I2C error %u at 0x%02X\n", error, address);
    }
  }
  Serial.printf("I2C scan complete: %u address(es) acknowledged.\n", count);
}

bool startCO2() {
  hasCO2 = false;
  scd41.wakeUp();
  scd41.stopPeriodicMeasurement();  // The installed driver includes its 500ms wait.
  int16_t error = scd41.reinit();
  if (error) {
    printScdError("reinit", error);
    return false;
  }
  error = scd41.startPeriodicMeasurement();
  if (error) {
    printScdError("start", error);
    return false;
  }
  scdStartedMs = millis();
  return true;
}

void initMissingSensors() {
  if (!shtReady) {
    shtReady = sht31.begin(0x44);
    Serial.printf("SHT31 init: %s\n", shtReady ? "OK" : "FAIL (check 0x44)");
  }
  if (!lightReady) {
    lightReady = lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, 0x23, &Wire);
    Serial.printf("BH1750 init: %s\n", lightReady ? "OK" : "FAIL (check 0x23)");
  }
  if (!scdReady) {
    scdReady = startCO2();
    Serial.printf("SCD41 init: %s\n", scdReady ? "OK; wait for first measurement" : "FAIL (check 0x62)");
  }
}

void printTemperatureHumidity() {
  float temperature = NAN, humidity = NAN;
  if (!shtReady || !sht31.readBoth(&temperature, &humidity) ||
      !isfinite(temperature) || !isfinite(humidity) ||
      temperature < -40 || temperature > 125 || humidity < 0 || humidity > 100) {
    shtReady = false;
    Serial.println("SHT31 [FAIL] no valid temperature/humidity; check power/SDA/SCL");
    return;
  }
  Serial.printf("SHT31 [OK] temp=%.2f C, RH=%.2f %% (unscaled)\n", temperature, humidity);
}

void printLight() {
  const float lux = lightReady ? lightMeter.readLightLevel() : NAN;
  if (!isfinite(lux) || lux < 0) {
    lightReady = false;
    Serial.println("BH1750 [FAIL] no valid lux; check power/address/SDA/SCL");
    return;
  }
  Serial.printf("BH1750 [OK] light=%.1f lux\n", lux);
}

void printCO2() {
  if (!scdReady) {
    Serial.println("SCD41 [FAIL] not initialized; retry every 30s");
    return;
  }
  bool dataReady = false;
  int16_t error = scd41.getDataReadyStatus(dataReady);
  if (error) {
    hasCO2 = scdReady = false;
    printScdError("data-ready", error);
    return;
  }
  if (dataReady) {
    uint16_t co2 = 0;
    float unusedTemperature = 0, unusedHumidity = 0;
    error = scd41.readMeasurement(co2, unusedTemperature, unusedHumidity);
    if (error) {
      hasCO2 = scdReady = false;
      printScdError("read", error);
      return;
    }
    if (co2 == 0 || co2 > 40000) {
      hasCO2 = scdReady = false;
      Serial.println("SCD41 [FAIL] invalid CO2 measurement (no fallback value used)");
      return;
    }
    lastCO2 = co2;
    lastCO2Ms = millis();
    hasCO2 = true;
    Serial.printf("SCD41 [NEW] CO2=%u ppm\n", static_cast<unsigned>(lastCO2));
    return;
  }
  const uint32_t ageMs = millis() - (hasCO2 ? lastCO2Ms : scdStartedMs);
  if (ageMs >= CO2_MAX_AGE_MS) {
    hasCO2 = scdReady = false;
    Serial.println("SCD41 [STALE] no new measurement for 15s; value discarded");
  } else if (hasCO2) {
    Serial.printf("SCD41 [HOLD] CO2=%u ppm, last reading age=%.1f s\n",
                  static_cast<unsigned>(lastCO2), ageMs / 1000.0f);
  } else {
    Serial.println("SCD41 [WAIT] first measurement pending (normally about 5s)");
  }
}

void printSmokeADC() {
  uint32_t rawSum = 0, mvSum = 0;
  uint16_t rawMin = 4095, rawMax = 0;
  for (unsigned i = 0; i < MQ2_SAMPLES; ++i) {
    const uint16_t raw = analogRead(MQ2_PIN);
    rawSum += raw;
    // Calibrated mV is another conversion, not raw/4095*3300.
    mvSum += analogReadMilliVolts(MQ2_PIN);
    if (raw < rawMin) rawMin = raw;
    if (raw > rawMax) rawMax = raw;
    delay(2);
  }
  const float rawAverage = rawSum / static_cast<float>(MQ2_SAMPLES);
  const float pinMv = mvSum / static_cast<float>(MQ2_SAMPLES);
  Serial.printf("MQ2 [ADC_ONLY] raw_avg=%.1f, min=%u, max=%u, pin=%.0f mV, AO_est=%.0f mV\n",
                rawAverage, static_cast<unsigned>(rawMin), static_cast<unsigned>(rawMax),
                pinMv, pinMv * DIVIDER_RATIO);
  if (rawMax >= 4090 || pinMv >= 3000) {
    Serial.println("  CHECK: near ADC full scale; check AO divider voltage with a meter");
  } else if (rawAverage <= 5) {
    Serial.println("  CHECK: near zero; if persistent, check MQ2 5V/GND/AO and the divider");
  }
  Serial.println("  ADC_ONLY does not prove MQ2 connection/warm-up/calibration; not smoke ppm.");
}

void setup() {
  Serial.begin(115200);
  delay(1500);  // Do not wait forever for the serial monitor.
  Serial.println("\n=== Factory sensor wiring/readout check ===");
  Serial.println("I2C: SDA=GPIO8 SCL=GPIO9 | MQ2: GPIO4 after a 10k/10k divider");
  Serial.println("No Wi-Fi/MQTT. Open monitor at 115200; reset board to repeat the I2C scan.");
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);
  Wire.setTimeOut(50);
  scd41.begin(Wire, SCD41_I2C_ADDR_62);
  scd41.wakeUp();
  scanI2C();
  initMissingSensors();
  pinMode(MQ2_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(MQ2_PIN, ADC_11db);
  lastRetryMs = lastSampleMs = millis();
}

void loop() {
  if (static_cast<uint32_t>(millis() - lastSampleMs) >= SAMPLE_MS) {
    lastSampleMs = millis();
    Serial.printf("\n--- uptime=%lu s ---\n", static_cast<unsigned long>(millis() / 1000));
    printTemperatureHumidity();
    printLight();
    printCO2();
    printSmokeADC();
  }
  if (static_cast<uint32_t>(millis() - lastRetryMs) >= RETRY_MS) {
    if (!shtReady || !lightReady || !scdReady) initMissingSensors();
    lastRetryMs = millis();
  }
  delay(10);
}
