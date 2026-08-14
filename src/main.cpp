/**
 * FreeD Serial -> UDP Forwarder
 * Arduino UNO R4 WiFi + Ethernet Shield v2
 *
 * 기능: RS422 Serial (YL-128 TTL 변환) -> FreeD 29Byte 패킷 -> UDP 전송
 *
 * 전송 구조: target별 전용 socket + 동기 송신, 실패 시 escalating backoff.
 *   socket마다 destination이 고정되어 chip이 MAC을 cache (ARP는 target당
 *   최초 1회 — ARP 폭격/timeout 방지). UDP 원격 제어(port 50998),
 *   운영 진단 broadcast(port 50999). 역사는 "네트워크 전역 객체" 주석 참고.
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
#include "mac_from_uid.h"
#include <Ethernet.h>
#include <EthernetUdp.h>
#include <SPI.h>
#include <WDT.h>
// raw W5500 접근 (올바른 RTR/RCR 주소 쓰기 — applyW5500Timeouts 참고).
// platformio.ini에서 Ethernet@2.0.2 정확히 고정 — 내부 헤더 의존이므로.
#include <utility/w5100.h>

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
  uint8_t lastDhcpIP[4]; // 마지막 DHCP lease IP — static fallback이 재사용
                         // ({0,0,0,0} = 아직 기록 없음 -> localIP 사용)
  uint8_t reserved[68];  // 향후 확장용
};

// 날짜 인코딩 (FD-YY-MM-DD). 코드 기본값 변경 시 bump해서 과거 debug 세션이
// EEPROM에 저장했을 수 있는 stale config가 기본값을 덮어쓰지 못하게 한다.
constexpr uint32_t CONFIG_MAGIC = 0xFD260611;
constexpr int EEPROM_ADDR = 0;

// 기본 설정값
AppConfig g_config = {
    CONFIG_MAGIC,
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00}, // MAC - all-zero = 미설정: MCU unique
                                          // ID에서 유도한다 (resolveMac 참고).
                                          // `set mac`으로 명시 지정한 경우에만
                                          // EEPROM 값이 이 유도를 덮어쓴다.
    {0, 0, 0, 0},                         // fallback static IP. all-zero = 미설정.
                                          // 하드코딩 주소를 기본값으로 두면 EEPROM이
                                          // 빈 새 보드가 첫 부팅에 그 주소를 집어
                                          // 이미 그 IP를 쓰던 장비를 밟는다.
                                          // 운영자가 `set local`로만 지정한다.
    {
        // targets[4] — 전부 미설정(0.0.0.0:0, off)으로 출고한다. 실제 주소를
        // 기본값으로 두면 EEPROM이 빈 새 보드가 전원을 넣는 순간 운영 target
        // 으로 송출을 시작해, 교체 중인 기존 장비와 같은 port에 두 스트림이
        // 섞인다. 대시보드에서 지정해야 송출이 시작된다.
        {0, {0, 0, 0, 0}, 0}, // Target 0
        {0, {0, 0, 0, 0}, 0}, // Target 1
        {0, {0, 0, 0, 0}, 0}, // Target 2
        {0, {0, 0, 0, 0}, 0}, // Target 3
    },
    {0, 0, 65445, 0x080000, 50000}, // Remap (disabled)
    {0, 0, 0, 0},                   // lastDhcpIP (미기록)
    {0}};

// ============================================================================
// 네트워크 전역 객체 — target별 socket + 동기 송신 (+ UDP 원격 제어/진단)
//
// 송신 경로의 역사 (미래의 리팩토링 전에 반드시 읽을 것):
//   v1.3까지: 단일 socket + 동기 endPacket(). 모든 target이 살아있을 때
//     59.94Hz를 장기간 유지한 검증된 구성. 단 target이 꺼지면 매 send가
//     ARP timeout(library 버그 #84로 417.6ms)에 블로킹돼 ~2Hz로 붕괴.
//   v1.4: target별 socket + 비동기 SEND(fire-and-forget)로 전환 — 이론상
//     깨끗했지만 현장에서 0.5s 단위의 간헐적 수신 끊김이 두 차례 패치로도
//     해소되지 않았다 (원인 미확정; async 기계장치가 유력 용의자).
//   v1.5: 검증된 동기 단일 socket으로 원복 + RTR/RCR raw 수정(417.6ms->80ms)
//     + escalating backoff + UDP 원격 제어. 끊김은 해소됐으나, 단일 socket의
//     DIPR 교대가 매 send 재ARP(타겟당 60 ARP/s)를 일으켜 ARP 응답이 한계인
//     host(.175, 같은 switch의 Windows Server인데도)가 ~5초당 1회 timeout.
//   v1.6(현재): socket만 target별로 분리, 송신은 v1.5의 동기 그대로 유지.
//     socket마다 DIPR이 고정되어 chip이 resolved MAC을 cache — ARP는
//     target당 최초 1회뿐이라 폭격과 timeout이 원인째 사라진다.
//     비용: cached MAC은 만료가 없어 target NIC 교체(같은 IP, 다른 MAC) 시
//     silent blackhole — 운영 절차는 dashboard에서 해당 target off->on 토글
//     또는 reboot (자동 ARP refresh는 의도적으로 제외: v1.4에서 0.5s mute
//     regression의 원인이었다).
// ============================================================================
EthernetUDP g_ctrlUdp; // 원격 제어 수신 (CTRL_PORT)
EthernetUDP g_diagUdp; // 진단 broadcast 송신 (DIAG_PORT)

// target별 전용 socket + 송신 건강 상태
struct TargetHealth {
  EthernetUDP udp;   // 전용 socket — DIPR 고정으로 chip이 MAC을 cache
  bool open = false; // begin(0) 성공 여부
  uint32_t lastFailMs = 0;
  uint32_t backoffMs = 0; // 0 = 정상. 경과시간 비교만 사용 — 절대시각 비교는
                          // millis() wrap(49.7일)에서 송출을 침묵시킨다
  uint8_t failStreak = 0; // 연속 실패 수 (backoff 단계, capped)
  uint32_t sentOk = 0;
  uint32_t sendFail = 0; // endPacket()==0 (ARP timeout = 전원 꺼짐/케이블 분리)
  uint32_t skipped = 0;  // backoff 중이라 건너뛴 frame
};
// 주의: udp 멤버가 W5500 socket을 소유하므로 `g_health[i] = TargetHealth{}`
// 통째 대입 금지 (열린 socket 누수) — clearTargetHealth()/reset 함수 사용.
TargetHealth g_health[MAX_TARGETS];

bool g_networkReady = false;
bool g_linkWasUp = false;
bool g_rtrPatchOk = false; // applyW5500Timeouts readback 결과
uint32_t g_lastLinkCheckMs = 0;

// DHCP maintain() 통제. library의 Dhcp.cpp는 renew/rebind 실패 후 state가
// 풀리지 않아 매 호출마다 ~3s 블로킹 DISCOVER를 반복한다(storm) — FreeD
// 송출이 DHCP 장애 동안 사실상 죽는다. 1초 gate + 실패 시 60초 holdoff로
// "분당 한 번의 3초 공백"으로 묶고, static fallback 모드에서는 maintain()을
// 아예 호출하지 않는다 (library가 static begin 후에도 _dhcp를 유지하는 버그).
bool g_staticFallback = false;
uint32_t g_lastMaintainMs = 0;
uint32_t g_maintainHoldoffMs = 0; // 0 = 정상(1s gate), >0 = 실패 후 holdoff

// static fallback 중 DHCP 복귀 시도 주기 (시도당 ~3.5s 송출 공백)
constexpr uint32_t DHCP_FALLBACK_RETRY_MS = 300000;

// 주소가 아예 없을 때(fallback할 known-good 주소도 없음)의 DHCP 재시도 주기.
// 이 상태에선 송출할 수단 자체가 없으니 fallback 복귀(5분)보다 자주 시도한다.
constexpr uint32_t DHCP_NOADDR_RETRY_MS = 30000;
uint32_t g_lastDhcpRetryMs = 0;

// W5500 내장 IP 충돌 감지(IR bit7) 상태 — 진단 표시 + fallback 시 즉시
// DHCP 재시도의 트리거
bool g_conflictSeen = false;
uint32_t g_lastConflictMs = 0;
uint32_t g_lastConflictRetryMs = 0;

// 도달 불가 target 재시도 backoff: 500ms -> 1s -> 2s -> 4s(cap), 성공 시 해제.
// 동기 송신에서 죽은 target probe 1회 = ARP timeout(80ms)만큼 loop 블로킹.
// backoff가 이 비용을 정상 상태에서 4초당 1회로 제한한다 (80ms 블로킹은
// Serial1 ring 512B = ~294ms 여유 안이라 frame 손실 없음). 복구 감지(전원
// 재투입 후 송신 재개)는 최악 ~4초.
constexpr uint32_t TARGET_RETRY_BASE_MS = 500;
constexpr uint8_t TARGET_RETRY_MAX_SHIFT = 3; // 500 << 3 = 4000ms cap

// UDP 원격 제어: 같은 LAN의 아무 host에서 target 설정/재부팅 (production
// 빌드에서도 동작 — serial 콘솔과 달리 USB 연결이 필요 없다).
//   echo "target 1 off" | nc -u -w1 <arduino-ip> 50998
// 명령 (설정 변경은 EEPROM 자동 저장):
//   status                          - 진단 라인 회신
//   targets                         - target 목록 (활성화/ip/port)
//   info                            - MAC / 현재 IP / fallback 후보 회신
//   target <0-3> on|off             - 활성화/비활성화
//   target <0-3> ip <a.b.c.d>       - IP 변경
//   target <0-3> port <n>           - port 변경
//   target <0-3> set <ip> <port>    - ip+port 설정 + 활성화 (한 번에)
//   reboot                          - 응답 회신 후 MCU reset
// 폐쇄 show LAN 전제 — 인증 없음.
constexpr uint16_t CTRL_PORT = 50998;

// DHCP 재시도 설정
constexpr uint8_t DHCP_MAX_RETRIES = 3;
constexpr uint32_t DHCP_RETRY_DELAY_MS = 1000;

// DHCP 호출 타임아웃. 워치독 최대(~5.59s) 안에 단일 블로킹이 들어오도록 축소한다.
// (기본 5000/4000ms는 lease 갱신이 길어질 때 쇼 도중 WDT 오작동 reset을 유발)
// DHCP는 보통 100ms 안에 응답하므로 2000/1500ms로도 충분하다.
constexpr uint32_t DHCP_TIMEOUT_MS = 2000;
constexpr uint32_t DHCP_RESPONSE_TIMEOUT_MS = 1500;

// W5500 ARP/재전송 timeout: 20ms x (3+1) = 80ms. 죽은 target으로의 동기
// endPacket()이 블로킹되는 시간 = ARP timeout이므로 짧아야 하고, 동시에
// 수신 host가 렌더 부하/절전(EEE)으로 ARP 응답이 수십 ms 늦어도 견뎌야
// 한다. v1.6의 per-socket MAC cache 덕에 ARP는 target당 최초 1회(또는
// socket reset 후)뿐이지만, 그 1회와 dead-target probe가 이 예산을 쓴다.
// (probe 1회당 loop 블로킹 80ms — Serial1 ring 여유 294ms 안.)
//
// 주의: Ethernet library의 setRetransmissionTimeout()/Count()는 W5100 레지스터
// 주소(RTR=0x0017, RCR=0x0019)를 하드코딩하는데 W5500 쓰기 경로는 주소 변환을
// 하지 않는다 (arduino-libraries/Ethernet#84). W5500의 실제 주소는 RTR=0x0019,
// RCR=0x001B — library 호출은 RTR을 0x01D0(=46.4ms)으로 오염시키고 RCR은 기본
// 8을 유지시켜, ARP timeout이 46.4ms x 9 = 417.6ms가 됐다 (= "2Hz 버그"의
// 정체). 반드시 applyW5500Timeouts()의 raw register 쓰기를 사용할 것.
constexpr uint16_t W5500_RETRANSMIT_MS = 20;
constexpr uint8_t W5500_RETRANSMIT_COUNT = 3;
constexpr uint16_t W5500_REG_RTR = 0x0019; // 16-bit, 100us 단위 (common block)
constexpr uint16_t W5500_REG_RCR = 0x001B; // 8-bit

// 운영 진단용 broadcast datagram (production 빌드에는 콘솔이 없어 내부 상태가
// 보이지 않는 문제의 해결책). 5초마다 subnet broadcast:50999로 ASCII 한 줄을
// 보낸다 — up(초)으로 WDT reset 여부, 카운터로 send 실패/skip을 현장에서
// 즉시 확인 가능. broadcast는 ARP가 없어 endPacket()이 즉시 완료된다
// (단, link down 중에는 ARP retry timer도 없으므로 송신 직전 link 확인).
constexpr uint32_t DIAG_PERIOD_MS = 5000;
constexpr uint16_t DIAG_PORT = 50999;
uint32_t g_lastDiagMs = 0;
uint32_t g_framesForwarded = 0; // 파싱 성공 후 송신 경로로 넘어간 frame 수
uint32_t g_dhcpRenew = 0;       // DHCP renew/rebind 성공 횟수
uint32_t g_dhcpFail = 0;        // DHCP renew/rebind 실패 횟수

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
 * "a.b.c.d" 문자열 파싱 (debug 콘솔 + UDP 원격 제어 공용)
 */
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

