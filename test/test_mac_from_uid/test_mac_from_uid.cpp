#include <string.h>
#include <unity.h>

#include "mac_from_uid.h"

// RA4M1 unique IDs are 16 bytes. Boards from the same wafer/lot tend to
// share leading bytes, so these test vectors deliberately differ in only a
// trailing byte — a near-worst-case pair modeling boards from the same batch.
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
  // bit1 = locally administered (1), bit0 = multicast (0).
  // If this breaks, the switch will silently treat frames as multicast and
  // misbehave.
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
