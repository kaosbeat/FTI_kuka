from kukapy.robot import Robot

# Initialize the robot connection
robot = Robot(ip="192.168.10.201", port=12345)

# Move the robot to a specified position (XYZ coordinates + Quaternion)
# robot.move_to([500, 0, 300], [0, 1, 0, 0])

# Retrieve the current position
pos = robot.get_position()
print("Current Position:", pos)

# Disconnect from the robot
robot.disconnect()