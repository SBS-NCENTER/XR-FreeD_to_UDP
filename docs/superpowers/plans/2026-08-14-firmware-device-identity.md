# Firmware Device Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 같은 firmware를 올린 두 대 이상의 XRFD 컨버터가 같은 LAN에서 동시에 켜져도 MAC 주소와 IP 주소가 절대 충돌하지 않게 만든다.

**Architecture:** MAC은 하드코딩 상수 대신 RA4M1의 128-bit factory unique ID에서 유도해 보드마다 자동으로 달라지게 한다 (`R_BSP_UniqueIdGet()`, FSP inline). static fallback IP는 "추측한 주소"(하드코딩 `10.10.204.123`)를 후보에서 제거하고, EEPROM에 남은 지난 DHCP lease 또는 운영자가 명시 지정한 주소만 사용한다 — 둘 다 없으면 fallback하지 않고 DHCP만 재시도한다. MAC 원격 조회용 `info` 명령을 신설하되, 5초 주기 진단 broadcast(`buildStatusLine()`)는 버퍼 여유가 9B뿐이라 손대지 않는다.

**Tech Stack:** C++ (Arduino framework, PlatformIO 6.1.19), Arduino UNO R4 WiFi (Renesas RA4M1), `arduino-libraries/Ethernet@2.0.2` (W5500), Renesas FSP BSP, Unity (native 단위 테스트)

## Global Constraints

- **`lib_deps = arduino-libraries/Ethernet@2.0.2` 를 캐럿 없이 그대로 유지한다.** `src/main.cpp`가 `<utility/w5100.h>`를 통해 라이브러리 내부(async `Sock_SEND`, raw RTR/RCR 레지스터)에 의존한다 (`platformio.ini:16-19`).
- **`CONFIG_MAGIC`(`src/main.cpp:129`, 현재 `0xFD260611`)을 bump하지 않는다.** bump하면 운영 장비의 EEPROM config가 통째로 무시되어 현재 `t1/t2/t3 = off` 설정이 기본값(`t1 = on`)으로 되살아나고, 저장된 `lastDhcpIP`도 사라진다. 이 plan의 기본값 변경은 **EEPROM이 빈 새 보드에만** 적용되어야 하며, 그것이 magic을 그대로 두는 이유다.
- **`AppConfig` struct(`src/main.cpp:116-126`) 레이아웃을 바꾸지 않는다.** 필드 추가·삭제·순서 변경·타입 변경 모두 금지 — 기존 장비 EEPROM 이미지와 바이너리 호환이 깨진다. 값(기본값)만 바꾼다.
- **`buildStatusLine()`(`src/main.cpp:564`)의 출력 길이를 늘리지 않는다.** 수신 버퍼가 `char reply[256]`이고 현재 최악 길이가 ~247B다 (`src/main.cpp:670` 주석).
- **진단 broadcast 문자열 포맷을 바꾸지 않는다.** `dashboard/backend/protocol.py:5`와 `tools/xrfd_dashboard.py:38`의 정규식이 이 포맷에 의존한다.
- **FreeD 송출 hot path에 블로킹을 추가하지 않는다.** `loop()`, `processSerialData()`, `sendToTargets()` 경로에 새 지연이 생기면 안 된다. 과거 target 하나가 죽었을 때 살아있는 target의 전송률이 붕괴한 회귀가 있었고, v1.6의 per-target socket 구조가 그 수정본이다.
- **블로킹 구간에는 `WDT.refresh()`를 유지한다.** 하드웨어 워치독이 ~5.59s다 (`src/main.cpp:1613`).
- 코드 주석은 영어, 단 기존 파일의 한국어 주석 스타일을 따르는 곳은 그대로 유지한다 (`src/main.cpp`는 한국어 주석 관례).

## File Structure

| 파일 | 상태 | 책임 |
|---|---|---|
| `include/mac_from_uid.h` | **[create]** | UID 바이트열 → MAC 6바이트 유도. Arduino 의존성 0인 순수 로직이라 native 테스트 가능 |
| `test/test_mac_from_uid/test_mac_from_uid.cpp` | **[create]** | 위 헤더의 Unity 단위 테스트 |
| `platformio.ini` | [modify] | `[env:native]` 테스트 환경 + `default_envs` 추가 |
| `src/main.cpp` | [modify] | MAC 확정(`resolveMac`), fallback 정책, 무주소 재시도, `info` 명령 |
| `README.md` | [modify] | 장비 identity/fallback 동작 설명 갱신 |
| `MANUAL.md` | [modify] | 운영자용 절차(신규 장비 투입, MAC 확인) 갱신 |

`include/`와 `test/`에는 현재 PlatformIO 기본 `README` 스텁만 있다. 새 파일은 그 옆에 추가하며 스텁은 지우지 않는다.

**의존 순서:** Task 1 → Task 2 → Task 3 → Task 4 → Task 5. Task 2는 Task 1의 헤더를, Task 3은 Task 2가 만든 `g_effectiveMac`을 쓴다.

---

### Task 1: MAC 유도 순수 함수 + native 테스트 환경

**Files:**
- Create: `include/mac_from_uid.h`
- Create: `test/test_mac_from_uid/test_mac_from_uid.cpp`
- Modify: `platformio.ini`

**Interfaces:**
- Produces: `void deriveMacFromUid(const uint8_t *uid, size_t uidLen, uint8_t out[6])` — Task 2가 호출한다.
- Produces: `XRFD_MAC_PREFIX` (`static const uint8_t[3]` = `{0x02, 0xF0, 0xED}`) — Task 1의 테스트와 Task 2가 참조한다.
- Consumes: 없음 (Arduino/FSP 의존성 없는 순수 함수).

