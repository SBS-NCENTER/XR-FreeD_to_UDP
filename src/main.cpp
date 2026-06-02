/**
 * FreeD Serial -> UDP Forwarder
 * Arduino UNO R4 WiFi + Ethernet Shield v2
 *
 * 기능: RS422 Serial (YL-128 TTL 변환) -> FreeD 29Byte 패킷 -> UDP 전송
 *
 * 하드웨어 연결:
 *   - YL-128 TX -> Arduino RX1 (D0)
 *   - YL-128 RX -> Arduino TX1 (D1) [사용 안 함]
 *   - YL-128 GND -> Arduino GND
 *   - Ethernet Shield v2 -> SPI (D10, D11, D12, D13)
 *
 * 빌드 설정:
 *   - DEBUG_SERIAL_MONITOR=0 : 시리얼 출력 비활성화 (프로덕션)
 *   - DEBUG_SERIAL_MONITOR=1 : 시리얼 출력 활성화 (디버깅)
 *
 * FreeD D1 패킷 구조 (29 Bytes):
 *   [0]     : Type (0xD1)
 *   [1]     : Camera ID
 *   [2-4]   : Pan (signed 24-bit, 1/32768 degree)
 *   [5-7]   : Tilt
 *   [8-10]  : Roll
 *   [11-13] : X Position (signed 24-bit, 1/64mm)
 *   [14-16] : Y Position
 *   [17-19] : Z Position
 *   [20-22] : Zoom (unsigned 24-bit)
 *   [23-25] : Focus (unsigned 24-bit)
 *   [26-27] : Status (2 bytes)
 *   [28]    : Checksum
 *
 * 체크섬: (64 - sum(packet[0:28])) % 256  (FreeD 표준)
 */

#include <Arduino.h>
#include <EEPROM.h>
#include <Ethernet.h>
#include <EthernetUdp.h>
#include <SPI.h>
#include <WDT.h>

// ============================================================================
// 디버그 모드 설정
// 0: 프로덕션 (시리얼 출력 OFF)
// 1: 디버그 (시리얼 출력 ON)
// ============================================================================
#define DEBUG_SERIAL_MONITOR 0

// 디버그 매크로 정의
#if DEBUG_SERIAL_MONITOR == 1
#define DEBUG_BEGIN(x) Serial.begin(x)
#define DEBUG_WAIT() while (!Serial && millis() < 3000)
#define DEBUG_PRINT(x) Serial.print(x)
#define DEBUG_PRINTLN(x) Serial.println(x)
#define DEBUG_PRINT_F(x) Serial.print(F(x))
#define DEBUG_PRINTLN_F(x) Serial.println(F(x))
#else
#define DEBUG_BEGIN(x)
#define DEBUG_WAIT()
#define DEBUG_PRINT(x)
#define DEBUG_PRINTLN(x)
#define DEBUG_PRINT_F(x)
#define DEBUG_PRINTLN_F(x)
#endif

// ============================================================================
// FreeD 상수
// ============================================================================
namespace FreeD {
constexpr uint8_t TYPE_D1 = 0xD1;
constexpr uint8_t PACKET_LEN = 29;
constexpr uint32_t BAUD_RATE = 38400;

// 패킷 필드 오프셋
constexpr uint8_t OFF_TYPE = 0;
constexpr uint8_t OFF_CAM_ID = 1;
constexpr uint8_t OFF_PAN = 2;
constexpr uint8_t OFF_TILT = 5;
constexpr uint8_t OFF_ROLL = 8;
constexpr uint8_t OFF_X = 11;
constexpr uint8_t OFF_Y = 14;
constexpr uint8_t OFF_Z = 17;
constexpr uint8_t OFF_ZOOM = 20;
constexpr uint8_t OFF_FOCUS = 23;
constexpr uint8_t OFF_STATUS = 26;
constexpr uint8_t OFF_CHECKSUM = 28;
} // namespace FreeD

// ============================================================================
// 설정 구조체
// ============================================================================
struct RemapConfig {
  uint8_t enabled;
  uint32_t inMin;
  uint32_t inMax;
  uint32_t offset; // 0x080000
  uint32_t scale;  // 50000
};

// 멀티 타겟 설정
constexpr uint8_t MAX_TARGETS = 4;

struct TargetConfig {
  uint8_t enabled;
  uint8_t ip[4];
  uint16_t port;
};

struct AppConfig {
  uint32_t magic;
  uint8_t mac[6];
  uint8_t localIP[4];
  TargetConfig targets[MAX_TARGETS]; // 최대 4개 타겟
  RemapConfig remap;
  uint8_t reserved[72]; // 향후 확장용
};

