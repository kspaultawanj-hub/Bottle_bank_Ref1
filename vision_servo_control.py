"""
ตรวจจับขวดด้วยโมเดล TensorFlow Lite (export จาก Teachable Machine)
แล้วสั่งงาน Servo Motor ตามผลการตรวจจับ พร้อม Cooldown กันหมุนถี่เกินไป

โครงสร้างการทำงาน:
1. โหลดโมเดล .tflite และรายชื่อคลาสจาก labels.txt (อ่านแบบไดนามิก ไม่ hardcode ลำดับ)
2. อ่านภาพจากกล้องแบบ real-time ด้วย Picamera2
3. ประมวลผลภาพ (resize + normalize) ให้ตรงกับ input ของโมเดล
4. รัน inference ได้ผลลัพธ์เป็นคลาส + ค่า confidence
5. ตัดสินใจสั่ง Servo ตามเงื่อนไข:
   - คลาสที่ตรวจพบอยู่ใน REJECT_CLASS_NAMES (มีฝา/มีฉลาก) และ confidence >= threshold
     -> หมุนขวา (ไม่ผ่าน)
   - คลาสอื่น (ไม่มีฝา ไม่มีฉลาก) หรือ confidence < threshold
     -> หมุนซ้าย (ผ่าน หรือ ไม่มั่นใจพอ)
6. มี cooldown ระหว่างการสั่ง servo แต่ละครั้ง กันหมุนสลับถี่เกินไปจากการตรวจจับพลาดชั่วขณะ

ติดตั้งก่อนใช้งาน:
    sudo apt install -y python3-picamera2
    pip3 install --break-system-packages tflite-runtime numpy pillow gpiozero

*** ต้องแก้ค่าคงที่ในส่วน CONFIG ด้านล่างให้ตรงกับอุปกรณ์จริงก่อนใช้งาน ***
โดยเฉพาะ SERVO_GPIO_PIN, MODEL_PATH, LABELS_PATH และ REJECT_CLASS_NAMES
(ชื่อคลาสใน REJECT_CLASS_NAMES ต้องสะกดตรงกับใน labels.txt เป๊ะๆ)
"""

import time

import numpy as np
from gpiozero import Servo
from PIL import Image
from picamera2 import Picamera2
from tflite_runtime.interpreter import Interpreter

# ========================= CONFIG (แก้ตรงนี้ตามอุปกรณ์จริง) =========================

MODEL_PATH = "model/model.tflite"      # path ไปยังไฟล์โมเดลที่ export จาก Teachable Machine
LABELS_PATH = "model/labels.txt"       # path ไปยังไฟล์รายชื่อคลาส

SERVO_GPIO_PIN = 17                    # ขา GPIO ที่ต่อสาย signal ของ servo (เปลี่ยนตามการต่อจริง)

# ตำแหน่ง servo ตามไลบรารี gpiozero (ช่วงค่า -1.0 ถึง 1.0)
# -1.0 = สุดซ้าย, 0.0 = กึ่งกลาง, 1.0 = สุดขวา
SERVO_RIGHT_POSITION = 1.0
SERVO_LEFT_POSITION = -1.0
SERVO_CENTER_POSITION = 0.0

SERVO_HOLD_SECONDS = 1.0               # เวลาที่ให้ servo ค้างอยู่ตำแหน่งซ้าย/ขวา ก่อนกลับ center

CONFIDENCE_THRESHOLD = 0.5             # ค่าความมั่นใจขั้นต่ำที่จะยอมรับผลตรวจจับ (0.0 - 1.0)

# รายชื่อคลาสที่ถือว่า "ไม่ผ่าน" (ยังมีฝา/มีฉลากติดอยู่) -> สั่งหมุนขวา
# ต้องสะกดให้ตรงกับชื่อคลาสใน labels.txt เป๊ะๆ แก้ตรงนี้ให้ตรงกับโมเดลจริงของคุณ
REJECT_CLASS_NAMES = {"has_cap", "has_label", "has_cap_label"}

DETECTION_COOLDOWN_SECONDS = 2.0       # เวลาหน่วงขั้นต่ำระหว่างการสั่ง servo แต่ละครั้ง

FRAME_DELAY_SECONDS = 0.3              # หน่วงเวลาระหว่างเฟรม ลดภาระ CPU

CAMERA_RESOLUTION = (640, 480)         # ความละเอียดภาพที่ดึงจากกล้อง

# ======================================================================================


def load_labels(labels_path):
    """
    อ่านไฟล์ labels.txt จาก Teachable Machine
    รูปแบบไฟล์แต่ละบรรทัดคือ "<index> <ชื่อคลาส>" เช่น "0 valid_bottle"
    return: list ของชื่อคลาส เรียงตาม index (index ของ list ตรงกับ index ของโมเดล)
    """
    labels = []
    with open(labels_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)  # ตัดเลข index นำหน้าออก เหลือแค่ชื่อคลาส
            name = parts[1] if len(parts) == 2 else parts[0]
            labels.append(name)
    return labels


def load_model(model_path):
    """
    โหลดโมเดล TensorFlow Lite และเตรียม interpreter ให้พร้อมรับภาพ
    return: (interpreter, input_details, output_details)
    """
    interpreter = Interpreter(model_path=model_path)
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()
    return interpreter, input_details, output_details