- [ ] **Step 1: platformio.ini에 native 테스트 환경을 추가한다**

기존 `[env:uno_r4_wifi]` 블록은 그대로 두고, 파일 맨 위에 `[platformio]` 섹션을, 맨 아래에 `[env:native]`를 추가한다. `default_envs`가 없으면 `pio run`이 native 환경까지 보드 빌드하려 들어 실패한다.

파일 최상단(첫 주석 블록 바로 뒤, `[env:uno_r4_wifi]` 앞)에 추가:

```ini
[platformio]
; `pio run` / `pio run -t upload`이 보드 환경만 대상으로 하게 한다.
; native 단위 테스트는 `pio test -e native`로 명시 실행.
default_envs = uno_r4_wifi
```

파일 맨 끝에 추가:

```ini
; 호스트에서 도는 순수 로직 단위 테스트 전용 환경 (보드 불필요).
; include/ 의 Arduino 비의존 헤더만 대상으로 한다.
[env:native]
platform = native
test_framework = unity
build_flags = -I include
```

- [ ] **Step 2: 실패하는 테스트를 작성한다**

`test/test_mac_from_uid/test_mac_from_uid.cpp`:

```cpp
#include <string.h>
#include <unity.h>

#include "mac_from_uid.h"

// RA4M1 unique ID는 16바이트다. 같은 wafer/lot에서 산 보드들은 앞쪽
// 바이트가 겹치는 경향이 있어, 테스트 벡터도 뒤쪽 1바이트만 다른
// "최악에 가까운" 쌍을 쓴다.
static const uint8_t UID_A[16] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88,
                                  0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00};
static const uint8_t UID_B[16] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88,
                                  0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x01};

// Unity requires both symbols even when unused — omitting them fails at link.
void setUp(void) {}
void tearDown(void) {}

void test_prefix_is_fixed(void) {
  uint8_t mac[6];
  deriveMacFromUid(UID_A, sizeof(UID_A), mac);
  TEST_ASSERT_EQUAL_UINT8(0x02, mac[0]);
  TEST_ASSERT_EQUAL_UINT8(0xF0, mac[1]);
  TEST_ASSERT_EQUAL_UINT8(0xED, mac[2]);
}

void test_is_locally_administered_unicast(void) {
  // bit1 = locally administered(1), bit0 = multicast(0).
  // 이게 깨지면 switch가 프레임을 multicast로 취급해 조용히 오동작한다.
  uint8_t mac[6];
  deriveMacFromUid(UID_A, sizeof(UID_A), mac);
  TEST_ASSERT_EQUAL_UINT8(0x02, mac[0] & 0x03);
}

void test_is_deterministic(void) {
  uint8_t first[6], second[6];
  deriveMacFromUid(UID_A, sizeof(UID_A), first);
  deriveMacFromUid(UID_A, sizeof(UID_A), second);
  TEST_ASSERT_EQUAL_UINT8_ARRAY(first, second, 6);
}

void test_one_byte_uid_difference_changes_mac(void) {
  uint8_t a[6], b[6];
  deriveMacFromUid(UID_A, sizeof(UID_A), a);
  deriveMacFromUid(UID_B, sizeof(UID_B), b);
  TEST_ASSERT_FALSE(memcmp(a, b, 6) == 0);
}

void test_leading_byte_difference_changes_mac(void) {
  uint8_t flipped[16];
  memcpy(flipped, UID_A, sizeof(UID_A));
  flipped[0] ^= 0x01;
  uint8_t a[6], b[6];
  deriveMacFromUid(UID_A, sizeof(UID_A), a);
  deriveMacFromUid(flipped, sizeof(flipped), b);
  TEST_ASSERT_FALSE(memcmp(a, b, 6) == 0);
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_prefix_is_fixed);
  RUN_TEST(test_is_locally_administered_unicast);
  RUN_TEST(test_is_deterministic);
  RUN_TEST(test_one_byte_uid_difference_changes_mac);
  RUN_TEST(test_leading_byte_difference_changes_mac);
  return UNITY_END();
}
```

- [ ] **Step 3: 테스트가 실패하는지 확인한다**

Run: `pio test -e native`
Expected: 컴파일 실패 — `fatal error: mac_from_uid.h: No such file or directory`

- [ ] **Step 4: 헤더를 구현한다**

`include/mac_from_uid.h`:

```cpp
#pragma once

#include <stddef.h>
#include <stdint.h>

// XRFD 장비의 MAC prefix. 과거 하드코딩 MAC(02:F0:ED:CA:FE:01)의 앞 3바이트를
// 그대로 유지해서, show LAN의 MAC table에서 "이건 XRFD 컨버터"라고 눈으로
// 알아볼 수 있게 한다. 0x02 = locally administered + unicast.
static const uint8_t XRFD_MAC_PREFIX[3] = {0x02, 0xF0, 0xED};

// MCU의 factory unique ID에서 보드별로 고유한 MAC을 유도한다.
//
// 바이트를 잘라 쓰지 않고 해시하는 이유: 같은 시기에 구매한 RA4M1 보드들은
// unique ID의 앞쪽 바이트가 겹치는 경향이 있어, raw slice는 하필 함께 산
// 보드끼리 충돌할 수 있다. FNV-1a는 입력 16바이트 전부를 출력 전 비트에
// 섞어 넣는다.
//
// out[0..2] = XRFD_MAC_PREFIX, out[3..5] = 해시 하위 24비트.
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
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `pio test -e native`
Expected: `5 Tests 0 Failures 0 Ignored` / `PASSED`

- [ ] **Step 6: 보드 빌드가 깨지지 않았는지 확인한다**

Run: `pio run`
Expected: `[SUCCESS]`, 그리고 로그에 `Building in release mode` 다음 `Environment  uno_r4_wifi`만 나타난다 (native 환경은 빌드되지 않는다).

- [ ] **Step 7: Commit**

변경 파일: `include/mac_from_uid.h`, `test/test_mac_from_uid/test_mac_from_uid.cpp`, `platformio.ini`

커밋 메시지 (사용자가 nvim에서 직접 입력):

```
feat: derive per-board MAC from MCU unique ID

