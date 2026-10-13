#pragma once

// Copy to config.local.h and edit locally. Empty defaults disable networking.
// config.local.h is ignored by Git; never put credentials in this example.
constexpr char WIFI_SSID[] = "";
constexpr char WIFI_PASSWORD[] = "";
constexpr char MQTT_HOST[] = "";  // Raspberry Pi LAN IPv4 address
constexpr uint16_t MQTT_PORT = 1883;
constexpr char MQTT_USERNAME[] = "";  // Leave empty for an anonymous LAN broker.
constexpr char MQTT_PASSWORD[] = "";

// Existing museum sketch wiring; confirm against the physical board before upload.
constexpr int SDA_GPIO = 8;
constexpr int SCL_GPIO = 9;