// ============================================================================
// 네트워크 함수
// ============================================================================

// W5500 재전송 타임아웃을 raw register로 설정하고 readback으로 검증한다.
// W5100Class::init()의 soft reset은 최초 chip 감지 시에만 일어나지만, 전원
// 글리치로 인한 chip 리셋이나 향후 library 변경에 대비해 begin() 직후마다
// 재적용한다 — 비용은 SPI 몇 transaction뿐.
// (RTR/RCR은 common register라 1회 설정으로 모든 socket에 적용)
bool applyW5500Timeouts() {
  if (W5100.getChip() != 55) {
    DEBUG_PRINTLN_F("[ETH] WARN: not W5500 - RTR/RCR raw patch skipped");
    return false;
  }
  const uint16_t ticks = W5500_RETRANSMIT_MS * 10; // RTR 단위 = 100us
  uint8_t rtr[2] = {(uint8_t)(ticks >> 8), (uint8_t)(ticks & 0xFF)};
  SPI.beginTransaction(SPI_ETHERNET_SETTINGS);
  W5100.write(W5500_REG_RTR, rtr, 2);
  W5100.write(W5500_REG_RCR, W5500_RETRANSMIT_COUNT);
  uint8_t chk[2];
  W5100.read(W5500_REG_RTR, chk, 2);
  uint8_t rcrChk = W5100.read(W5500_REG_RCR);
  SPI.endTransaction();
  if (chk[0] != rtr[0] || chk[1] != rtr[1] ||
      rcrChk != W5500_RETRANSMIT_COUNT) {
    DEBUG_PRINTLN_F("[ETH] WARN: RTR/RCR readback mismatch");
    return false;
  }
  return true;
}