def preprocess_frame(frame, input_details):
    """
    แปลงภาพจากกล้อง (numpy array, RGB) ให้มีขนาด/ฟอร์แมตตรงกับที่โมเดลต้องการ

    Teachable Machine ปกติใช้ input ขนาด 224x224
    - โมเดลแบบ quantized (dtype = uint8): ใช้ค่าสี 0-255 ตรงๆ ไม่ต้อง normalize
    - โมเดลแบบ floating point (dtype = float32): ต้อง normalize เป็นช่วง 0.0-1.0
    """
    input_shape = input_details[0]["shape"]  # เช่น [1, 224, 224, 3]
    target_height, target_width = input_shape[1], input_shape[2]

    image = Image.fromarray(frame).resize((target_width, target_height))
    input_data = np.expand_dims(np.array(image), axis=0)

    if input_details[0]["dtype"] == np.float32:
        input_data = input_data.astype(np.float32) / 255.0

    return input_data


def run_inference(interpreter, input_details, output_details, input_data):
    """
    รันภาพเข้าโมเดล 1 ครั้ง แล้วคืนค่า (index ของคลาสที่ทายได้, ค่า confidence)
    """
    interpreter.set_tensor(input_details[0]["index"], input_data)
    interpreter.invoke()
    output_data = interpreter.get_tensor(output_details[0]["index"])[0]

    # โมเดล quantized จะคืนค่าเป็น uint8 (0-255) ต้อง normalize กลับเป็น 0.0-1.0
    if output_data.dtype == np.uint8:
        output_data = output_data.astype(np.float32) / 255.0

    predicted_index = int(np.argmax(output_data))
    confidence = float(output_data[predicted_index])
    return predicted_index, confidence


def move_servo_right(servo):
    """สั่ง servo หมุนไปทางขวา (กรณีตรวจพบว่าขวดยังมีฝา/ฉลาก - ไม่ผ่าน)"""
    print("  -> Servo หมุนขวา (ไม่ผ่าน: ยังมีฝา/ฉลาก)")
    servo.value = SERVO_RIGHT_POSITION
    time.sleep(SERVO_HOLD_SECONDS)
    servo.value = SERVO_CENTER_POSITION


def move_servo_left(servo):
    """สั่ง servo หมุนไปทางซ้าย (กรณีขวดผ่านเงื่อนไข หรือ confidence ต่ำเกินไป)"""
    print("  -> Servo หมุนซ้าย (ผ่าน/ไม่มั่นใจ)")
    servo.value = SERVO_LEFT_POSITION
    time.sleep(SERVO_HOLD_SECONDS)
    servo.value = SERVO_CENTER_POSITION


def decide_and_act(predicted_class, confidence, servo):
    """
    ตัดสินใจสั่งงาน servo ตามผลตรวจจับ

    เงื่อนไข (เรียงตามลำดับความสำคัญ):
    1. confidence ต่ำกว่า threshold -> ไม่มั่นใจพอ -> หมุนซ้าย
    2. คลาสที่ทายได้อยู่ใน REJECT_CLASS_NAMES (มีฝา/มีฉลาก) -> หมุนขวา
    3. นอกนั้น (ขวดผ่านเงื่อนไข ไม่มีฝา ไม่มีฉลาก) -> หมุนซ้าย
    """
    if confidence < CONFIDENCE_THRESHOLD:
        print(f"  Confidence ต่ำเกินไป ({confidence:.2f} < {CONFIDENCE_THRESHOLD})")
        move_servo_left(servo)
        return

    if predicted_class in REJECT_CLASS_NAMES:
        move_servo_right(servo)
    else:
        move_servo_left(servo)


def main():
    print("=== ระบบตรวจจับขวด + สั่งงาน Servo Motor ===\n")

    labels = load_labels(LABELS_PATH)
    interpreter, input_details, output_details = load_model(MODEL_PATH)

    servo = Servo(SERVO_GPIO_PIN)
    servo.value = SERVO_CENTER_POSITION

    picam2 = Picamera2()
    config = picam2.create_preview_configuration(main={"size": CAMERA_RESOLUTION})
    picam2.configure(config)
    picam2.start()
    time.sleep(1)  # รอกล้องปรับแสง

    print(f"โหลดโมเดลสำเร็จ | คลาสทั้งหมด: {labels}")
    print(f"คลาสที่ถือว่า 'ไม่ผ่าน': {REJECT_CLASS_NAMES}")
    print("เริ่มตรวจจับแบบ real-time... (กด Ctrl+C เพื่อออก)\n")

    last_action_time = 0.0

    try:
        while True:
            frame = picam2.capture_array()  # ภาพเป็น numpy array (RGB)

            input_data = preprocess_frame(frame, input_details)
            predicted_index, confidence = run_inference(
                interpreter, input_details, output_details, input_data
            )
            predicted_class = labels[predicted_index]

            print(f"ตรวจพบ: {predicted_class} (confidence: {confidence:.2f})")

            # เช็ค cooldown ก่อนสั่ง servo กันหมุนถี่เกินไปจากการตรวจจับพลาดชั่วขณะ
            now = time.time()
            if now - last_action_time >= DETECTION_COOLDOWN_SECONDS:
                decide_and_act(predicted_class, confidence, servo)
                last_action_time = now
            else:
                remaining = DETECTION_COOLDOWN_SECONDS - (now - last_action_time)
                print(f"  (cooldown อีก {remaining:.1f} วินาที ข้ามการสั่ง servo รอบนี้)")

            time.sleep(FRAME_DELAY_SECONDS)

    except KeyboardInterrupt:
        print("\nปิดโปรแกรม")
    finally:
        servo.value = SERVO_CENTER_POSITION
        picam2.stop()


if __name__ == "__main__":
    main()
