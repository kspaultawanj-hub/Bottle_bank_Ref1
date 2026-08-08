"""
อ่านค่า UID จากเครื่องอ่าน RFID แบบ USB (HID Keyboard Emulation)
ใช้ library evdev เพื่ออ่านค่าแบบ background ไม่ต้องพึ่ง terminal focus

ติดตั้งก่อนใช้งาน:
    sudo apt install -y python3-evdev
    pip3 install --break-system-packages evdev

ต้องรันด้วยสิทธิ์ที่เข้าถึง /dev/input/ ได้ (ปกติต้องใช้ sudo หรือเพิ่ม user
เข้า group 'input': sudo usermod -aG input $USER แล้ว logout/login ใหม่)
"""

import evdev
from evdev import InputDevice, categorize, ecodes, list_devices

from supabase_client import get_student_by_uid, register_student, update_points

# แผนที่คีย์โค้ดตัวเลข (KEY_1 ... KEY_0) เป็นตัวอักษร
KEYCODE_MAP = {
    "KEY_1": "1", "KEY_2": "2", "KEY_3": "3", "KEY_4": "4", "KEY_5": "5",
    "KEY_6": "6", "KEY_7": "7", "KEY_8": "8", "KEY_9": "9", "KEY_0": "0",
    "KEY_ENTER": "ENTER",
}


def find_rfid_device():
    """
    ค้นหาเครื่องอ่าน RFID อัตโนมัติจากชื่ออุปกรณ์ input ทั้งหมด

    ใช้คำค้นหาที่เจาะจงก่อน (rfid, sycreader, id&ic) เพื่อเลี่ยงการจับอุปกรณ์อื่น
    ที่บังเอิญมีคำว่า "keyboard" หรือ "hid" ในชื่อ (เช่น จอสัมผัส หรือคีย์บอร์ดจำลองอื่นๆ)
    ถ้าหาด้วยคำเจาะจงไม่เจอ ค่อย fallback ไปหาด้วยคำกว้างๆ เป็นทางเลือกสุดท้าย
    ถ้าหาไม่เจอเลย ให้รันฟังก์ชัน list_all_devices() เพื่อดูชื่อจริงแล้วแก้ keyword
    """
    devices = [InputDevice(path) for path in list_devices()]

    specific_keywords = ["rfid", "sycreader", "id&ic"]
    for device in devices:
        name_lower = device.name.lower()
        if any(keyword in name_lower for keyword in specific_keywords):
            return device

    broad_keywords = ["keyboard", "hid"]
    for device in devices:
        name_lower = device.name.lower()
        if any(keyword in name_lower for keyword in broad_keywords):
            print(f"คำเตือน: ใช้อุปกรณ์จากการค้นหาแบบกว้าง ({device.name}) "
                  f"อาจไม่ใช่เครื่องอ่าน RFID จริง")
            return device

    return None


def list_all_devices():
    """แสดงรายชื่ออุปกรณ์ input ทั้งหมดในเครื่อง เผื่อต้องหาชื่ออุปกรณ์เอง"""
    print("=== อุปกรณ์ input ทั้งหมดในเครื่อง ===")
    for path in list_devices():
        device = InputDevice(path)
        print(f"  path: {device.path}  |  name: {device.name}")


def read_card_uid(device):
    """
    วนอ่าน event จากอุปกรณ์ทีละตัว สะสมตัวเลขจนกว่าจะเจอ ENTER
    แล้ว return UID ที่อ่านได้ (เป็น string)
    """
    buffer = ""
    for event in device.read_loop():
        if event.type == ecodes.EV_KEY:
            data = categorize(event)
            if data.keystate == 1:  # keydown เท่านั้น (ไม่เอา keyup)
                key = data.keycode
                if key in KEYCODE_MAP:
                    if KEYCODE_MAP[key] == "ENTER":
                        if buffer:
                            uid = buffer
                            buffer = ""
                            return uid
                    else:
                        buffer += KEYCODE_MAP[key]