// 통계/backoff 초기화 (socket은 건드리지 않는다)
void clearTargetHealth(uint8_t i) {
  TargetHealth &h = g_health[i];
  h.lastFailMs = 0;
  h.backoffMs = 0;
  h.failStreak = 0;
  h.sentOk = 0;
  h.sendFail = 0;
  h.skipped = 0;
}

// W5500 ephemeral source port 할당기. library의 begin(0) 카운터는
// 49152..65535를 순회하며 CTRL_PORT(50998)/DIAG_PORT(50999)도 밟는데,
// target socket이 그 port를 물면 W5500의 inbound demux(낮은 socket index
// 우선)가 제어/진단 수신을 그 socket으로 삼켜버린다(읽는 코드가 없어 유실).
// 예약 port를 건너뛰며 직접 할당해 원천 차단한다.
uint16_t nextSrcPort() {
  static uint16_t p = 49152;
  do {
    p++;
    if (p < 49152) // uint16 wrap -> 범위 복귀
      p = 49152;
  } while (p == CTRL_PORT || p == DIAG_PORT);
  return p;
}

// target socket을 (재)생성한다. 효과 세 가지:
//  - TX FIFO 초기화: endPacket() 실패 시 남은 payload가 다음 datagram에
//    병합되는 것(29B -> 58B 오염) 방지
//  - cached MAC 무효화: 다음 send가 ARP를 다시 수행 (NIC 교체 복구 경로 —
//    dashboard에서 off->on 토글이 이 함수를 거친다)
//  - begin 실패 시 open=false로 남겨 송신 경로가 backoff로 재시도
bool resetTargetSocket(uint8_t i) {
  TargetHealth &h = g_health[i];
  h.udp.stop();
  h.open = h.udp.begin(nextSrcPort()) != 0; // 예약 port 회피 ephemeral
  return h.open;
}

