#pragma once

#include <stddef.h>
#include <stdint.h>

// MAC prefix for XRFD devices. Keeps the first three bytes of the old
// hardcoded MAC (02:F0:ED:CA:FE:01) unchanged, so a switch's MAC table lets
// you spot "this is an XRFD converter" at a glance on the show LAN.
// 0x02 = locally administered + unicast.
static const uint8_t XRFD_MAC_PREFIX[3] = {0x02, 0xF0, 0xED};

// Derives a per-board MAC from the MCU's factory unique ID.
//
// Why hash instead of slicing bytes: RA4M1 boards purchased around the same
// time tend to share leading bytes in their unique ID, so a raw slice could
// collide precisely between boards bought together. FNV-1a mixes all 16
// input bytes into every output bit.
//
// out[0..2] = XRFD_MAC_PREFIX, out[3..5] = low 24 bits of the hash.
inline void deriveMacFromUid(const uint8_t *uid, size_t uidLen, uint8_t out[6]) {
  uint32_t h = 2166136261UL; // FNV-1a 32-bit offset basis
  for (size_t i = 0; i < uidLen; i++) {
    h ^= (uint32_t)uid[i];
    h *= 16777619UL; // FNV-1a 32-bit prime
  }
  out[0] = XRFD_MAC_PREFIX[0];
  out[1] = XRFD_MAC_PREFIX[1];
  out[2] = XRFD_MAC_PREFIX[2];
  out[3] = (uint8_t)((h >> 16) & 0xFF);
  out[4] = (uint8_t)((h >> 8) & 0xFF);
  out[5] = (uint8_t)(h & 0xFF);
}
