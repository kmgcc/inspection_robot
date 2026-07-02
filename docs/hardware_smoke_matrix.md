# Hardware Smoke Matrix

Run this on the car before the final demo:

```bash
sh /home/pi/project_demo/raspbot/killprocess.sh
```

`LOCAL_NOT_RUN` means the software hook is implemented and can be imported locally, but the physical RASPBOT hardware has not been exercised from this workstation.

| Item | Example / Command | Result | Notes |
|---|---|---|---|
| Camera | `ls /dev/video*` and `CAMERA_DEVICE=0 python3 scripts/test_side_camera_tag_on_car.py` | LOCAL_NOT_RUN | Side camera should be mounted facing the shelf side. |
| AprilTag TAG36H11 | `python3 scripts/test_side_camera_tag_on_car.py` | LOCAL_NOT_RUN | Uses `dt_apriltags.Detector(families="tag36h11")`; fallback is the official ROS2 AprilTag node. |
| OCR shelf label | `python3 scripts/test_side_camera_tag_on_car.py` with printed shelf label above tag | LOCAL_NOT_RUN | OCR is optional `pytesseract`; AprilTag remains the primary identity. |
| Color block detection | `python3 scripts/test_side_camera_tag_on_car.py` with item color block | LOCAL_NOT_RUN | Color evidence is computed from the tag-centered image crop. |
| Simple image/template detection | `python3 scripts/test_side_camera_tag_on_car.py` with item label | LOCAL_NOT_RUN | Current first version reports `image_class=null`; 2.1/vision extension can add template classes. |
| Mecanum forward/backward | `python3 -c "from inspection_robot.robot import motion; motion.move_forward_slow(); motion.move_backward_slow()"` | LOCAL_NOT_RUN | Calls vendor `McLumk_Wheel_Sports.move_forward/move_backward` at conservative speed. |
| Mecanum strafe | `python3 -c "from inspection_robot.robot import motion; motion.strafe_left_slow(); motion.strafe_right_slow()"` | LOCAL_NOT_RUN | Calls vendor `move_left/move_right`. |
| Mecanum rotation | `python3 -c "from inspection_robot.robot import motion; motion.rotate_left_slow(); motion.rotate_right_slow()"` | LOCAL_NOT_RUN | Calls vendor `rotate_left/rotate_right`. |
| Ultrasonic | `python3 scripts/test_obstacle_avoidance_on_car.py` | LOCAL_NOT_RUN | `blocked_distance_mm=200`, `clear_distance_mm=280`, three blocked samples to confirm. |
| Line sensor / tape boundary | `python3 scripts/test_tape_boundary_on_car.py` | LOCAL_NOT_RUN | Four-channel order is `left, left-center, right-center, right`; official examples use `0` for black tape. |
| Buzzer | `python3 scripts/test_obstacle_avoidance_on_car.py` | LOCAL_NOT_RUN | Uses `Raspbot.Ctrl_BEEP_Switch`. |
| RGB | `python3 scripts/test_obstacle_avoidance_on_car.py` | LOCAL_NOT_RUN | Uses `Raspbot.Ctrl_WQ2812_ALL`; green=normal, yellow=obstacle, purple=warning, red=high priority. |

## Fallback Notes

- `McLumk_Wheel_Sports` is imported only when a motion function runs. If it cannot import on the car, verify the Yahboom example notebooks are run from the vendor Python environment.
- `Raspbot_Lib` is imported only when reading sensors or using buzzer/RGB. If ultrasonic or line sensor reads fail, run the official basic examples first to confirm I2C access.
- `dt_apriltags` and OpenCV are imported only by the side-camera detector. If they are not available, use `ros2 run yahboomcar_apriltag apriltag_identify` in the official Docker environment as the AprilTag source.
- Shelf labels should be placed with the human-readable shelf id above the AprilTag so the side camera can see both in one frame.
- Black tape is a forbidden-zone safety boundary, not the main line-following path.