void armTargetBackoff(TargetHealth &h, uint32_t now) {
  h.lastFailMs = now;
  h.backoffMs = TARGET_RETRY_BASE_MS << h.failStreak;
  if (h.failStreak < TARGET_RETRY_MAX_SHIFT)
    h.failStreak++;
}

// 활성 target들의 socket을 (재)생성한다. best-effort — 실패한 target은
// 송신 경로의 backoff가 재시도한다. Ethernet.begin() 이후에만 호출할 것.
void openTargetSockets() {
  for (uint8_t i = 0; i < MAX_TARGETS; i++) {
    TargetHealth &h = g_health[i];
    h.udp.stop();
    h.open = false;
    clearTargetHealth(i);
    if (g_config.targets[i].enabled) {
      resetTargetSocket(i);
    }
  }
}

// target이 실제로 지정됐는지 — IP가 0.0.0.0이거나 port가 0이면 미설정이다.
// 미설정 target을 켜면 socket은 열리지만 송신이 계속 실패해, 대시보드에
// "ON인데 fail만 오르는" 상태로 보인다. on 경로에서 이걸 막는다.
bool targetConfigured(uint8_t i) {
  const TargetConfig &tc = g_config.targets[i];
  return tc.port != 0 &&
         (tc.ip[0] | tc.ip[1] | tc.ip[2] | tc.ip[3]) != 0;
}

// target 설정 변경(on/off/ip/port) 적용: 통계 초기화 + socket 재생성/해제.
// IP가 바뀌면 다음 send에서 새 주소로 ARP가 일어난다.
void applyTargetConfigChange(uint8_t i) {
  clearTargetHealth(i);
  g_health[i].udp.stop();
  g_health[i].open = false;
  if (g_config.targets[i].enabled && g_networkReady) {
    resetTargetSocket(i);
  }
}

void sendUdpPacket(const uint8_t *data, uint8_t len) {
  if (!g_networkReady)
    return;

  for (uint8_t i = 0; i < MAX_TARGETS; i++) {
    if (!g_config.targets[i].enabled)
      continue;
    // 동기 송신은 target마다 ARP timeout까지 블로킹될 수 있다. RTR patch
    // 실패 시(1.8s/probe) 4 target 연속이면 WDT(5.59s)를 넘으므로 매 target
    // 직전에 갱신 — loop 본문은 hardware timeout으로 유계라 진짜 hang 보호는
    // 유지된다.
    WDT.refresh();
    TargetHealth &h = g_health[i];
    uint32_t now = millis();

    // 실패 backoff 중이면 건너뛴다 (경과시간 비교 — millis wrap 안전)
    if (h.backoffMs != 0 && (now - h.lastFailMs) < h.backoffMs) {
      h.skipped++;
      continue;
    }

    // socket 미확보 상태면 재확보 시도 (실패 시 backoff로 빈도 제한)
    if (!h.open && !resetTargetSocket(i)) {
      armTargetBackoff(h, now);
      continue;
    }

    IPAddress dst(g_config.targets[i].ip[0], g_config.targets[i].ip[1],
                  g_config.targets[i].ip[2], g_config.targets[i].ip[3]);
    if (!h.udp.beginPacket(dst, g_config.targets[i].port))
      continue; // 0.0.0.0 또는 port 0 — 설정 오류
    h.udp.write(data, len);

    // 동기 송신: MAC이 cache된 정상 target은 <1ms. ARP가 필요한 첫 send와
    // 죽은 target은 최대 80ms 블로킹 — 실패 시 backoff로 probe를 제한한다.
    if (h.udp.endPacket()) {
      h.sentOk++;
      h.backoffMs = 0;
      h.failStreak = 0;
    } else {
      h.sendFail++;
      armTargetBackoff(h, now);
      resetTargetSocket(i); // 실패 payload 제거 + 다음 시도에서 재ARP
    }
  }
}