constexpr uint32_t CONFIG_MAGIC = 0xFD251223;
constexpr int EEPROM_ADDR = 0;

// 기본 설정값
AppConfig g_config = {
    CONFIG_MAGIC,
    {0x02, 0xF0, 0xED, 0xCA, 0xFE, 0x01}, // MAC - 고유값 (Locally Administered)
    {10, 10, 204, 100},                   // Local IP (fallback)
    {
        // targets[4] - 멀티 타겟 설정
        {1, {10, 10, 204, 184}, 50001}, // Target 0: 활성화
        {1, {10, 10, 204, 61}, 50001},  // Target 1: 활성화
        {0, {10, 10, 204, 186}, 50001}, // Target 2: 비활성화
        {0, {10, 10, 204, 187}, 50001}, // Target 3: 비활성화
    },
    {0, 0, 65445, 0x080000, 50000}, // Remap (disabled)
    {0}};

// ============================================================================
// 네트워크 전역 객체
// ============================================================================
EthernetUDP g_udp;
bool g_networkReady = false;
bool g_linkWasUp = false;
uint32_t g_lastLinkCheckMs = 0;

// DHCP 재시도 설정
constexpr uint8_t DHCP_MAX_RETRIES = 3;
constexpr uint32_t DHCP_RETRY_DELAY_MS = 1000;

// DHCP 호출 타임아웃. 워치독 최대(~5.59s) 안에 단일 블로킹이 들어오도록 축소한다.
// (기본 5000/4000ms는 lease 갱신이 길어질 때 쇼 도중 WDT 오작동 reset을 유발)
// DHCP는 보통 100ms 안에 응답하므로 2000/1500ms로도 충분하다.
constexpr uint32_t DHCP_TIMEOUT_MS = 2000;
constexpr uint32_t DHCP_RESPONSE_TIMEOUT_MS = 1500;

// W5500 재전송(ARP 포함) 타임아웃 튜닝. 기본 RTR=200ms x RCR=8 = ~1.8s 블로킹을
// 20ms x (1+1) = ~40ms로 제한하여, 도달 불가 target으로의 endPacket()이
// loop와 Serial1 RX를 굶기지 않게 한다.
constexpr uint16_t W5500_RETRANSMIT_MS = 20;
constexpr uint8_t W5500_RETRANSMIT_COUNT = 1;

// ============================================================================
// FreeD 프레임 처리 변수
// ============================================================================
uint8_t g_frameBuffer[FreeD::PACKET_LEN];
uint8_t g_frameIndex = 0;

// 동기화 상태 머신
enum class SyncState : uint8_t {
  HUNTING,    // 0xD1 헤더 탐색
  COLLECTING, // 29바이트 수집
};
SyncState g_syncState = SyncState::HUNTING;

// ============================================================================
// 통계 변수 (디버그 모드에서만 사용)
// ============================================================================
#if DEBUG_SERIAL_MONITOR == 1
volatile uint32_t g_ppsCounter = 0;
volatile uint32_t g_bytesReceived = 0;
volatile uint32_t g_checksumErrors = 0;
uint32_t g_lastStatsMs = 0;

// 디버깅 변수
bool g_dumpEnabled = false;
uint8_t g_dumpCount = 0;
uint8_t g_parityMode = 1;

// 콘솔 명령 버퍼
char g_cmdBuffer[128];
uint8_t g_cmdIndex = 0;
#endif

// ============================================================================
// 유틸리티 함수
// ============================================================================

/**
 * FreeD 체크섬 계산 (표준 방식)
 */
uint8_t calculateChecksum(const uint8_t *packet) {
  uint16_t sum = 0;
  for (uint8_t i = 0; i < 28; i++) {
    sum += packet[i];
  }
  return (64 - (sum & 0xFF)) & 0xFF;
}

/**
 * 체크섬 검증
 */
bool verifyChecksum(const uint8_t *packet) {
  return packet[FreeD::OFF_CHECKSUM] == calculateChecksum(packet);
}

/**
 * 24-bit Big-Endian 읽기
 */