같은 firmware를 올린 보드들이 하드코딩 MAC을 공유해 같은 LAN에서
충돌하는 문제의 기반 작업. FNV-1a로 RA4M1 unique ID 16바이트를 섞어
MAC 하위 3바이트를 만든다. 앞 3바이트(02:F0:ED)는 기존 값을 유지.

호스트 단위 테스트를 위해 [env:native] 추가. default_envs로 `pio run`이
보드 환경만 빌드하도록 고정.
```

---

### Task 2: 부팅 시 MAC 확정 (resolveMac)

**Files:**
- Modify: `src/main.cpp` (기본값 `src/main.cpp:135`, 전역 선언부, `setup()` `src/main.cpp:1603`, `Ethernet.begin` 3곳, `printStatus()`)

**Interfaces:**
- Consumes: `deriveMacFromUid()` (Task 1)
- Produces: `uint8_t g_effectiveMac[6]` — Task 3과 Task 4가 참조한다. 이 배열이 wire로 나가는 MAC의 단일 출처이며, `g_config.mac`을 직접 읽는 코드는 이 task 이후 남아 있으면 안 된다.
- Produces: `void resolveMac()` — `loadConfig()` 직후 한 번만 호출한다.

- [ ] **Step 1: 헤더를 include한다**

`src/main.cpp`의 `#include <EEPROM.h>` (`src/main.cpp:40`) 바로 아래에 추가:

```cpp
#include "mac_from_uid.h"
```

`R_BSP_UniqueIdGet()`과 `bsp_unique_id_t`는 별도 include가 필요 없다 — `Arduino.h`가 `bsp_api.h`를 포함한다 (framework-arduinorenesas-uno `cores/arduino/Arduino.h:5`). 컴파일 프로브로 확인된 사실이다.

- [ ] **Step 2: 기본 MAC을 "미설정" 마커로 바꾼다**

`src/main.cpp:135`를 찾는다:

```cpp
    {0x02, 0xF0, 0xED, 0xCA, 0xFE, 0x01}, // MAC - 고유값 (Locally Administered)
```

다음으로 교체:

```cpp
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00}, // MAC - all-zero = 미설정: MCU unique
                                          // ID에서 유도한다 (resolveMac 참고).
                                          // `set mac`으로 명시 지정한 경우에만
                                          // EEPROM 값이 이 유도를 덮어쓴다.
```

**CONFIG_MAGIC은 건드리지 않는다.** 그래야 이 새 기본값이 EEPROM이 빈 새 보드에만 적용되고, 운영 중인 장비의 저장된 설정(현재 MAC 포함)은 업데이트 후에도 그대로 유지된다.

- [ ] **Step 3: g_effectiveMac 전역과 resolveMac()을 추가한다**

`loadConfig()` / `saveConfig()` 정의 블록(`src/main.cpp:392-410`) 바로 아래에 추가:

```cpp
// 실제로 wire에 나가는 MAC. loadConfig() 직후 딱 한 번 확정한다.
//
// g_config.mac이 all-zero(미설정)면 MCU의 factory unique ID에서 유도한다 —
// 같은 firmware를 여러 보드에 올려도 MAC이 자동으로 달라지므로, 장비 교체
// 중 신·구 장비가 잠시 같이 켜져 있어도 MAC이 겹치지 않는다. unique ID는
// 불변이라 firmware를 다시 올려도 같은 MAC이 나온다.
//
// EEPROM에 명시 MAC이 있으면(과거 장비, 혹은 `set mac`) 그쪽이 이긴다.
uint8_t g_effectiveMac[6];

void resolveMac() {
  static const uint8_t kZero[6] = {0, 0, 0, 0, 0, 0};
  if (memcmp(g_config.mac, kZero, 6) != 0) {
    memcpy(g_effectiveMac, g_config.mac, 6);
    return;
  }
  const bsp_unique_id_t *uid = R_BSP_UniqueIdGet();
  deriveMacFromUid(uid->unique_id_bytes, sizeof(uid->unique_id_bytes),
                   g_effectiveMac);
}
```

- [ ] **Step 4: setup()에서 resolveMac()을 호출한다**

`src/main.cpp:1603`의 `loadConfig();` 아래 줄에 추가한다. `initNetwork()`(`src/main.cpp:1608`)보다 반드시 먼저 와야 한다:

```cpp
  loadConfig();
  resolveMac(); // g_effectiveMac 확정 — initNetwork()보다 먼저여야 한다
```

- [ ] **Step 5: Ethernet.begin() 3곳을 g_effectiveMac으로 바꾼다**

세 군데 모두 `g_config.mac` → `g_effectiveMac`으로 교체한다. 다른 인자는 그대로 둔다.

1. `applyStaticFallback()` 안 (`src/main.cpp:777`):
```cpp
  Ethernet.begin(g_effectiveMac, ip, gateway, gateway, subnet);
```

2. `initNetwork()` 안 (`src/main.cpp:818`):
```cpp
    if (Ethernet.begin(g_effectiveMac, DHCP_TIMEOUT_MS, DHCP_RESPONSE_TIMEOUT_MS) !=
        0) {
```

