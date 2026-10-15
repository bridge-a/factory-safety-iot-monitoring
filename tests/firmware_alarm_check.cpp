// Host-side checks for the exact alarm controller used by the ESP32 sketch.
#include "../firmware/factory_sensors/alarm_logic.h"
#include <assert.h>
#include <stdio.h>
#include <string>
using namespace factory_alarm;

int main() {
  Channel smoke;
  smoke.update(1199, true, RULES[3], 0); assert(smoke.level == NORMAL);
  smoke.update(1200, true, RULES[3], 2000); assert(smoke.level == WARNING);
  smoke.update(2000, true, RULES[3], 4000); assert(smoke.level == ALARM);
  for (uint32_t t = 6000; t <= 20000; t += 2000) smoke.update(200, true, RULES[3], t);
  assert(smoke.level == ALARM);
  smoke.update(200, true, RULES[3], 22000); assert(smoke.level == NORMAL);

  Channel co2;
  co2.update(1700, true, RULES[2], 0);
  co2.update(600, true, RULES[2], 2000);
  co2.update(NAN, false, RULES[2], 4000);
  co2.update(600, true, RULES[2], 50000); assert(co2.level == ALARM);
  for (uint32_t t = 52000; t <= 66000; t += 2000) co2.update(600, true, RULES[2], t);
  assert(co2.level == NORMAL);
  co2.update(1500, true, RULES[2], 68000);
  co2.update(700, true, RULES[2], 70000);
  co2.update(700, true, RULES[2], 90000); assert(co2.level == ALARM && co2.since == 90000);

  Channel wrap;
  const uint32_t start = UINT32_MAX - 8000;
  wrap.update(45, true, RULES[0], start);
  for (uint32_t dt = 2000; dt <= 16000; dt += 2000) wrap.update(25, true, RULES[0], start + dt);
  assert(wrap.level == ALARM);
  wrap.update(25, true, RULES[0], start + 18000); assert(wrap.level == NORMAL);

  Remote remote;
  remote.received = true; remote.at = UINT32_MAX - 5000; remote.level = ALARM;
  assert(remote.active(remote.at + 9999)); assert(!remote.active(remote.at + 10000));
  assert(maximum(ALARM, NORMAL) == ALARM);  // Remote green cannot silence local red.
  const char* token = "1234567890abcdef-00000001";
  Level parsed = NORMAL;
  for (const char* word : {"red", "yellow", "green"}) {
    const std::string payload = std::string(token) + "|" + word;
    assert(parseCommand(reinterpret_cast<const uint8_t*>(payload.data()), payload.size(), token, 100, 200, parsed));
    assert(!parseCommand(reinterpret_cast<const uint8_t*>(payload.data()), payload.size(), token, 100, 6100, parsed));
    assert(!parseCommand(reinterpret_cast<const uint8_t*>(payload.data()), payload.size(), "", 100, 200, parsed));
    assert(!parseCommand(reinterpret_cast<const uint8_t*>(payload.data()), payload.size(), "1234567890abcdef-00000002", 100, 200, parsed));
  }
  for (const std::string& bad : {std::string("red"), std::string(token) + "|yellowX", std::string(token) + "|off"}) {
    assert(!parseCommand(reinterpret_cast<const uint8_t*>(bad.data()), bad.size(), token, 100, 200, parsed));
  }
  assert(outputs(ALARM, false, 0).buzzer && !outputs(ALARM, false, 500).buzzer);
  assert(outputs(WARNING, false, 199).buzzer && !outputs(WARNING, false, 200).buzzer);
  assert(outputs(WARNING, false, 900).led && !outputs(WARNING, false, 1000).led);
  assert(!outputs(NORMAL, false, 0).led && !outputs(NORMAL, false, 0).buzzer);
  assert(outputs(NORMAL, true, 0).led && !outputs(NORMAL, true, 0).buzzer);
  puts("PASS: threshold boundaries, recovery, missing/gap handling, timer wrap, command validation/expiry, output patterns");
}
