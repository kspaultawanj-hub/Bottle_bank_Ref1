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

# แผนที่คีย์โค้ดตัวเลข (KEY_1 ... KEY_0) เป็นตัวอักษร
KEYCODE_MAP = {
    "KEY_1": "1", "KEY_2": "2", "KEY_3": "3", "KEY_4": "4", "KEY_5": "5",
    "KEY_6": "6", "KEY_7": "7", "KEY_8": "8", "KEY_9": "9", "KEY_0": "0",
    "KEY_ENTER": "ENTER",
}


def find_rfid_device():
    """
    ค้นหาอุปกรณ์ RFID reader อัตโนมัติจากรายชื่ออุปกรณ์ input ทั้งหมด
    เครื่องอ่าน RFID มักมีชื่อประมาณ 'HID Keyboard' หรือระบุยี่ห้อ/รุ่น
    ถ้าหาไม่เจอ ให้รันฟังก์ชัน list_all_devices() เพื่อดูชื่อจริงแล้วแก้ keyword
    """
    devices = [InputDevice(path) for path in list_devices()]
    for device in devices:
        name_lower = device.name.lower()
        if "keyboard" in name_lower or "rfid" in name_lower or "hid" in name_lower:
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


def on_card_scanned(uid):
    """
    Callback ที่จะถูกเรียกทุกครั้งที่แตะบัตรสำเร็จ
    ตรงนี้คือจุดที่จะเชื่อมต่อกับ Supabase ในขั้นตอนถัดไป
    """
    print(f"อ่านบัตรสำเร็จ! UID = {uid}")
    # TODO: เรียกฟังก์ชันเช็ค/ลงทะเบียนกับ Supabase ตรงนี้ในบทเรียนถัดไป
    # เช่น: check_or_register_student(uid)


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