uint32_t readU24BE(const uint8_t *p) {
  return ((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | (uint32_t)p[2];
}

/**
 * 24-bit Big-Endian 쓰기
 */
void writeU24BE(uint8_t *p, uint32_t val) {
  val &= 0xFFFFFF;
  p[0] = (uint8_t)(val >> 16);
  p[1] = (uint8_t)(val >> 8);
  p[2] = (uint8_t)(val);
}

/**
 * Zoom/Focus 리매핑 (선형 스케일링)
 */
uint32_t applyRemap(uint32_t val) {
  if (!g_config.remap.enabled) {
    return val & 0xFFFFFF;
  }

  uint32_t inMin = g_config.remap.inMin;
  uint32_t inMax = g_config.remap.inMax;

  if (inMax <= inMin) {
    return g_config.remap.offset & 0xFFFFFF;
  }

  if (val <= inMin)
    return g_config.remap.offset & 0xFFFFFF;
  if (val >= inMax)
    return (g_config.remap.offset + g_config.remap.scale) & 0xFFFFFF;

  uint64_t numerator = (uint64_t)g_config.remap.scale * (val - inMin);
  uint32_t result =
      g_config.remap.offset + (uint32_t)(numerator / (inMax - inMin));

  return result & 0xFFFFFF;
}

// ============================================================================
// EEPROM 설정 관리
// ============================================================================
void loadConfig() {
  AppConfig temp;
  EEPROM.get(EEPROM_ADDR, temp);

  if (temp.magic == CONFIG_MAGIC) {
    g_config = temp;
    DEBUG_PRINTLN_F("[CONFIG] Loaded from EEPROM");
  } else {
    DEBUG_PRINTLN_F("[CONFIG] Using defaults (new version)");
  }
}

void saveConfig() {
  g_config.magic = CONFIG_MAGIC;
  EEPROM.put(EEPROM_ADDR, g_config);
  DEBUG_PRINTLN_F("[CONFIG] Saved to EEPROM");
}

// ============================================================================
// 네트워크 함수
// ============================================================================

// W5500 재전송 타임아웃을 짧게 설정한다. Ethernet.begin()이 칩을 리셋하므로
// begin() 직후 매번 호출해야 한다. (RTR/RCR은 공용 레지스터라 1회 설정으로 모든
// socket에 적용)
void applyW5500Timeouts() {
  Ethernet.setRetransmissionTimeout(W5500_RETRANSMIT_MS);
  Ethernet.setRetransmissionCount(W5500_RETRANSMIT_COUNT);
}

bool initNetwork() {
  DEBUG_PRINTLN_F("[ETH] Initializing...");

  // W5500 초기화 전 딜레이
  delay(100);
  Ethernet.init(10); // W5500 CS = D10
  delay(100);

  // 링크 상태 확인
  DEBUG_PRINT_F("[ETH] Link: ");
  if (Ethernet.linkStatus() == LinkOFF) {
    DEBUG_PRINTLN_F("DOWN - Check cable!");
    g_networkReady = false;
    return false;
  }
  DEBUG_PRINTLN_F("UP");

  // DHCP 시도 (재시도 포함)
  DEBUG_PRINTLN_F("[ETH] DHCP requesting...");

  for (uint8_t retry = 0; retry < DHCP_MAX_RETRIES; retry++) {
    WDT.refresh(); // 재시도 루프가 워치독(~5.59s)을 넘기지 않도록 매 회 갱신
    if (retry > 0) {
      DEBUG_PRINT_F("[ETH] DHCP retry ");
      DEBUG_PRINT(retry);
      DEBUG_PRINTLN_F("...");
      delay(DHCP_RETRY_DELAY_MS);
    }

    if (Ethernet.begin(g_config.mac, DHCP_TIMEOUT_MS, DHCP_RESPONSE_TIMEOUT_MS) !=
        0) {
      // DHCP 성공
      applyW5500Timeouts();
      DEBUG_PRINT_F("[ETH] DHCP OK! IP: ");
      DEBUG_PRINTLN(Ethernet.localIP());

      if (!g_udp.begin(0)) {
        DEBUG_PRINTLN_F("[UDP] Failed to start");
        g_networkReady = false;
        return false;
      }

#if DEBUG_SERIAL_MONITOR == 1
      Serial.println(F("[UDP] Targets:"));
      for (uint8_t i = 0; i < MAX_TARGETS; i++) {
        if (g_config.targets[i].enabled) {
          Serial.print(F("  ["));
          Serial.print(i);
          Serial.print(F("] "));
          Serial.print(g_config.targets[i].ip[0]);
          Serial.print('.');
          Serial.print(g_config.targets[i].ip[1]);
          Serial.print('.');
          Serial.print(g_config.targets[i].ip[2]);
          Serial.print('.');
          Serial.print(g_config.targets[i].ip[3]);
          Serial.print(':');
          Serial.println(g_config.targets[i].port);
        }
      }
#endif

      g_networkReady = true;
      g_linkWasUp = true;
      return true;
    }
  }

  // DHCP 실패 - Static IP 사용
  DEBUG_PRINTLN_F("[ETH] DHCP failed! Using static IP...");

  IPAddress ip(g_config.localIP[0], g_config.localIP[1], g_config.localIP[2],
               g_config.localIP[3]);
  IPAddress gateway(g_config.localIP[0], g_config.localIP[1],
                    g_config.localIP[2], 1);
  IPAddress subnet(255, 255, 255, 0);

  Ethernet.begin(g_config.mac, ip, gateway, gateway, subnet);
  applyW5500Timeouts();

  DEBUG_PRINT_F("[ETH] Static IP: ");
  DEBUG_PRINTLN(Ethernet.localIP());

  if (!g_udp.begin(0)) {
    DEBUG_PRINTLN_F("[UDP] Failed to start");
    g_networkReady = false;
    return false;
  }

  g_networkReady = true;
  g_linkWasUp = true;
  return true;
}

void checkNetworkStatus() {
  uint32_t now = millis();

  if (now - g_lastLinkCheckMs >= 2000) {
    g_lastLinkCheckMs = now;

    bool linkUp = (Ethernet.linkStatus() == LinkON);

    if (linkUp && !g_linkWasUp) {
      DEBUG_PRINTLN_F("[ETH] Link UP - Reinitializing...");
      delay(500);
      initNetwork();
    } else if (!linkUp && g_linkWasUp) {
      DEBUG_PRINTLN_F("[ETH] Link DOWN");
      g_networkReady = false;
    }

    g_linkWasUp = linkUp;
  }

  // DHCP 갱신
  if (g_networkReady) {
    Ethernet.maintain();
  }
}

void sendUdpPacket(const uint8_t *data, uint8_t len) {
  if (!g_networkReady)
    return;

  for (uint8_t i = 0; i < MAX_TARGETS; i++) {
    if (g_config.targets[i].enabled) {
      IPAddress target(g_config.targets[i].ip[0], g_config.targets[i].ip[1],
                       g_config.targets[i].ip[2], g_config.targets[i].ip[3]);

      g_udp.beginPacket(target, g_config.targets[i].port);
      g_udp.write(data, len);
      g_udp.endPacket();
    }
  }
}

// ============================================================================
// 디버그 전용 함수
// ============================================================================
#if DEBUG_SERIAL_MONITOR == 1

void printNetworkStatus() {
  Serial.print(F("Ethernet Link: "));
  Serial.println(Ethernet.linkStatus() == LinkON ? "UP" : "DOWN");
  Serial.print(F("IP: "));
  Serial.println(Ethernet.localIP());
  Serial.print(F("MAC: "));
  for (uint8_t i = 0; i < 6; i++) {
    if (g_config.mac[i] < 0x10)
      Serial.print('0');
    Serial.print(g_config.mac[i], HEX);
    if (i < 5)
      Serial.print(':');
  }
  Serial.println();
}

void dumpPacket(const uint8_t *packet, uint8_t len, bool valid) {
  Serial.print(valid ? F("[DUMP OK] ") : F("[DUMP NG] "));
  for (uint8_t i = 0; i < len; i++) {
    if (packet[i] < 0x10)
      Serial.print('0');
    Serial.print(packet[i], HEX);
    Serial.print(' ');
  }

  uint8_t calcSum = calculateChecksum(packet);
  Serial.print(F("| calc="));
  if (calcSum < 0x10)
    Serial.print('0');
  Serial.print(calcSum, HEX);
  Serial.print(F(" pkt="));
  if (packet[28] < 0x10)
    Serial.print('0');
  Serial.println(packet[28], HEX);
}

void setSerialParity(uint8_t mode) {
  Serial1.end();
  delay(10);

  switch (mode) {
  case 0:
    Serial1.begin(FreeD::BAUD_RATE, SERIAL_8N1);
    Serial.println(F("[UART] Parity: None (8N1)"));
    break;
  case 1:
    Serial1.begin(FreeD::BAUD_RATE, SERIAL_8O1);
    Serial.println(F("[UART] Parity: Odd (8O1)"));
    break;
  case 2:
    Serial1.begin(FreeD::BAUD_RATE, SERIAL_8E1);
    Serial.println(F("[UART] Parity: Even (8E1)"));
    break;
  default:
    Serial1.begin(FreeD::BAUD_RATE, SERIAL_8N1);
    Serial.println(F("[UART] Parity: None (8N1) - default"));
    break;
  }
  g_parityMode = mode;
}

void printStatus() {
  Serial.println(F("\n========== STATUS =========="));
  Serial.println(F("Mode: Ethernet (DEBUG)"));

  printNetworkStatus();

  Serial.println(F("Targets:"));
  for (uint8_t i = 0; i < MAX_TARGETS; i++) {
    Serial.print(F("  ["));
    Serial.print(i);
    Serial.print(F("] "));
    if (g_config.targets[i].enabled) {
      Serial.print(g_config.targets[i].ip[0]);
      Serial.print('.');
      Serial.print(g_config.targets[i].ip[1]);
      Serial.print('.');
      Serial.print(g_config.targets[i].ip[2]);
      Serial.print('.');
      Serial.print(g_config.targets[i].ip[3]);
      Serial.print(':');
      Serial.print(g_config.targets[i].port);
      Serial.println(F(" [ON]"));
    } else {
      Serial.print(g_config.targets[i].ip[0]);
      Serial.print('.');
      Serial.print(g_config.targets[i].ip[1]);
      Serial.print('.');
      Serial.print(g_config.targets[i].ip[2]);
      Serial.print('.');
      Serial.print(g_config.targets[i].ip[3]);
      Serial.print(':');
      Serial.print(g_config.targets[i].port);
      Serial.println(F(" [OFF]"));
    }
  }

  Serial.print(F("Remap: "));
  if (g_config.remap.enabled) {
    Serial.print(F("ON ("));
    Serial.print(g_config.remap.inMin);
    Serial.print(F("-"));
    Serial.print(g_config.remap.inMax);
    Serial.print(F(" -> 0x"));
    Serial.print(g_config.remap.offset, HEX);
    Serial.print(F("+"));
    Serial.print(g_config.remap.scale);
    Serial.println(F(")"));
  } else {
    Serial.println(F("OFF"));
  }

  Serial.println(F("============================\n"));
}

void printHelp() {
  Serial.println(F("\n===== COMMANDS ====="));
  Serial.println(F("status              - Show status"));
  Serial.println(F("dump [n]            - Dump n packets (default 5)"));
  Serial.println(F("--- Target (Multi) ---"));
  Serial.println(F("target <0-3> ip <a.b.c.d>  - Set target IP"));
  Serial.println(F("target <0-3> port <n>      - Set target port"));
  Serial.println(F("target <0-3> on|off        - Enable/disable target"));
  Serial.println(F("--- Legacy (Target 0) ---"));
  Serial.println(F("set ip <a.b.c.d>    - Set target 0 IP"));
  Serial.println(F("set port <n>        - Set target 0 port"));
  Serial.println(F("--- Network ---"));
  Serial.println(F("set local <a.b.c.d> - Set fallback static IP"));
  Serial.println(F("set mac <hex>       - Set MAC (e.g., 02F0EDCAFE01)"));
  Serial.println(F("--- Serial ---"));
  Serial.println(F("set parity n|o|e    - Set parity (none/odd/even)"));
  Serial.println(F("--- Remap ---"));
  Serial.println(F("set remap on|off    - Enable/disable remap"));
  Serial.println(F("set inmin <n>       - Remap input min"));
  Serial.println(F("set inmax <n>       - Remap input max"));
  Serial.println(F("set offset <hex>    - Remap offset"));
  Serial.println(F("set scale <n>       - Remap scale"));
  Serial.println(F("--- System ---"));
  Serial.println(F("save                - Save to EEPROM"));
  Serial.println(F("load                - Load from EEPROM"));
  Serial.println(F("reboot              - Restart"));
  Serial.println(F("====================\n"));
}

bool parseIP(const char *str, uint8_t *out) {
  int a, b, c, d;
  if (sscanf(str, "%d.%d.%d.%d", &a, &b, &c, &d) != 4)
    return false;
  if (a < 0 || a > 255 || b < 0 || b > 255 || c < 0 || c > 255 || d < 0 ||
      d > 255)
    return false;
  out[0] = a;
  out[1] = b;
  out[2] = c;
  out[3] = d;
  return true;
}

bool parseMAC(const char *str, uint8_t *out) {
  if (strlen(str) != 12)
    return false;

  for (uint8_t i = 0; i < 6; i++) {
    char hex[3] = {str[i * 2], str[i * 2 + 1], '\0'};
    out[i] = strtoul(hex, NULL, 16);
  }
  return true;
}

void processCommand(char *cmd) {
  while (*cmd == ' ')
    cmd++;
  char *end = cmd + strlen(cmd) - 1;
  while (end > cmd && (*end == ' ' || *end == '\r' || *end == '\n')) {
    *end-- = '\0';
  }

  if (strlen(cmd) == 0)
    return;

  char *token = strtok(cmd, " ");
  if (!token)
    return;

  if (strcmp(token, "status") == 0) {
    printStatus();
    return;
  }

  if (strcmp(token, "help") == 0 || strcmp(token, "?") == 0) {
    printHelp();
    return;
  }

  if (strcmp(token, "save") == 0) {
    saveConfig();
    return;
  }

  if (strcmp(token, "load") == 0) {
    loadConfig();
    printStatus();
    return;
  }

  if (strcmp(token, "reboot") == 0) {
    Serial.println(F("[SYS] Rebooting..."));
    delay(100);
    NVIC_SystemReset();
    return;
  }

  if (strcmp(token, "dump") == 0) {
    char *countStr = strtok(NULL, " ");
    uint8_t count = 5;
    if (countStr) {
      count = atoi(countStr);
      if (count == 0 || count > 100)
        count = 5;
    }
    g_dumpCount = count;
    g_dumpEnabled = true;
    Serial.print(F("[DUMP] Will dump "));
    Serial.print(count);
    Serial.println(F(" packets..."));
    return;
  }

  // 멀티 타겟 명령어
  if (strcmp(token, "target") == 0) {
    char *idxStr = strtok(NULL, " ");
    char *action = strtok(NULL, " ");
    char *value = strtok(NULL, " ");

    if (!idxStr) {
      Serial.println(F("[ERR] Usage: target <0-3> ip/port/on/off [value]"));
      return;
    }

    int idx = atoi(idxStr);
    if (idx < 0 || idx >= MAX_TARGETS) {
      Serial.println(F("[ERR] Target index must be 0-3"));
      return;
    }

    if (!action) {
      Serial.print(F("Target ["));
      Serial.print(idx);
      Serial.print(F("]: "));
      Serial.print(g_config.targets[idx].ip[0]);
      Serial.print('.');
      Serial.print(g_config.targets[idx].ip[1]);
      Serial.print('.');
      Serial.print(g_config.targets[idx].ip[2]);
      Serial.print('.');
      Serial.print(g_config.targets[idx].ip[3]);
      Serial.print(':');
      Serial.print(g_config.targets[idx].port);
      Serial.println(g_config.targets[idx].enabled ? " [ON]" : " [OFF]");
      return;
    }

    if (strcmp(action, "ip") == 0) {
      if (!value || !parseIP(value, g_config.targets[idx].ip)) {
        Serial.println(F("[ERR] Invalid IP"));
        return;
      }
      Serial.print(F("[OK] Target "));
      Serial.print(idx);
      Serial.print(F(" IP: "));
      Serial.print(g_config.targets[idx].ip[0]);
      Serial.print('.');
      Serial.print(g_config.targets[idx].ip[1]);
      Serial.print('.');
      Serial.print(g_config.targets[idx].ip[2]);
      Serial.print('.');
      Serial.println(g_config.targets[idx].ip[3]);
      return;
    }

    if (strcmp(action, "port") == 0) {
      if (!value) {
        Serial.println(F("[ERR] Missing port"));
        return;
      }
      g_config.targets[idx].port = atoi(value);
      Serial.print(F("[OK] Target "));
      Serial.print(idx);
      Serial.print(F(" Port: "));
      Serial.println(g_config.targets[idx].port);
      return;
    }

    if (strcmp(action, "on") == 0) {
      g_config.targets[idx].enabled = 1;
      Serial.print(F("[OK] Target "));
      Serial.print(idx);
      Serial.println(F(" ENABLED"));
      return;
    }

    if (strcmp(action, "off") == 0) {
      g_config.targets[idx].enabled = 0;
      Serial.print(F("[OK] Target "));
      Serial.print(idx);
      Serial.println(F(" DISABLED"));
      return;
    }

    Serial.println(F("[ERR] Unknown action. Use: ip/port/on/off"));
    return;
  }

  if (strcmp(token, "set") == 0) {
    char *param = strtok(NULL, " ");
    char *value = strtok(NULL, " ");

    if (!param) {
      Serial.println(F("[ERR] Missing parameter"));
      return;
    }

    if (strcmp(param, "ip") == 0) {
      if (!value || !parseIP(value, g_config.targets[0].ip)) {
        Serial.println(F("[ERR] Invalid IP"));
        return;
      }
      Serial.print(F("[OK] Target 0 IP: "));
      Serial.print(g_config.targets[0].ip[0]);
      Serial.print('.');
      Serial.print(g_config.targets[0].ip[1]);
      Serial.print('.');
      Serial.print(g_config.targets[0].ip[2]);
      Serial.print('.');
      Serial.println(g_config.targets[0].ip[3]);
      return;
    }

    if (strcmp(param, "port") == 0) {
      if (!value) {
        Serial.println(F("[ERR] Missing port"));
        return;
      }
      g_config.targets[0].port = atoi(value);
      Serial.print(F("[OK] Target 0 Port: "));
      Serial.println(g_config.targets[0].port);
      return;
    }

    if (strcmp(param, "parity") == 0) {
      if (!value) {
        Serial.println(F("[ERR] Use n/o/e (none/odd/even)"));
        return;
      }
      if (value[0] == 'n' || value[0] == 'N') {
        setSerialParity(0);
      } else if (value[0] == 'o' || value[0] == 'O') {
        setSerialParity(1);
      } else if (value[0] == 'e' || value[0] == 'E') {
        setSerialParity(2);
      } else {
        Serial.println(F("[ERR] Use n/o/e (none/odd/even)"));
      }
      return;
    }

    if (strcmp(param, "local") == 0) {
      if (!value || !parseIP(value, g_config.localIP)) {
        Serial.println(F("[ERR] Invalid IP"));
        return;
      }
      Serial.print(F("[OK] Fallback IP: "));
      Serial.print(g_config.localIP[0]);
      Serial.print('.');
      Serial.print(g_config.localIP[1]);
      Serial.print('.');
      Serial.print(g_config.localIP[2]);
      Serial.print('.');
      Serial.println(g_config.localIP[3]);
      return;
    }

    if (strcmp(param, "mac") == 0) {
      if (!value || !parseMAC(value, g_config.mac)) {
        Serial.println(
            F("[ERR] Invalid MAC (use 12 hex chars, e.g., 02F0EDCAFE01)"));
        return;
      }
      Serial.print(F("[OK] MAC: "));
      for (uint8_t i = 0; i < 6; i++) {
        if (g_config.mac[i] < 0x10)
          Serial.print('0');
        Serial.print(g_config.mac[i], HEX);
        if (i < 5)
          Serial.print(':');
      }
      Serial.println(F(" (reboot to apply)"));
      return;
    }

    if (strcmp(param, "remap") == 0) {
      if (!value) {
        Serial.println(F("[ERR] Use 'on' or 'off'"));
        return;
      }
      if (strcmp(value, "on") == 0) {
        g_config.remap.enabled = 1;
        Serial.println(F("[OK] Remap ON"));
      } else if (strcmp(value, "off") == 0) {
        g_config.remap.enabled = 0;
        Serial.println(F("[OK] Remap OFF"));
      }
      return;
    }

    if (strcmp(param, "inmin") == 0 && value) {
      g_config.remap.inMin = strtoul(value, NULL, 10);
      Serial.print(F("[OK] inMin: "));
      Serial.println(g_config.remap.inMin);
      return;
    }

    if (strcmp(param, "inmax") == 0 && value) {
      g_config.remap.inMax = strtoul(value, NULL, 10);
      Serial.print(F("[OK] inMax: "));
      Serial.println(g_config.remap.inMax);
      return;
    }

    if (strcmp(param, "offset") == 0 && value) {
      g_config.remap.offset = strtoul(value, NULL, 16);
      Serial.print(F("[OK] offset: 0x"));
      Serial.println(g_config.remap.offset, HEX);
      return;
    }

    if (strcmp(param, "scale") == 0 && value) {
      g_config.remap.scale = strtoul(value, NULL, 10);
      Serial.print(F("[OK] scale: "));
      Serial.println(g_config.remap.scale);
      return;
    }

    Serial.println(F("[ERR] Unknown param. Type 'help'"));
    return;
  }

  Serial.println(F("[ERR] Unknown cmd. Type 'help'"));
}

void processConsole() {
  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      if (g_cmdIndex > 0) {
        g_cmdBuffer[g_cmdIndex] = '\0';
        processCommand(g_cmdBuffer);
        g_cmdIndex = 0;
      }
    } else if (g_cmdIndex < sizeof(g_cmdBuffer) - 1) {
      g_cmdBuffer[g_cmdIndex++] = c;
    }
  }
}

