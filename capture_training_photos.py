"""
สคริปต์ถ่ายรูปสำหรับเก็บชุดข้อมูล (Dataset) เพื่อเทรนโมเดลตรวจจับขวด
ใช้กับ Raspberry Pi 4 + Pi Camera (ต้องติดตั้ง picamera2 มาแล้ว)

วิธีใช้:
1. รันสคริปต์ แล้วพิมพ์ชื่อคลาสที่จะถ่าย เช่น valid_bottle, has_cap, has_label, not_bottle
2. กด SPACE เพื่อถ่ายรูป, กด Q เพื่อออกจากคลาสปัจจุบัน
3. รูปจะถูกเก็บไว้ในโฟลเดอร์ dataset/<ชื่อคลาส>/
4. ทำซ้ำจนกว่าจะครบทุกคลาส (แนะนำ 100-150 รูปต่อคลาส)

ติดตั้ง dependency ที่ต้องใช้ (ถ้ายังไม่มี):
    sudo apt install -y python3-picamera2 python3-opencv
"""

import os
import time
from datetime import datetime

import cv2
from picamera2 import Picamera2

DATASET_DIR = "dataset"


def get_next_index(folder):
    existing = [f for f in os.listdir(folder) if f.endswith(".jpg")]
    return len(existing)


def capture_for_class(picam2, class_name):
    folder = os.path.join(DATASET_DIR, class_name)
    os.makedirs(folder, exist_ok=True)
    count = get_next_index(folder)

    print(f"\n=== กำลังถ่ายรูปคลาส: {class_name} ===")
    print("กด SPACE เพื่อถ่ายรูป | กด Q เพื่อออกจากคลาสนี้\n")

    while True:
        frame = picam2.capture_array()
        preview = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        cv2.putText(
            preview,
            f"{class_name} | count: {count}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 255, 0),
            2,
        )
        cv2.imshow("Capture - SPACE=save, Q=quit class", preview)

        key = cv2.waitKey(1) & 0xFF
        if key == ord(" "):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S%f")
            filename = os.path.join(folder, f"{class_name}_{timestamp}.jpg")
            cv2.imwrite(filename, preview)
            count += 1
            print(f"บันทึกแล้ว: {filename} (รวม {count} รูป)")
        elif key == ord("q"):
            break


def main():
    picam2 = Picamera2()
    config = picam2.create_preview_configuration(main={"size": (640, 480)})
    picam2.configure(config)
    picam2.start()
    time.sleep(1)  # รอกล้องปรับแสง

    print("=== เครื่องมือเก็บรูปสำหรับเทรนโมเดล ===")
    print("คลาสแนะนำ: valid_bottle, has_cap, has_label, not_bottle")

    try:
        while True:
            class_name = input(
                "\nพิมพ์ชื่อคลาสที่จะถ่าย (หรือพิมพ์ 'exit' เพื่อจบโปรแกรม): "
            ).strip()
            if class_name.lower() == "exit":
                break
            if not class_name:
                continue
            capture_for_class(picam2, class_name)
    finally:
        cv2.destroyAllWindows()
        picam2.stop()
        print("\nจบการเก็บข้อมูล ไฟล์ทั้งหมดอยู่ในโฟลเดอร์ dataset/")


if __name__ == "__main__":
    main()
