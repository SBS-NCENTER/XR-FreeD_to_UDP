import socket
import struct
import time
import tkinter as tk



x=200
y=200
z=0
p=0
t=0
r=0
zo=1024
fo=512



# --- 설정 ---
HOST = '10.10.204.255' 
PORT = 50001        
PACKET_SIZE = 29
CAM_ID = 0x01
DEGREE_SCALE = 32768.0   #각도 디그리
CM_SCALE = 640.0 
# --- 체크섬 계산 함수 
def calculate_checksum(packet_data):
    checksum_value = 0
    data_to_sum = packet_data[:28] 
    for byte in data_to_sum:
        checksum_value = (checksum_value + (0x40 - byte)) & 0xFF 
    return checksum_value

# 4바이트 정수 값을 3바이트 바이너리 데이터로 변환 /빅엔디안 /
def serialize_24bit_signed(value):
    return struct.pack('>i', value)[1:]

# --- 메인 송신 함수 ---[D1(1)] [CA(1)] [Pan(3)] [Tilt(3)] [AX(3)] [X(3)] [Y(3)] [Z(3)] [ZH(3)] [FH(3)] [ST(2)] [CK(1)]
def run_d1_sender():
    sender_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    pan_angle = 0.0
    pan_delta = 0.5 
    try:
        print(f" D1 Format 송신 시작: {HOST}:{PORT}")
        while True:
            # 2. D1 포맷의 정수 단위로 변환
            raw_pos_x = int(x * CM_SCALE) 
            raw_pos_y = int(y * CM_SCALE) 
            raw_pos_z = int(z * CM_SCALE)
            raw_pan = int(p * DEGREE_SCALE)
            raw_tilt = int(t * DEGREE_SCALE) 
            raw_roll = int(r * DEGREE_SCALE)  
            raw_zoom = zo
            raw_focus = fo
            status_bytes = b'\x00\x00' # Status H, L (2 bytes)

            # 3. 데이터 필드별 3바이트 직렬화
            pos_x_bytes = serialize_24bit_signed(raw_pos_x)
            pos_y_bytes = serialize_24bit_signed(raw_pos_y)
            pos_z_bytes = serialize_24bit_signed(raw_pos_z)
            pan_bytes = serialize_24bit_signed(raw_pan)
            tilt_bytes = serialize_24bit_signed(raw_tilt)
            roll_bytes = serialize_24bit_signed(raw_roll)
            zoom_bytes = struct.pack('>i', raw_zoom)[1:]
            focus_bytes = struct.pack('>i', raw_focus)[1:]
            reserved_bytes = b'\x00\x00\x00' # Reserved Data (Bytes 9-11)
            
            # 4. 28바이트 (체크섬 제외) 패킷 조립
            d1_type = 0xD1
            
            packet_base = struct.pack('>BB', d1_type, CAM_ID) + \
                          pan_bytes + tilt_bytes + roll_bytes + \
                          pos_x_bytes + pos_y_bytes + pos_z_bytes + \
                          zoom_bytes + focus_bytes + status_bytes 
            
            checksum = calculate_checksum(packet_base)                 # 5. [체크섬 계산]
            
            final_packet = packet_base + struct.pack('>B', checksum)   # 6. 최종 29바이트 패킷 조립 (체크섬 추가)

            sender_socket.sendto(final_packet, (HOST, PORT))           # 7. 전송
            
            output_string = (
                f"\rX: {raw_pos_x / 640.0:.2f} cm |" # 단위 1/64mm 적용
                f"Y: {raw_pos_y / 640.0:.2f} cm |" # 단위 1/64mm 적용
                f"Z: {raw_pos_z / 640.0:.2f} cm |" # 단위 1/64mm 적용
                f"Pan: {raw_pan / 32768.0:.2f}° | " # 단위 1/32768° 적용
                f"Tilt: {raw_tilt / 32768.0:.2f}° | "
                f"Roll: {raw_roll / 32768.0:.2f}° | "
                f"Zoom: {raw_zoom} | "
                f"focus: {raw_focus} | "  )
            print(output_string, end='', flush=True)

            time.sleep(0.01)

    except Exception as e:
        print(f"\r 에러 종료: {e}")
        
    finally:
        sender_socket.close()
        print("\n 송신 소켓이 닫혔습니다.")




if __name__ == "__main__":

    run_d1_sender()