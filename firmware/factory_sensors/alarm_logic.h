#pragma once
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
inline Outputs outputs(Level level, bool missing, uint32_t elapsed) {
  if (level == ALARM) return {elapsed % 1000 < 500, elapsed % 1000 < 500};
  if (level == WARNING) return {elapsed % 2000 < 1000, elapsed % 4000 < 200};
  return {missing && elapsed % 2000 < 100, false};
}
}  // namespace factory_alarm