3. `tryDhcpUpgrade()` 안 (`src/main.cpp:880`):
```cpp
  if (Ethernet.begin(g_effectiveMac, DHCP_TIMEOUT_MS, DHCP_RESPONSE_TIMEOUT_MS) !=
      0) {
```

- [ ] **Step 6: printStatus()의 MAC 출력을 바꾼다**

`src/main.cpp:978-983` 부근의 MAC 출력 루프에서 `g_config.mac[i]`를 `g_effectiveMac[i]`로 교체한다 (두 군데: `< 0x10` 비교와 `print(..., HEX)`):

```cpp
  Serial.print(F("MAC: "));
  for (uint8_t i = 0; i < 6; i++) {
    if (g_effectiveMac[i] < 0x10)
      Serial.print('0');
    Serial.print(g_effectiveMac[i], HEX);
```

- [ ] **Step 7: g_config.mac 잔여 참조가 없는지 확인한다**

Run: `grep -n "g_config\.mac" src/main.cpp`
Expected: `resolveMac()` 안의 `memcmp`/`memcpy` 두 줄과, `set mac` 명령 핸들러(`src/main.cpp:1370` 부근)의 `parseMAC(value, g_config.mac)` — **총 3곳만** 남는다. `Ethernet.begin`이나 `printStatus`에는 하나도 남으면 안 된다.

- [ ] **Step 8: 빌드하고 native 테스트가 여전히 통과하는지 확인한다**

Run: `pio run && pio test -e native`
Expected: 둘 다 `[SUCCESS]` / `PASSED`. `pio run` 로그의 RAM 사용량이 이전 대비 6바이트(`g_effectiveMac`) 정도만 늘어야 한다.

- [ ] **Step 9: `set mac` 도움말에 미설정 복귀 방법을 적는다**

`src/main.cpp:1123`:

```cpp
  Serial.println(F("set mac <hex>       - Set MAC (e.g., 02F0EDCAFE01)"));
```

다음으로 교체:

```cpp
  Serial.println(F("set mac <hex>       - Set MAC (000000000000 = derive from MCU ID)"));
```

- [ ] **Step 10: Commit**

변경 파일: `src/main.cpp`

커밋 메시지:

```
feat: resolve MAC from MCU unique ID when unset

기본 MAC을 all-zero(미설정 마커)로 바꾸고, 부팅 시 g_effectiveMac을
한 번 확정한다. all-zero면 unique ID에서 유도, EEPROM에 명시 값이 있으면
그쪽 우선. Ethernet.begin 3곳과 printStatus가 모두 g_effectiveMac을 쓴다.

CONFIG_MAGIC은 의도적으로 유지 — 새 기본값이 EEPROM이 빈 새 보드에만
적용되어야 운영 장비의 target 설정과 lastDhcpIP가 보존된다.
```

---

### Task 3: fallback IP 정책 — 추측하지 않기 + 무주소 재시도 경로

**Files:**
- Modify: `src/main.cpp` (기본값 `src/main.cpp:136`, `applyStaticFallback()` `src/main.cpp:766-786`, `initNetwork()` `src/main.cpp:864-872`, `tryDhcpUpgrade()` `src/main.cpp:895`, `checkNetworkStatus()` `src/main.cpp:902-948`, 재시도 상수)

**Interfaces:**
- Consumes: `g_effectiveMac` (Task 2)
- Produces: `bool applyStaticFallback()` — 반환 타입이 `void`에서 `bool`로 바뀐다. `true` = static 주소를 적용함, `false` = 쓸 주소가 없어 아무것도 하지 않음. 호출자 3곳이 이 값을 처리해야 한다.
- Produces: `constexpr uint32_t DHCP_NOADDR_RETRY_MS` — 주소가 아예 없을 때의 재시도 주기.

**배경 (구현자가 반드시 알아야 할 것):** 현재 `applyStaticFallback()`은 절대 실패하지 않는다 — `lastDhcpIP`가 비면 하드코딩 `10.10.204.123`으로 떨어진다. 그래서 DHCP가 실패해도 항상 `g_networkReady = true`가 되고, 이후 재시도는 `g_staticFallback` 경로가 맡는다 (`src/main.cpp:942-948`). 하드코딩 후보를 없애면 **fallback이 실패할 수 있게 되고, 그 순간 재시도 경로가 통째로 사라진다**: `checkNetworkStatus()`의 `initNetwork()` 재호출은 link **transition**(`linkUp && !g_linkWasUp`, `src/main.cpp:911`)에만 걸려 있어서, 링크가 계속 UP인 채 주소만 없으면 영영 다시 시도하지 않는다. Step 5가 그 구멍을 막는 부분이며 생략하면 장비가 부팅 후 영구히 죽는다.

- [ ] **Step 1: 하드코딩 fallback 주소 기본값을 비운다**

`src/main.cpp:136-137`을 찾는다:

```cpp
    {10, 10, 204, 123},                   // Local IP (최후 fallback — 실제로는
                                          // lastDhcpIP가 우선한다)
```

다음으로 교체:

```cpp
    {0, 0, 0, 0},                         // fallback static IP. all-zero = 미설정.
                                          // 하드코딩 주소를 기본값으로 두면 EEPROM이
                                          // 빈 새 보드가 첫 부팅에 그 주소를 집어
                                          // 이미 그 IP를 쓰던 장비를 밟는다.
                                          // 운영자가 `set local`로만 지정한다.
```

- [ ] **Step 2: applyStaticFallback()이 실패할 수 있게 한다**

`src/main.cpp:766-786`의 함수 전체(주석 포함)를 다음으로 교체한다:

```cpp
// static fallback 적용: lastDhcpIP(있으면) 또는 운영자가 `set local`로 지정한
// localIP. gateway는 같은 /24의 .1로 가정 (target들이 전부 같은 subnet이라
// 실제로는 쓰이지 않는다).
//
// 둘 다 비어 있으면 아무것도 하지 않고 false를 반환한다. 이 장비는 이
// 네트워크에서 한 번도 lease를 받은 적이 없어 **어떤 주소가 비었는지 모르며**,
// 추측한 주소를 집으면 남의 장비 IP를 밟는다. 그럴 바엔 주소 없이 DHCP를
// 계속 기다리는 편이 낫다 (checkNetworkStatus의 무주소 재시도가 담당).
bool applyStaticFallback() {
  static const uint8_t kZero[4] = {0, 0, 0, 0};
  const uint8_t *a = NULL;
  if (memcmp(g_config.lastDhcpIP, kZero, 4) != 0)
    a = g_config.lastDhcpIP;
  else if (memcmp(g_config.localIP, kZero, 4) != 0)
    a = g_config.localIP;

  if (a == NULL) {
    DEBUG_PRINTLN_F("[ETH] No known-good address — staying off-net, DHCP only");
    g_staticFallback = false; // maintain()도 돌리지 않는다 (주소가 없다)
    g_lastDhcpRetryMs = millis();
    return false;
  }

  IPAddress ip(a[0], a[1], a[2], a[3]);
  IPAddress gateway(a[0], a[1], a[2], 1);
  IPAddress subnet(255, 255, 255, 0);

  Ethernet.begin(g_effectiveMac, ip, gateway, gateway, subnet);
  g_rtrPatchOk = applyW5500Timeouts();
  g_staticFallback = true; // maintain() 호출 금지 (storm 방지)
  g_lastDhcpRetryMs = millis();

  g_ctrlUdp.stop();
  g_ctrlUdp.begin(CTRL_PORT);
  g_diagUdp.stop();
  g_diagUdp.begin(nextSrcPort());
  openTargetSockets();
  return true;
}
```

- [ ] **Step 3: initNetwork()의 fallback 처리를 고친다**

`src/main.cpp:864-872`의 DHCP 실패 이후 블록을 찾는다:

```cpp
  // DHCP 실패 - static fallback (마지막 lease IP 우선)
  DEBUG_PRINTLN_F("[ETH] DHCP failed! Using static fallback...");
  applyStaticFallback();
  DEBUG_PRINT_F("[ETH] Static IP: ");
  DEBUG_PRINTLN(Ethernet.localIP());

  g_networkReady = true;
  g_linkWasUp = true;
  return true;
}
```

다음으로 교체:

```cpp
  // DHCP 실패 - static fallback (마지막 lease IP 우선)
  DEBUG_PRINTLN_F("[ETH] DHCP failed! Trying static fallback...");
  if (!applyStaticFallback()) {
    // 쓸 주소가 없다. 네트워크 미준비 상태로 두고 checkNetworkStatus()의
    // 무주소 재시도에 맡긴다. link는 살아 있으므로 g_linkWasUp은 true로
    // 둔다 — 여기서 false로 두면 다음 pass가 link UP 전이로 오인해
    // initNetwork()를 즉시 다시 불러 재시도 주기가 무의미해진다.
    g_networkReady = false;
    g_linkWasUp = true;
    return false;
  }
  DEBUG_PRINT_F("[ETH] Static IP: ");
  DEBUG_PRINTLN(Ethernet.localIP());

  g_networkReady = true;
  g_linkWasUp = true;
  return true;
}
```

- [ ] **Step 4: tryDhcpUpgrade()의 fallback 복원을 고친다**

`src/main.cpp:895` 부근, 함수 끝의 실패 경로를 찾는다:

```cpp
  applyStaticFallback(); // 실패 — begin()이 칩 설정을 건드렸으니 재적용
  WDT.refresh();
  return false;
}
```

다음으로 교체:

```cpp
  // 실패 — begin()이 칩 설정을 건드렸으니 재적용. 쓸 주소가 없으면
  // (무주소 상태에서 올라온 재시도) 네트워크 미준비로 되돌린다.
  if (!applyStaticFallback())
    g_networkReady = false;
  WDT.refresh();
  return false;
}
```

- [ ] **Step 5: 무주소 재시도 경로를 추가한다 (생략하면 장비가 영구히 죽는다)**

먼저 상수를 추가한다. `src/main.cpp:205`의 `DHCP_FALLBACK_RETRY_MS` 선언 아래에:

```cpp
// 주소가 아예 없을 때(fallback할 known-good 주소도 없음)의 DHCP 재시도 주기.
// 이 상태에선 송출할 수단 자체가 없으니 fallback 복귀(5분)보다 자주 시도한다.
constexpr uint32_t DHCP_NOADDR_RETRY_MS = 30000;
```

다음으로 `checkNetworkStatus()` 안, link 전이 처리(`src/main.cpp:911-918`)의 `g_linkWasUp = linkUp;` **바로 아래**에 재시도 분기를 넣는다:

```cpp
    g_linkWasUp = linkUp;

    // 무주소 재시도: link는 살아 있는데 주소가 없는 상태(DHCP 실패 +
    // fallback할 known-good 주소 없음). 위의 재초기화는 link **전이**에만
    // 걸려 있어서, 이 분기가 없으면 케이블을 뽑았다 꽂기 전까지 영영
    // 복구되지 않는다.
    if (!g_networkReady && linkUp &&
        now - g_lastDhcpRetryMs >= DHCP_NOADDR_RETRY_MS) {
      g_lastDhcpRetryMs = now; // 실패해도 주기 유지
      DEBUG_PRINTLN_F("[ETH] No address — retrying DHCP...");
      initNetwork();
    }
```