// 상태 한 줄 생성 — 진단 broadcast와 원격 제어 status 응답이 공유한다.
// 예: XRFD up=123 ip=10.10.204.100 rx=7380 dhcp=0/0 rtr=Y
//     t0=A,7380,0,0 t1=B,12,3,45 t2=off t3=off
//     (t<i> = state[A=alive/B=backoff/C=no-socket/off], sentOk, sendFail, skipped)
int buildStatusLine(char *buf, size_t size) {
  IPAddress lip = Ethernet.localIP();
  // ms = 장치 자체 millis(). rx와 같은 패킷에 실리므로 수신측이
  // frame rate를 (Δrx / Δms)로 계산하면 네트워크/처리 지연과 무관하게 정확하다.
  uint32_t nowMs = millis();
  int len = snprintf(buf, size,
                     "XRFD up=%lu ms=%lu ip=%u.%u.%u.%u rx=%lu dhcp=%lu/%lu rtr=%c",
                     (unsigned long)(nowMs / 1000UL), (unsigned long)nowMs,
                     lip[0], lip[1], lip[2], lip[3],
                     (unsigned long)g_framesForwarded,
                     (unsigned long)g_dhcpRenew, (unsigned long)g_dhcpFail,
                     g_rtrPatchOk ? 'Y' : 'N');
  for (uint8_t i = 0; i < MAX_TARGETS && len > 0 && len < (int)size; i++) {
    if (!g_config.targets[i].enabled) {
      len += snprintf(buf + len, size - len, " t%u=off", (unsigned)i);
      continue;
    }
    const TargetHealth &h = g_health[i];
    len += snprintf(buf + len, size - len, " t%u=%c,%lu,%lu,%lu", (unsigned)i,
                    !h.open ? 'C' : (h.backoffMs == 0 ? 'A' : 'B'),
                    (unsigned long)h.sentOk,
                    (unsigned long)h.sendFail, (unsigned long)h.skipped);
  }
  // 최근 60초 내 IP 충돌이 감지됐으면 표시 (dashboard가 경고로 띄운다)
  if (g_conflictSeen && len > 0 && len < (int)size &&
      millis() - g_lastConflictMs < 60000) {
    len += snprintf(buf + len, size - len, " CONFLICT");
  }
  if (len < 0)
    return 0;
  if (len >= (int)size)
    len = (int)size - 1;
  return len;
}

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

// 운영 진단 datagram을 subnet broadcast로 송신 — 상단 DIAG 상수 주석 참고.
// 현장 수신: 같은 LAN의 아무 host에서  tcpdump -A -n udp port 50999
void sendDiagnostics() {
  if (!g_networkReady)
    return;
  uint32_t now = millis();
  if (now - g_lastDiagMs < DIAG_PERIOD_MS)
    return;
  g_lastDiagMs = now;

  // broadcast send에는 ARP retry timer가 없어 link down 도중의 endPacket()이
  // 오래 머물 수 있다 — 송신 직전에 link를 확인한다 (SPI 1회, 5초마다)
  if (Ethernet.linkStatus() != LinkON)
    return;

  // 256: 카운터들이 10자리(>1e9, 약 6개월 연속운용)가 되어도 잘리지 않게
  char buf[256];
  int len = buildStatusLine(buf, sizeof(buf));
  if (len <= 0)
    return;

  // subnet broadcast 주소 = localIP | ~subnetMask (byte 단위 — endianness 무관)
  IPAddress lip = Ethernet.localIP();
  IPAddress sm = Ethernet.subnetMask();
  IPAddress bcast((uint8_t)(lip[0] | ~sm[0]), (uint8_t)(lip[1] | ~sm[1]),
                  (uint8_t)(lip[2] | ~sm[2]), (uint8_t)(lip[3] | ~sm[3]));
  if (!g_diagUdp.beginPacket(bcast, DIAG_PORT))
    return;
  g_diagUdp.write((const uint8_t *)buf, (size_t)len);
  g_diagUdp.endPacket(); // broadcast: ARP 없음, 즉시 완료
}

// targets 명령 응답: 4개 target의 활성화/ip/port 목록 (개행 구분, 1 datagram)
// 예: t0 on 10.10.204.184:50001
//     t1 off 10.10.204.175:50001
int buildTargetsReply(char *buf, size_t size) {
  int len = 0;
  for (uint8_t i = 0; i < MAX_TARGETS && len >= 0 && len < (int)size; i++) {
    const TargetConfig &tc = g_config.targets[i];
    len += snprintf(buf + len, size - len, "%st%u %s %u.%u.%u.%u:%u",
                    (i == 0) ? "" : "\n", (unsigned)i,
                    tc.enabled ? "on" : "off", tc.ip[0], tc.ip[1], tc.ip[2],
                    tc.ip[3], tc.port);
  }
  if (len < 0)
    return 0;
  if (len >= (int)size)
    len = (int)size - 1;
  return len;
}