def simulate_bottle_session(student):
    """
    จำลองขั้นตอนรับขวด (ใช้แทนกล้อง+AI ไปก่อนชั่วคราว)
    พิมพ์ OK  -> จำลองว่าใส่ขวดถูกต้อง 1 ใบ (+5 แต้ม)
    พิมพ์ done -> จบรอบ กลับไปรอแตะบัตรใบถัดไป

    หมายเหตุ: ทีหลังตอนต่อกล้องจริง แค่แทนที่จุดที่เช็ค command == "ok"
    ด้วยผลลัพธ์จากโมเดล AI ตรวจจับขวด โครงสร้างที่เหลือใช้ต่อได้เลย
    """
    uid = student["rfid_uid"]
    points = student["points"]

    print(f"\n--- เริ่มรับขวดสำหรับ {student['nickname']} (แต้มปัจจุบัน: {points}) ---")
    print("พิมพ์ OK แล้ว Enter เพื่อจำลองใส่ขวด 1 ใบ (+5 แต้ม)")
    print("พิมพ์ done แล้ว Enter เพื่อจบรอบ\n")

    while True:
        command = input("> ").strip().lower()

        if command == "ok":
            updated = update_points(uid, 5)
            if updated:
                points = updated["points"]
                print(f"รับขวดสำเร็จ! +5 แต้ม (แต้มรวมตอนนี้: {points})")
            else:
                print("เกิดข้อผิดพลาดในการบันทึกแต้ม กรุณาลองใหม่")
        elif command == "done":
            print(f"--- จบรอบ แต้มรวมของ {student['nickname']}: {points} แต้ม ---\n")
            break
        else:
            print("คำสั่งไม่รู้จัก พิมพ์ OK เพื่อเพิ่มแต้ม หรือ done เพื่อจบรอบ")


def on_card_scanned(uid):
    """
    Callback ที่จะถูกเรียกทุกครั้งที่แตะบัตรสำเร็จ
    - ถ้าเจอในฐานข้อมูลแล้ว: แสดงชื่อเล่นและแต้มสะสม แล้วเข้าสู่โหมดรับขวด
    - ถ้ายังไม่เจอ: ให้กรอกข้อมูลเพื่อลงทะเบียนใหม่ แล้วเข้าสู่โหมดรับขวด
    """
    print(f"\nอ่านบัตรสำเร็จ! UID = {uid}")

    student = get_student_by_uid(uid)

    if student:
        print(f"ยินดีต้อนรับกลับ {student['nickname']}!")
        print(f"แต้มสะสมปัจจุบัน: {student['points']} แต้ม")
        simulate_bottle_session(student)
        return

    print("ยังไม่เคยลงทะเบียนบัตรใบนี้ กรุณากรอกข้อมูลเพื่อลงทะเบียน\n")
    student_code = input("รหัสนักเรียน: ").strip()
    first_name = input("ชื่อ: ").strip()
    last_name = input("นามสกุล: ").strip()
    nickname = input("ชื่อเล่น: ").strip()

    new_student = register_student(uid, student_code, first_name, last_name, nickname)

    if new_student:
        print(f"\nลงทะเบียนสำเร็จ! ยินดีต้อนรับ {new_student['nickname']}")
        print(f"แต้มเริ่มต้น: {new_student['points']} แต้ม")
        simulate_bottle_session(new_student)
    else:
        print("\nเกิดข้อผิดพลาดในการลงทะเบียน กรุณาลองใหม่อีกครั้ง")


def main():
    print("=== เครื่องมืออ่าน RFID Card ===\n")
    device = find_rfid_device()

    if device is None:
        print("ไม่พบอุปกรณ์ RFID reader โดยอัตโนมัติ")
        print("กรุณาดูรายชื่ออุปกรณ์ทั้งหมดด้านล่าง แล้วแก้ keyword ในฟังก์ชัน find_rfid_device()\n")
        list_all_devices()
        return

    print(f"พบอุปกรณ์: {device.name} ({device.path})")
    print("พร้อมอ่านบัตร... (กด Ctrl+C เพื่อออก)\n")

    try:
        while True:
            uid = read_card_uid(device)
            on_card_scanned(uid)
    except KeyboardInterrupt:
        print("\nปิดโปรแกรม")


if __name__ == "__main__":
    main()