- [ ] **Step 6: 빌드하고 native 테스트를 확인한다**

Run: `pio run && pio test -e native`
Expected: 둘 다 `[SUCCESS]` / `PASSED`

- [ ] **Step 7: 재시도 경로를 코드로 검증한다**

Run: `grep -n "DHCP_NOADDR_RETRY_MS\|applyStaticFallback\|g_networkReady = false" src/main.cpp`
Expected 확인 항목:
- `DHCP_NOADDR_RETRY_MS`가 선언 1회 + `checkNetworkStatus()` 사용 1회 = 2곳
- `applyStaticFallback` 정의 1회 + 호출 2곳(`initNetwork`, `tryDhcpUpgrade`) = 3곳, **모든 호출이 `if (!...)` 형태로 반환값을 검사**
- `g_networkReady = false`가 link DOWN 처리, `initNetwork` 실패, `tryDhcpUpgrade` 실패 = 3곳

- [ ] **Step 8: Commit**

변경 파일: `src/main.cpp`

커밋 메시지:

```
fix: never guess a fallback IP on a device with no lease history

하드코딩 fallback 주소(10.10.204.123)를 기본값에서 제거한다. EEPROM이
빈 새 보드가 첫 부팅에 DHCP를 놓치면 그 주소를 집었는데, 하필 운영 중인
장비가 쓰던 IP라 교체 작업 중 IP 충돌을 일으켰다.

이제 fallback 후보는 lastDhcpIP(지난 lease)와 `set local`로 명시 지정한
주소뿐이고, 둘 다 없으면 주소 없이 DHCP만 기다린다.

applyStaticFallback()이 실패할 수 있게 되면서 재시도 경로가 필요해졌다 —
기존 재초기화는 link 전이에만 걸려 있어 링크가 계속 UP인 채 주소만 없으면
영영 복구되지 않는다. checkNetworkStatus()에 30초 주기 무주소 재시도 추가.
```

---

### Task 4: `info` 명령 — MAC 원격 조회

**Files:**
- Modify: `src/main.cpp` (`processControlPacket()` `src/main.cpp:679-735`, 명령 주석 `src/main.cpp:225-233`, `printHelp()` `src/main.cpp:1110-1137`, `processCommand()` `src/main.cpp:1188` 부근)

**Interfaces:**
- Consumes: `g_effectiveMac` (Task 2)
- Produces: UDP 제어 명령 `info` — 응답 포맷 `XRFD info mac=02f0edcafe01 ip=10.10.204.123 fb=10.10.204.123`. `fb`는 fallback 후보 주소이며, 없으면 `fb=none`.

**왜 status가 아니라 새 명령인가:** `buildStatusLine()`은 5초마다 도는 진단 broadcast와 `status` 응답이 **공유**하며, 수신 버퍼가 `char reply[256]`인데 현재 최악 길이가 ~247B다 (`src/main.cpp:670`). MAC 필드(17B)를 넣으면 넘쳐서 뒤쪽 `t3=...`나 `CONFLICT`가 잘리고 `dashboard/backend/protocol.py`의 파싱이 깨진다. 별도 명령은 hot path인 broadcast를 전혀 건드리지 않는다.

- [ ] **Step 1: info 응답 생성 함수를 추가한다**

`buildStatusLine()` 정의 블록 바로 아래 (`src/main.cpp:597` 부근, `buildTargetsReply` 정의 근처)에 추가:

```cpp
// `info` 응답 — 장비 identity 한 줄. 진단 broadcast와 달리 요청이 있을 때만
// 만들어지므로 buildStatusLine()의 256B 예산과 무관하다.
// 예: XRFD info mac=02f0edcafe01 ip=10.10.204.123 fb=10.10.204.123
int buildInfoLine(char *buf, size_t size) {
  static const uint8_t kZero[4] = {0, 0, 0, 0};
  IPAddress lip = Ethernet.localIP();
  const uint8_t *fb = NULL;
  if (memcmp(g_config.lastDhcpIP, kZero, 4) != 0)
    fb = g_config.lastDhcpIP;
  else if (memcmp(g_config.localIP, kZero, 4) != 0)
    fb = g_config.localIP;

  int len = snprintf(buf, size,
                     "XRFD info mac=%02x%02x%02x%02x%02x%02x ip=%u.%u.%u.%u fb=",
                     g_effectiveMac[0], g_effectiveMac[1], g_effectiveMac[2],
                     g_effectiveMac[3], g_effectiveMac[4], g_effectiveMac[5],
                     lip[0], lip[1], lip[2], lip[3]);
  if (len > 0 && len < (int)size) {
    if (fb == NULL)
      len += snprintf(buf + len, size - len, "none");
    else
      len += snprintf(buf + len, size - len, "%u.%u.%u.%u", fb[0], fb[1], fb[2],
                      fb[3]);
  }
  if (len < 0)
    return 0;
  if (len >= (int)size)
    len = (int)size - 1;
  return len;
}
```

- [ ] **Step 2: UDP 제어 핸들러에 info를 연결한다**

`src/main.cpp:680` 부근, `targets` 분기 바로 뒤에 추가한다. 순서가 중요하니 `reboot` 분기 **앞**에 넣는다:

```cpp
  } else if (strcmp(cmd, "targets") == 0) {
    rlen = buildTargetsReply(reply, sizeof(reply));
  } else if (strcmp(cmd, "info") == 0) {
    rlen = buildInfoLine(reply, sizeof(reply));
  } else if (strcmp(cmd, "reboot") == 0) {
```