void printStats() {
  uint32_t now = millis();

  if (now - g_lastStatsMs >= 1000) {
    Serial.print(F("[STAT] PPS:"));
    Serial.print(g_ppsCounter);
    Serial.print(F(" BPS:"));
    Serial.print(g_bytesReceived);
    Serial.print(F(" ERR:"));
    Serial.print(g_checksumErrors);
    Serial.print(F(" SYNC:"));
    Serial.println(g_syncState == SyncState::HUNTING ? "HUNT" : "COLL");

    g_ppsCounter = 0;
    g_bytesReceived = 0;
    g_lastStatsMs = now;
  }
}

#endif // DEBUG_SERIAL_MONITOR == 1

// ============================================================================
// FreeD 패킷 처리
// ============================================================================

void processAndSendPacket(uint8_t *packet) {
  if (g_config.remap.enabled) {
    uint32_t zoomIn = readU24BE(&packet[FreeD::OFF_ZOOM]);
    uint32_t focusIn = readU24BE(&packet[FreeD::OFF_FOCUS]);

    uint32_t zoomOut = applyRemap(zoomIn);
    uint32_t focusOut = applyRemap(focusIn);

    writeU24BE(&packet[FreeD::OFF_ZOOM], zoomOut);
    writeU24BE(&packet[FreeD::OFF_FOCUS], focusOut);

    packet[FreeD::OFF_CHECKSUM] = calculateChecksum(packet);
  }

  sendUdpPacket(packet, FreeD::PACKET_LEN);

#if DEBUG_SERIAL_MONITOR == 1
  g_ppsCounter++;
#endif
}

