
import sys
import os
import json
import asyncio
from itertools import product as iproduct
from threading import Thread
from typing import List, Optional
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation as _Rot
import rtmidi
import time
import queue
import random

# sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from kukapy.robot import Robot
# wandermode = 0  # 0 == bored, 1 == active
kukastate_queue = queue.Queue()


def comparelist(
    list1: List[float], 
    list2: List[float], 
    margin: float, 
    count: Optional[int] = None
) -> bool:
    """
    Compares two lists element by element, checking if they are all within a margin.

    Args:
        list1: The first list of numbers.
        list2: The second list of numbers.
        margin: The allowed tolerance/margin (a positive float).
        count: (Optional) The number of initial elements to compare. 
               If None, the entire lists are compared.

    Returns:
        True if all compared pairs are within the margin, False otherwise.
    """
    
    # 1. Determine the effective comparison length (N)
    if count is None:
        N = len(list1)
    else:
        N = count

    # 2. Check for list length compatibility
    if len(list1) < N or len(list2) < N:
        # If either list is shorter than the requested count, they cannot be compared fully.
        print(f"Warning: Requested count ({N}) exceeds the length of one or both lists.")
        return False # Or raise an error, depending on desired strictness

    # 3. Slice the lists to compare only the first N elements
    slice1 = list1[:N]
    slice2 = list2[:N]
    
    # 4. Perform the comparison using zip and all()
    return all(abs(a - b) <= margin for a, b in zip(slice1, slice2))


class KukaState:
    """Manages all shared state variables for the robot."""
    def __init__(self):
        self.state = {  "robot": {},
                        "wandermode": 0,
                        "wanderspeed" :0,
                        "dynmode": 0,
                        "randomwristmode": 0,
                        "reachmode":0,
                    #### poses ####
                        "poses" : {   
                                "init": [
                                    [  0, -90,  90,   0,  90,   0],  # home                  (baseline)
                                ],
                                "wander": [
                                    [  -90, -90,  90,   0,  90,   0],  # a1                  (baseline)
                                    [  90, -90,  90,   0,  90,   0],  # a1                  (baseline)
                                ]},

                        "dynposes":{
                                "dynwander": 
                                    {"dynjoints":[0], "dynlimits":[[-90,90]], "dynpose": [  0, -90,  90,   0,  90,   0]}
                                
                        },
                        "nextjpose":[  0, -90,  90,   0,  90,   0],
                        "prevjpose":[  0, -90,  90,   0,  90,   0]
                    } 

    def update(self, msg):
        kukastate_queue.put(msg)
        # self.state[key] = val

    def get(self, key):
        return self.state[key]

class MidiInputHandler:
    def __init__(self, port, state):
        self.port = port
        self._wallclock = time.time()
        self.state = state
        # print(self.state.get("wandermode"))

    def __call__(self, event, data=None):
        message, deltatime = event
        self._wallclock += deltatime
        # print("[%s] @%0.6f %r" % (self.port, self._wallclock, message))

        if message[0] == 176:
            if message[1] == 13:
                # print(self.state["dynposes"]["dynwander"]["dynjoints"])
                vel = (message[2]-64)/8
                # print("setting pose vel")
                # print(vel)
                dynp = self.state.get("dynposes")
                for n,i in enumerate(dynp["dynwander"]["dynjoints"]):
                    # print(n,i)
                    # print(self.state["dynposes"]["dynwander"]["dynpose"])
                    j = dynp["dynwander"]["dynpose"][i]
                    j = j+vel
                    if j > dynp["dynwander"]["dynlimits"][n][1]:
                        dynp["dynwander"]["dynpose"][i] = dynp["dynwander"]["dynlimits"][n][1]
                    elif j < dynp["dynwander"]["dynlimits"][n][0]:
                        dynp["dynwander"]["dynpose"][i] = dynp["dynwander"]["dynlimits"][n][0]
                    else :
                        dynp["dynwander"]["dynpose"][i] = j
                    self.state.update(dynp)
                    print(self.state.state)
        if message[0] == 144:
            if message[1] == 41:
                if (self.state.get("wandermode") == 0):
                    print("wandermode = 1")
                    wmode = {"wandermode": 1}
                else:
                    print("wandermode = 0")
                    wmode = {"wandermode": 0}
                self.state.update(wmode)
            if message[1] == 73:
                if (self.state.get("dynmode") == 0):
                    print("dynmode = 1")
                    dmode = {"dynmode": 1}
                else:
                    print("dynmode = 0")
                    dmode = {"dynmode": 0}
                self.state.update(dmode)
            if message[1] == 42:
                if (self.state.get("randomwristmode") == 0):
                    print("randomwristmode = 1")
                    rmode = {"randomwristmode": 1}
                else:
                    print("randomwristmode = 0")
                    rmode = {"randomwristmode": 0}
                self.state.update(rmode)
            if message[1] == 74:
                if (self.state.get("reachmode") == 0):
                    print("reachmode = 1")
                    rmode = {"reachmode": 1}
                else:
                    print("reachmode = 0")
                    rmode = {"reachmode": 0}
                self.state.update(rmode)
            else:
                print("unkown command")
            print(self.state.state)


