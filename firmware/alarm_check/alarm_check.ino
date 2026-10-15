#include <Arduino.h>

#if !defined(CONFIG_IDF_TARGET_ESP32S3)
#error "Select an ESP32-S3 board: GPIO6 is not suitable for this test on classic ESP32."
#endif

// Standalone timing test; no sensors, Wi-Fi, MQTT or alarm thresholds.
// GPIO5 -> LED series resistor -> LED anode; LED cathode -> GND.
// Active-high buzzer control on GPIO6; never connect a 5V load back to GPIO6.
// This test does not verify that a bare buzzer's current is safe for direct GPIO drive.
constexpr uint8_t LED_PIN = 5;
constexpr uint8_t BUZZER_PIN = 6;
constexpr uint32_t PHASE_MS = 2000;

void setOutputs(bool enabled) {
  digitalWrite(LED_PIN, enabled ? HIGH : LOW);
  digitalWrite(BUZZER_PIN, enabled ? HIGH : LOW);
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  setOutputs(false);
  Serial.begin(115200);
}

void loop() {
  setOutputs(true);
  Serial.println("ON: LED + buzzer, 2 seconds");
  delay(PHASE_MS);

  setOutputs(false);
  Serial.println("OFF: LED + buzzer, 2 seconds");
  delay(PHASE_MS);
}