int8_t findNextHeader(const uint8_t *buf, uint8_t len, uint8_t startPos) {
  for (uint8_t i = startPos; i < len; i++) {
    if (buf[i] == FreeD::TYPE_D1) {
      return i;
    }
  }
  return -1;
}

void processSerialData() {
  while (Serial1.available() > 0) {
    int c = Serial1.read();
    if (c < 0)
      break;

    uint8_t byte = (uint8_t)c;

#if DEBUG_SERIAL_MONITOR == 1
    g_bytesReceived++;
#endif

    switch (g_syncState) {
    case SyncState::HUNTING:
      if (byte == FreeD::TYPE_D1) {
        g_frameBuffer[0] = byte;
        g_frameIndex = 1;
        g_syncState = SyncState::COLLECTING;
      }
      break;

    case SyncState::COLLECTING:
      g_frameBuffer[g_frameIndex++] = byte;

      if (g_frameIndex >= FreeD::PACKET_LEN) {
        bool valid = verifyChecksum(g_frameBuffer);

#if DEBUG_SERIAL_MONITOR == 1
        if (g_dumpEnabled && g_dumpCount > 0) {
          dumpPacket(g_frameBuffer, FreeD::PACKET_LEN, valid);
          g_dumpCount--;
          if (g_dumpCount == 0) {
            g_dumpEnabled = false;
            Serial.println(F("[DUMP] Finished"));
          }
        }
#endif

        if (valid) {
          processAndSendPacket(g_frameBuffer);
          g_syncState = SyncState::HUNTING;
          g_frameIndex = 0;
        } else {
#if DEBUG_SERIAL_MONITOR == 1
          g_checksumErrors++;
#endif

          int8_t nextHeader =
              findNextHeader(g_frameBuffer, FreeD::PACKET_LEN, 1);

          if (nextHeader > 0) {
            uint8_t remaining = FreeD::PACKET_LEN - nextHeader;
            memmove(g_frameBuffer, &g_frameBuffer[nextHeader], remaining);
            g_frameIndex = remaining;
          } else {
            g_syncState = SyncState::HUNTING;
            g_frameIndex = 0;
          }
        }
      }
      break;
    }
  }
}