kukastate = KukaState()


robot = Robot(port=18735)
robot.connect()

def initKuka(robot):
    j = robot.get_curjpos()
    p = robot.get_curpos()  
    print(j)
    print(p)

    # for i, joints in enumerate(poses):
    #     print(f"  Pose {i+1}/{len(poses)}: {joints}")
    #     robot.move("joint", joints, velocity=_VELOCITY)
    #     j = robot.get_curjpos()
    #     p = robot.get_curpos()
    #     data.append((j, p))
    #     print(f"    Joints: {[round(v,2) for v in j]}")
    #     print(f"    TCP   : X={p[0]:.1f}  Y={p[1]:.1f}  Z={p[2]:.1f}\n")
    # print("  Returning home...")
    # robot.move("joint", poses[0], velocity=_VELOCITY)



async def kukaLoop(kukastate):

    initKuka(robot)
    state = kukastate.state
    while True:
        # print(robot.get_curjpos())
        # print(state.get("nextjpose"))
        # print(comparelist(robot.get_curjpos(),state.get("nextjpose"),0.1,5))
        # if (comparelist(robot.get_curjpos(),state.get("nextjpose"),0.1,5)):
            if (state["wandermode"] == 0):
                print("about to move robot home")
                state.update({"nextjpose": state["poses"]["init"][0]})
                # robot.move("joint", , 100)

                # print(kukastate.state["wandermode"])
            if (state["wandermode"] == 1):
                print("wandermode on")
                # if (state["dynmode"] == 1):
                

                if (state["dynmode"] == 0):
                    print("not dynamic")
                    jpos = robot.get_curjpos()[0]
                    print(jpos)
                    print("="*80)
                    print(state["poses"]["wander"][1][0])
                    if jpos == state["poses"]["wander"][1][0]:
                        state.update({"nextjpose": state["poses"]["wander"][0]})
                    if jpos == state["poses"]["wander"][0][0]:
                        state.update({"nextjpose": state["poses"]["wander"][1]})
                        # robot.move("joint", state["poses"]["wander"][1], 100)
                    else:
                        state.update({"nextjpose": state["poses"]["wander"][1]})
                        # robot.move("joint", state["poses"]["wander"][1], 100)

                if (state["dynmode"] == 1):
                    print("dynamic mode")
                    print(state["dynposes"]["dynwander"]["dynpose"])
                    state.update({"nextjpose": state["dynposes"]["dynwander"]["dynpose"]})
            if (state["reachmode"] == 1):
                pose = state.get("nextjpose")
                pose[1] =  -30 # A2 
                pose[2] =  20 # A3
                state.update({"nextjpose":pose})
            if (state["reachmode"] == 0):
                pose = state.get("nextjpose")
                pose[1] =  -90 # A2 
                pose[2] =  90 # A3
                state.update({"nextjpose":pose})


            if (state["randomwristmode"] == 1):
                print("doing random wrist")
                # calc randowm wrist
                ## A4 -350, 350
                ## A5 -119, 119
                a4 = random.randint(-350,350)
                a5 = random.randint(-119,119)
                pose = state.get("nextjpose")
                pose[3] = a4
                pose[4] = a5
                state.update({"nextjpose":pose})
           
            robot.move("joint", state.get("nextjpose") , 100)
        # else:
        #     # positions are not equal, we're either moving or stuck
        #     if (state.get("prevjpos") != robot.get_curjpos()):  # check to see if we're stuck
        #         robot.move("joint", state["poses"]["init"][0] , 100)

        #     prevpos = robot.get_curjpos() 
        #     state.update({"prevpos":prevpos})

def queue_handler():
    running = True
    while running:
        try:
            msg = kukastate_queue.get_nowait()
            print(type(msg))
            kukastate.state.update(msg)
        except queue.Empty:
            time.sleep(0.01)



async def main():
    midiin = rtmidi.MidiIn()
    available_inports = midiin.get_ports()

    print(available_inports)

    midiin.open_port(1)
    port_name = available_inports[1]
    kukaDaemon = Thread(target=asyncio.run , args=(kukaLoop(kukastate),), daemon=True, name='kukaLoop')
    kukaDaemon.start()
    print("Attaching MIDI input callback handler.")
    midiin.set_callback(MidiInputHandler(port_name, kukastate))



    try:
        queue_handler()
    except KeyboardInterrupt:
        print('killed by keyboard')
    finally:
        print("closing") 
        midiin.close_port()
        kukastate.state["robot"].disconnect()
        del midiin

print("Entering main loop. Press Control-C to exit.")
if __name__ == "__main__":
    asyncio.run(main())