- [ ] **Step 3: 미지원 명령 안내 문자열에 info를 넣는다**

`src/main.cpp:733-735`의 오류 응답을 찾는다:

```cpp
    rlen = snprintf(reply, sizeof(reply),
                    "ERR cmds: status, targets, reboot, target <0-3> "
                    "on|off|ip <a.b.c.d>|port <n>|set <ip> <port>");
```

다음으로 교체:

```cpp
    rlen = snprintf(reply, sizeof(reply),
                    "ERR cmds: status, targets, info, reboot, target <0-3> "
                    "on|off|ip <a.b.c.d>|port <n>|set <ip> <port>");
```

- [ ] **Step 4: 상단 명령 주석에 info를 추가한다**

`src/main.cpp:229` 부근, `targets` 줄 아래에 추가:

```cpp
//   targets                         - target 목록 (활성화/ip/port)
//   info                            - MAC / 현재 IP / fallback 후보 회신
```

- [ ] **Step 5: serial 콘솔에도 info를 추가한다**

`processCommand()`에서 `reboot`을 처리하는 분기(`src/main.cpp:1188` 부근)를 찾는다:

```cpp
  if (strcmp(token, "reboot") == 0) {
```

그 **바로 앞**에 추가:

```cpp
  if (strcmp(token, "info") == 0) {
    char line[128];
    int n = buildInfoLine(line, sizeof(line));
    if (n > 0)
      Serial.println(line);
    return;
  }

  if (strcmp(token, "reboot") == 0) {
```

그리고 `printHelp()`의 `--- System ---` 섹션(`src/main.cpp:1132` 부근, `save` 줄 앞)에 추가:

```cpp
  Serial.println(F("info                - Show MAC / IP / fallback"));
```

- [ ] **Step 6: 빌드한다**

Run: `pio run && pio test -e native`
Expected: 둘 다 `[SUCCESS]` / `PASSED`

- [ ] **Step 7: 응답이 버퍼에 들어가는지 산술로 확인한다**

`buildInfoLine`의 최대 길이를 세어 본다: `"XRFD info mac="`(14) + MAC hex(12) + `" ip="`(4) + IPv4 최대(15) + `" fb="`(4) + IPv4 최대(15) = **64B**. UDP 경로의 `reply[256]`과 serial 경로의 `line[128]` 모두 충분하다. 이 계산이 맞는지 위 코드와 대조해 확인하고, 어긋나면 버퍼를 키운다.

- [ ] **Step 8: Commit**

변경 파일: `src/main.cpp`

커밋 메시지:

```
feat: add `info` command reporting MAC, IP and fallback

장비 MAC을 원격으로 확인할 방법이 없어 라우터 너머의 장비는 현장에서
serial로 붙어야만 MAC을 알 수 있었다. UDP/serial 양쪽에 info 추가.

status에 얹지 않은 이유: buildStatusLine()은 5초 주기 진단 broadcast와
공유하는데 reply 버퍼 256B에 현재 최악 ~247B라 여유가 없다. MAC을 끼우면
뒤쪽 필드가 잘려 대시보드 파싱이 깨진다.
```

---

### Task 5: 문서 갱신

**Files:**
- Modify: `README.md` (`README.md:19` 지능형 static fallback 항목, `README.md:233` status 명령 설명, `README.md:429` 진단 표)
- Modify: `MANUAL.md` (`MANUAL.md:22` IP 설명, `MANUAL.md:179` IP 충돌 대응, `MANUAL.md:233` fallback IP 표)

**Interfaces:**
- Consumes: Task 2~4의 최종 동작. 문서와 코드가 어긋나면 안 되므로 이 task는 반드시 마지막에 한다.

- [ ] **Step 1: README.md의 fallback 설명을 고친다**

`README.md:19`를 찾는다:

```markdown
- **지능형 static fallback**: DHCP 실패 시 **마지막 lease IP를 재사용** (EEPROM 기억 — 하드코딩 주소보다 충돌 확률 최소화), fallback 중 5분마다 DHCP 복귀 시도, **W5500 내장 IP 충돌 감지**(IR) 시 즉시 새 lease 요청 + 진단에 `CONFLICT` 표시
```

다음으로 교체:

```markdown
- **고유 MAC 자동 유도**: MAC을 하드코딩하지 않고 MCU의 factory unique ID에서 유도 — 같은 firmware를 여러 보드에 올려도 MAC이 겹치지 않아 장비 교체 중 신·구 장비가 함께 켜져 있어도 안전. `set mac`으로 명시 지정 가능(`000000000000`이면 유도로 복귀)
- **지능형 static fallback**: DHCP 실패 시 **마지막 lease IP를 재사용** (EEPROM 기억). **known-good 주소가 없으면 fallback하지 않고** 30초마다 DHCP만 재시도 — 주소를 추측해 남의 장비 IP를 밟느니 대기한다. fallback 중 5분마다 DHCP 복귀 시도, **W5500 내장 IP 충돌 감지**(IR) 시 즉시 새 lease 요청 + 진단에 `CONFLICT` 표시
```

- [ ] **Step 2: README.md의 제어 명령 설명에 info를 넣는다**

`README.md:233` 부근의 `status` 명령 설명 문단 끝에 다음 문장을 덧붙인다:

```markdown
`info` 명령은 장비의 MAC, 현재 IP, fallback 후보 주소를 한 줄로 회신합니다 (`XRFD info mac=... ip=... fb=...`). 진단 broadcast에는 MAC이 실리지 않으므로, 라우터 너머 장비의 MAC은 이 명령으로 확인합니다.
```

- [ ] **Step 3: MANUAL.md의 IP 설명을 고친다**