// ============================================================================
// setup() / loop()
// ============================================================================
void setup() {
  DEBUG_BEGIN(115200);
  DEBUG_WAIT();

  DEBUG_PRINTLN_F("\n=====================================");
  DEBUG_PRINTLN_F("  FreeD Serial -> UDP Forwarder");
#if DEBUG_SERIAL_MONITOR == 1
  DEBUG_PRINTLN_F("  Mode: Ethernet (DEBUG)");
#else
  DEBUG_PRINTLN_F("  Mode: Ethernet (PRODUCTION)");
#endif
  DEBUG_PRINTLN_F("=====================================\n");

  loadConfig();

  Serial1.begin(FreeD::BAUD_RATE, SERIAL_8O1);
  DEBUG_PRINTLN_F("[UART] Serial1: 38400, 8O1");

  initNetwork();

  // 하드웨어 워치독 시작: loop가 ~5s 이상 멈추면 MCU 자동 reset.
  // begin(ms)는 PCLKB 24MHz 기준 ~5.59s가 최대(realized). 초기 부팅 DHCP는
  // 감시 대상에서 제외하려고 initNetwork() 뒤에 시작한다.
  if (WDT.begin(5000)) {
    DEBUG_PRINTLN_F("[WDT] Started (~5.59s)");
  } else {
    DEBUG_PRINTLN_F("[WDT] FAILED to start");
  }

#if DEBUG_SERIAL_MONITOR == 1
  printStatus();
  Serial.println(F("Type 'help' for commands.\n"));
  g_lastStatsMs = millis();
#endif
}

void loop() {
  WDT.refresh(); // 하드웨어 워치독 갱신 (loop가 ~5.59s 이상 멈추면 reset)

#if DEBUG_SERIAL_MONITOR == 1
  // 디버그 모드: 콘솔 + 통계 출력
  processConsole();
#endif

  checkNetworkStatus();
  processSerialData();

#if DEBUG_SERIAL_MONITOR == 1
  printStats();
#endif
}
