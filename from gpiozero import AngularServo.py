from gpiozero import AngularServo
from time import sleep

servo = AngularServo(
    17,
    min_angle=0,
    max_angle=180
)

# ไปที่ 0 องศา
servo.angle = 0

sleep(2)