`MANUAL.md:22`를 찾는다:

```markdown
장비의 IP는 DHCP로 받으며, **몰라도 됩니다** — 모든 도구가 진단 broadcast에서 자동 발견합니다.
```

다음으로 교체:

```markdown
장비의 IP는 DHCP로 받으며, **몰라도 됩니다** — 모든 도구가 진단 broadcast에서 자동 발견합니다. MAC도 보드마다 자동으로 달라지므로(MCU unique ID에서 유도) 설정할 것이 없습니다.
```

- [ ] **Step 4: MANUAL.md의 fallback IP 표를 고친다**

`MANUAL.md:233` 부근의 표 행을 찾는다:

```markdown
│ fallback IP (last resort)│ last DHCP lease, else 10.10.204.123 │
```

다음으로 교체:

```markdown
│ fallback IP (last resort)│ last DHCP lease → `set local` 지정값 │
│                          │ 둘 다 없으면 fallback 없음(DHCP 대기)│
```

표의 세로선 정렬이 깨지지 않도록 주변 행의 폭에 맞춘다.

- [ ] **Step 5: MANUAL.md에 신규 장비 투입 절차를 추가한다**

`MANUAL.md:179`의 IP 충돌 문단 뒤에 새 소절을 추가한다. 아래 바깥 fence는 4개짜리이니, **MANUAL.md에는 3-backtick 블록만 들어간다**:

````markdown
### 장비를 새로 투입하거나 교체할 때

새 보드는 꽂기만 하면 됩니다 — MAC은 MCU unique ID에서 자동 유도되고, fallback IP는 지난 lease가 없으면 아예 잡지 않으므로 기존 장비의 주소를 밟지 않습니다.

교체 중 신·구 장비가 잠시 함께 켜져 있어도 네트워크 레벨 충돌은 없지만, **둘이 같은 target으로 동시에 FreeD를 쏘면** 받는 쪽에서 두 스트림이 한 port에 섞여 tracking이 튑니다. 한쪽 target을 꺼두세요:

```
target 0 off
```

대시보드의 target 토글로도 같은 일을 할 수 있습니다.

새 장비의 MAC을 확인하려면 (라우터 너머에서도 됩니다):

```
echo "info" | nc -u -w1 <장비-ip> 50998
```
````

- [ ] **Step 6: 문서와 코드가 어긋나지 않는지 확인한다**

Run: `grep -rn "10.10.204.123" README.md MANUAL.md`
Expected: 예시 IP로 쓰인 곳만 남고, **"fallback 기본값"이나 "last resort"로 설명하는 문장은 하나도 남지 않는다.** 남아 있으면 그 문장을 고친다.

- [ ] **Step 7: Commit**

변경 파일: `README.md`, `MANUAL.md`

커밋 메시지:

```
docs: describe derived MAC and no-guess fallback policy

MAC 자동 유도와 fallback 정책 변경을 README/MANUAL에 반영하고,
신규 장비 투입·교체 절차와 info 명령 사용법을 추가한다.
```

---

## 실장비 검증 (전 task 완료 후, 운영자 수행)

이 plan은 새 보드에 올릴 firmware를 만든다. **운영 중인 `10.10.204.123` 장비에는 이 단계에서 flash하지 않는다** (8.9일 무중단 가동 중).

- [ ] 새 보드에 업로드: `pio run -t upload`
- [ ] USB serial(115200) 접속 후 `info` — MAC이 `02:f0:ed:` 로 시작하되 뒤 3바이트가 `ca:fe:01`이 **아닌지** 확인. `fb=none`이어야 정상 (아직 lease 이력 없음).
- [ ] 운영망과 분리된 상태에서 부팅 → serial에 `[ETH] No address — staying off-net` 계열 로그가 뜨고, 30초마다 `[ETH] No address — retrying DHCP...`가 반복되는지 확인. **하드코딩 주소를 잡지 않는 것이 이 plan의 핵심**이다.
- [ ] 운영망 연결 → DHCP 획득 확인. `info`의 `ip=`와 `fb=`가 같은 주소로 채워지는지 확인 (lease를 기억했다는 뜻).
- [ ] ts5-server에서 원격 확인: `ssh ts5-t 'echo info | nc -u -w1 <새-장비-ip> 50998'`
- [ ] 기존 장비와 동시 가동: 두 장비의 MAC이 다른지 ARP로 확인
      `ssh ts5-t 'ip neigh show | grep 02:f0:ed'` — 서로 다른 MAC 2개가 각각 다른 IP로 보여야 한다.
- [ ] 기존 장비의 t0(`10.10.204.184:50001`)와 겹치지 않도록, 검증 중에는 새 장비의 target을 다른 주소로 두거나 꺼 둔다.

## 알려진 한계 (의도된 것)

- **주소가 없는 동안은 진단 broadcast도 나가지 않는다.** DHCP를 한 번도 못 받은 새 장비는 대시보드에 보이지 않으며, serial 콘솔로만 상태를 알 수 있다. 주소를 추측하지 않기로 한 결정의 직접적인 대가다.
- **기존 운영 장비의 MAC은 이 firmware를 올려도 바뀌지 않는다.** EEPROM에 명시 MAC(`02:F0:ED:CA:FE:01`)이 이미 저장돼 있고 `CONFIG_MAGIC`을 유지하기 때문이다. 의도된 동작이며, 그 덕에 target 설정과 lastDhcpIP도 함께 보존된다. 굳이 유도 MAC으로 바꾸려면 `set mac 000000000000` + `save` + `reboot`.
- **`set mac`으로 지정한 값은 EEPROM에 남는다.** 유도로 되돌리려면 `set mac 000000000000`.
