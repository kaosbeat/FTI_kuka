from kukapy.robot import Robot

robot = Robot(port=18735)
robot.connect()          # blocks until KRC connects in

print(robot.ping())                          # → 'pong'
print(robot.get_curjpos())                   # [A1, A2, A3, A4, A5, A6] in degrees
print(robot.get_curpos())                    # [X, Y, Z, A, B, C] in mm / degrees

robot.move("joint", [0, -90, 90, 0, 90, 0], velocity=20)   # PTP joint move
robot.move("pose",  [500, 0, 800, 0, 0, 0], velocity=10, linear=True)  # LIN move

robot.set_do(1, True)                        # set digital output 1 high
print(robot.get_do(1))                       # read digital output 1

robot.disconnect()