// UDP 원격 제어 명령 처리 (CTRL_PORT) — 문법은 상단 상수 주석 참고.
// non-blocking: 패킷이 없으면 즉시 반환. 응답은 송신자에게 unicast 회신.
void processControlPacket() {
  if (!g_networkReady)
    return;
  int sz = g_ctrlUdp.parsePacket();
  if (sz <= 0)
    return;

  char cmd[64];
  int n = g_ctrlUdp.read(cmd, sizeof(cmd) - 1);
  g_ctrlUdp.flush();
  if (n <= 0)
    return;
  cmd[n] = '\0';
  while (n > 0 &&
         (cmd[n - 1] == '\n' || cmd[n - 1] == '\r' || cmd[n - 1] == ' '))
    cmd[--n] = '\0';

  char reply[256]; // buildStatusLine 최악 길이(~247B) 수용
  int rlen = 0;
  int idx = -1;
  unsigned int pnum = 0;
  char arg[16] = {0};
  uint8_t ipb[4];
  bool doReboot = false;

  // 구체적인 패턴을 먼저 매칭한다 ("target N set ..."이 "target N <action>"에
  // 잡아먹히지 않도록 순서 중요)
  if (strcmp(cmd, "status") == 0) {
    rlen = buildStatusLine(reply, sizeof(reply));
  } else if (strcmp(cmd, "targets") == 0) {
    rlen = buildTargetsReply(reply, sizeof(reply));
  } else if (strcmp(cmd, "info") == 0) {
    rlen = buildInfoLine(reply, sizeof(reply));
  } else if (strcmp(cmd, "reboot") == 0) {
    rlen = snprintf(reply, sizeof(reply), "OK rebooting");
    doReboot = true; // 응답을 먼저 보낸 뒤 reset
  } else if (sscanf(cmd, "target %d set %15s %u", &idx, arg, &pnum) == 3 &&
             idx >= 0 && idx < MAX_TARGETS) {
    // ip+port 설정 + 활성화를 한 번에 (shell의 ChangeTarget용)
    if (parseIP(arg, ipb) && pnum >= 1 && pnum <= 65535) {
      memcpy(g_config.targets[idx].ip, ipb, 4);
      g_config.targets[idx].port = (uint16_t)pnum;
      g_config.targets[idx].enabled = 1;
      applyTargetConfigChange(idx);
      saveConfig();
      rlen = snprintf(reply, sizeof(reply), "OK target %d = %s:%u on (saved)",
                      idx, arg, pnum);
    } else {
      rlen = snprintf(reply, sizeof(reply), "ERR invalid ip/port");
    }
  } else if (sscanf(cmd, "target %d ip %15s", &idx, arg) == 2 && idx >= 0 &&
             idx < MAX_TARGETS) {
    if (parseIP(arg, ipb)) {
      memcpy(g_config.targets[idx].ip, ipb, 4);
      applyTargetConfigChange(idx);
      saveConfig();
      rlen = snprintf(reply, sizeof(reply), "OK target %d ip %s (saved)", idx,
                      arg);
    } else {
      rlen = snprintf(reply, sizeof(reply), "ERR invalid ip");
    }
  } else if (sscanf(cmd, "target %d port %u", &idx, &pnum) == 2 && idx >= 0 &&
             idx < MAX_TARGETS) {
    if (pnum >= 1 && pnum <= 65535) {
      g_config.targets[idx].port = (uint16_t)pnum;
      applyTargetConfigChange(idx);
      saveConfig();
      rlen = snprintf(reply, sizeof(reply), "OK target %d port %u (saved)",
                      idx, pnum);
    } else {
      rlen = snprintf(reply, sizeof(reply), "ERR invalid port");
    }
  } else if (sscanf(cmd, "target %d %15s", &idx, arg) == 2 && idx >= 0 &&
             idx < MAX_TARGETS &&
             (strcmp(arg, "on") == 0 || strcmp(arg, "off") == 0)) {
    bool on = (strcmp(arg, "on") == 0);
    if (on && !targetConfigured(idx)) {
      rlen = snprintf(reply, sizeof(reply),
                      "ERR target %d not configured (set ip/port first)", idx);
    } else {
      g_config.targets[idx].enabled = on ? 1 : 0;
      applyTargetConfigChange(idx); // 통계 초기화 + socket 재생성/해제
      saveConfig();                 // 재부팅 후에도 유지
      rlen = snprintf(reply, sizeof(reply), "OK target %d %s (saved)", idx,
                      on ? "on" : "off");
    }
  } else {
    rlen = snprintf(reply, sizeof(reply),
                    "ERR cmds: status, targets, info, reboot, target <0-3> "
                    "on|off|ip <a.b.c.d>|port <n>|set <ip> <port>");
  }

  if (rlen <= 0)
    return;
  if (rlen >= (int)sizeof(reply))
    rlen = (int)sizeof(reply) - 1;
  if (g_ctrlUdp.beginPacket(g_ctrlUdp.remoteIP(), g_ctrlUdp.remotePort())) {
    g_ctrlUdp.write((const uint8_t *)reply, (size_t)rlen);
    g_ctrlUdp.endPacket(); // 송신자는 방금 패킷을 보낸 host — ARP 즉시 해석
  }

  if (doReboot) {
    delay(100); // 응답 datagram이 wire로 나갈 시간
    NVIC_SystemReset();
  }
}

// DHCP가 준 IP를 기억한다 (변경 시에만 flash 기록 — wear 무시 가능 수준).
// static fallback이 이 주소를 재사용한다: "방금까지 합법적으로 내 것이던
// 주소"라 임의의 하드코딩 주소보다 충돌 확률이 압도적으로 낮고, DHCP 서버는
// MAC별로 같은 IP를 다시 주는 경향이 있어 복귀 시에도 대개 동일 IP로 이어진다.
void rememberDhcpLease() {
  IPAddress ip = Ethernet.localIP();
  uint8_t cur[4] = {ip[0], ip[1], ip[2], ip[3]};
  if (memcmp(cur, g_config.lastDhcpIP, 4) != 0) {
    memcpy(g_config.lastDhcpIP, cur, 4);
    saveConfig();
  }
}

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

    if (Ethernet.begin(g_effectiveMac, DHCP_TIMEOUT_MS, DHCP_RESPONSE_TIMEOUT_MS) !=
        0) {
      // DHCP 성공
      g_rtrPatchOk = applyW5500Timeouts();
      DEBUG_PRINT_F("[ETH] DHCP OK! IP: ");
      DEBUG_PRINTLN(Ethernet.localIP());

      g_staticFallback = false; // DHCP 모드: maintain() 활성 (필수 — 미해제
                                // 시 link 복구 후 lease가 조용히 만료된다)
      g_maintainHoldoffMs = 0;
      g_lastMaintainMs = millis();
      rememberDhcpLease(); // fallback용으로 이 lease IP를 기억
      // ctrl socket을 target들보다 먼저 bind — W5500 inbound demux가 낮은
      // index 우선이라 제어 port가 항상 이기게 (port 할당기와 이중 방어)
      g_ctrlUdp.stop();
      g_ctrlUdp.begin(CTRL_PORT);
      g_diagUdp.stop();
      g_diagUdp.begin(nextSrcPort());
      openTargetSockets(); // 활성 target socket 준비 (실패분은 send에서 재시도)

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

// fallback 중 DHCP 복귀 시도 (단발, 최대 ~3.5s 블로킹 = 그동안 송출 공백).
// 성공 시 DHCP 모드로 전환 + lease 기억, 실패 시 static fallback 복원.
bool tryDhcpUpgrade() {
  WDT.refresh();
  DEBUG_PRINTLN_F("[ETH] Fallback: retrying DHCP...");
  if (Ethernet.begin(g_effectiveMac, DHCP_TIMEOUT_MS, DHCP_RESPONSE_TIMEOUT_MS) !=
      0) {
    g_rtrPatchOk = applyW5500Timeouts();
    g_staticFallback = false;
    g_maintainHoldoffMs = 0;
    g_lastMaintainMs = millis();
    rememberDhcpLease();
    g_ctrlUdp.stop();
    g_ctrlUdp.begin(CTRL_PORT);
    g_diagUdp.stop();
    g_diagUdp.begin(nextSrcPort());
    openTargetSockets();
    WDT.refresh();
    DEBUG_PRINT_F("[ETH] DHCP recovered! IP: ");
    DEBUG_PRINTLN(Ethernet.localIP());
    return true;
  }
  // 실패 — begin()이 칩 설정을 건드렸으니 재적용. 쓸 주소가 없으면
  // (무주소 상태에서 올라온 재시도) 네트워크 미준비로 되돌린다.
  if (!applyStaticFallback())
    g_networkReady = false;
  WDT.refresh();
  return false;
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

    // W5500 내장 IP 충돌 감지: 다른 host가 우리 IP를 주장하면(같은 sender
    // IP의 ARP 수신) chip이 IR bit7을 세운다. fallback 중의 충돌은 대개
    // "DHCP가 복구되어 이 주소를 남에게 줬다"는 신호 — 즉시 새 lease를
    // 받아 해소한다 (60s rate limit). lease 보유 중의 충돌은 우리가 합법
    // 소유자이므로 진단(CONFLICT 표시)으로만 알린다.
    if (g_networkReady) {
      SPI.beginTransaction(SPI_ETHERNET_SETTINGS);
      uint8_t ir = W5100.readIR();
      if (ir & 0x80)
        W5100.writeIR(0x80); // write-1-clear
      SPI.endTransaction();
      if (ir & 0x80) {
        g_conflictSeen = true;
        g_lastConflictMs = now;
        if (g_staticFallback && now - g_lastConflictRetryMs >= 60000) {
          g_lastConflictRetryMs = now;
          tryDhcpUpgrade();
        }
      }
    }

    // fallback 중 주기적 DHCP 복귀 시도
    if (g_networkReady && g_staticFallback &&
        now - g_lastDhcpRetryMs >= DHCP_FALLBACK_RETRY_MS) {
      g_lastDhcpRetryMs = now; // tryDhcpUpgrade 실패 시에도 주기 유지
      tryDhcpUpgrade();
    }
  }

  // DHCP 갱신 — gate/holdoff 설명은 g_staticFallback 선언부 주석 참고.
  // 결과는 집계해 진단 datagram으로 노출.
  if (g_networkReady && !g_staticFallback) {
    uint32_t gate = (g_maintainHoldoffMs != 0) ? g_maintainHoldoffMs : 1000;
    if (now - g_lastMaintainMs >= gate) {
      g_lastMaintainMs = now;
      int rc = Ethernet.maintain();
      if (rc == 2 || rc == 4) { // RENEW_OK / REBIND_OK
        g_dhcpRenew++;
        g_maintainHoldoffMs = 0;
      } else if (rc == 1 || rc == 3) { // RENEW_FAIL / REBIND_FAIL
        g_dhcpFail++;
        g_maintainHoldoffMs = 60000;
      }
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
    if (g_effectiveMac[i] < 0x10)
      Serial.print('0');
    Serial.print(g_effectiveMac[i], HEX);
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

  Serial.print(F("W5500 RTR/RCR patch: "));
  Serial.println(g_rtrPatchOk ? F("OK (ARP timeout 80ms)")
                              : F("FAILED (chip default 1.8s)"));
  Serial.print(F("DHCP renew ok/fail: "));
  Serial.print(g_dhcpRenew);
  Serial.print('/');
  Serial.println(g_dhcpFail);

  Serial.println(F("TX stats [ok/fail/skipped]:"));
  for (uint8_t i = 0; i < MAX_TARGETS; i++) {
    if (!g_config.targets[i].enabled)
      continue;
    Serial.print(F("  ["));
    Serial.print(i);
    Serial.print(F("] "));
    Serial.print(g_health[i].sentOk);
    Serial.print('/');
    Serial.print(g_health[i].sendFail);
    Serial.print('/');
    Serial.println(g_health[i].skipped);
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
  Serial.println(F("set mac <hex>       - Set MAC (000000000000 = derive from MCU ID)"));
  Serial.println(F("--- Serial ---"));
  Serial.println(F("set parity n|o|e    - Set parity (none/odd/even)"));
  Serial.println(F("--- Remap ---"));
  Serial.println(F("set remap on|off    - Enable/disable remap"));
  Serial.println(F("set inmin <n>       - Remap input min"));
  Serial.println(F("set inmax <n>       - Remap input max"));
  Serial.println(F("set offset <hex>    - Remap offset"));
  Serial.println(F("set scale <n>       - Remap scale"));
  Serial.println(F("--- System ---"));
  Serial.println(F("info                - Show MAC / IP / fallback"));
  Serial.println(F("save                - Save to EEPROM"));
  Serial.println(F("load                - Load from EEPROM"));
  Serial.println(F("reboot              - Restart"));
  Serial.println(F("====================\n"));
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
    if (g_networkReady)
      openTargetSockets(); // target 구성이 바뀌었을 수 있음
    printStatus();
    return;
  }

  if (strcmp(token, "info") == 0) {
    char line[128];
    int n = buildInfoLine(line, sizeof(line));
    if (n > 0)
      Serial.println(line);
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
      applyTargetConfigChange(idx);
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
      if (!targetConfigured(idx)) {
        Serial.println(F("[ERR] Target not configured (set ip/port first)"));
        return;
      }
      g_config.targets[idx].enabled = 1;
      applyTargetConfigChange(idx);
      Serial.print(F("[OK] Target "));
      Serial.print(idx);
      Serial.println(F(" ENABLED"));
      return;
    }

    if (strcmp(action, "off") == 0) {
      g_config.targets[idx].enabled = 0;
      applyTargetConfigChange(idx);
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
      applyTargetConfigChange(0);
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
    uint32_t txFail = 0;
    for (uint8_t i = 0; i < MAX_TARGETS; i++) {
      txFail += g_health[i].sendFail;
    }
    Serial.print(F(" TXF:"));
    Serial.print(txFail); // 누적 send 실패 (ARP timeout 등)
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

  g_framesForwarded++;
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
  // loop()당 처리 바이트 상한: 어떤 입력 패턴에서도 WDT.refresh()와 제어/진단
  // 처리가 굶지 않도록 보장한다. 입력은 1740 B/s(60Hz x 29B)이고
  // loop는 sub-ms 주기로 돌므로 상한이 처리율을 제한하지는 않는다.
  constexpr uint16_t kBytesPerPass = 128;
  uint16_t budget = kBytesPerPass;

  while (budget > 0 && Serial1.available() > 0) {
    budget--;
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
  resolveMac(); // g_effectiveMac 확정 — initNetwork()보다 먼저여야 한다

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
  processControlPacket(); // UDP 원격 제어 (non-blocking)
  sendDiagnostics();      // 5초마다 진단 broadcast

#if DEBUG_SERIAL_MONITOR == 1
  printStats();
#endif
}
