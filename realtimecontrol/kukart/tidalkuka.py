
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
    count: Optional[int] = None ) -> bool:
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

def checklimits(joint,angle,limits):
    if angle < limits[joint][0]:
        angle = limits[joint][0]
    if angle > limits[joint][1]:
        angle = limits[joint][1]
    return angle


class KukaState:
    """Manages all shared state variables for the robot."""
    def __init__(self):
        self.state = {  "robot": {},
                        "wandermode": 0,
                        "wanderspeed" :0,
                        "dynmode": 0,
                        "randomwristmode": 0,
                        "reachmode":0,
                        "dancemode":1,
                        "limitadjust1":5,
                        "limitadjust2":5,
                        "limits" : [(-94, 122), (-134,34), (-119,157), (-349,349), (-118,118), (-357,357)],
                        "playzone" : {"init": {
                                        "startpos": [  0, -90,  90,   0,  90,   0],
                                        "safezone": [(-94, 122), (-134,34), (-119,157), (-349,349), (-118,118), (-357,357)]
                                        },
                                       "wander": {
                                        "startpos": [60, -22.5, -112.5, 180, 45],
                                        "safezone" : [(-94, 122), (-134,34), (-119,157), (-349,349), (-118,118), (-357,357)]
                                       } 
                        },
                        "linmode": False,
                    #### poses ####
                        "linposes" : { "pos1" : [[855.209656, -577.229004, 1515.260742, 145.978836, -27.013065, -179.960007],
                                                 [500.209656, -527.229004, 1515.260742, 105.978836, 55.013065, 117.960007],
                                                 [1780.209656, -527.229004, 1515.260742, 105.978836, 55.013065, 117.960007],
                                                 [1750.209656, -527.229004, 1515.260742, 105.978836, 55.013065, 117.960007]]},
                        "poses" : {   
                                "init": [
                                    [  0, -90,  90,   0,  90,   0],  # home                  (baseline)
                                ],
                                "ch1": [
                                    [  0, -80,  90,   0,  80,   0],               
                                    [  0, -90,  90,   0,  40,   0],  
                                    [  0, -70,  90,   0,  60,   0],  
                                    [  0, -100,  90,   0,  20,   0],  
                                ]},

                        "dynposes":{
                                "dynwander": 
                                    {"dynjoints":[0], "dynlimits":[[-90,90]], "dynpose": [  0, -90,  90,   0,  90,   0]}
                                
                        },
                        "nextjpose":[  0, -90,  90,   0,  90,   0],
                        "prevjpose":[  0, -90,  90,   0,  90,   0],
                        "nextpose": [855.209656, -577.229004, 1515.260742, 145.978836, -27.013065, -179.960007],
                        "prevpose": [855.209656, -577.229004, 1515.260742, 145.978836, -27.013065, -179.960007],
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
        self.ch1poses = state.state["poses"]["ch1"]
        print(self.ch1poses)
        print("*"*80)
        # print(self.state.get("wandermode"))

    def __call__(self, event, data=None):
        message, deltatime = event
        self._wallclock += deltatime
        print("[%s] @%0.6f %r" % (self.port, self._wallclock, message))

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
            if message[1] == 29:
                self.state.update({"limitadjust1": message[2]})
            if message[1] == 49:
                self.state.update({"limitadjust2": message[2]})

        if message[0] == 144:
            self.state.update({"linmode":False})
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
            if message[1] == 61:
                self.state.update({"nextjpose":self.ch1poses[0]})
            if message[1] == 62:
                self.state.update({"nextjpose":self.ch1poses[1]})
            if message[1] == 63:
                self.state.update({"nextjpose":self.ch1poses[2]})
            if message[1] == 64:
                self.state.update({"nextjpose":self.ch1poses[3]})
        if message[0] == 145:
            self.state.update({"linmode":False})
            #channel 2 (tidal1)
            if message[1] == 61:
                self.state.update({"nextjpose":self.ch1poses[0]})
            if message[1] == 62:
                self.state.update({"nextjpose":self.ch1poses[1]})
            if message[1] == 63:
                self.state.update({"nextjpose":self.ch1poses[2]})
            if message[1] == 64:
                self.state.update({"nextjpose":self.ch1poses[3]})  
        if message[0] == 146:
            self.state.update({"linmode":False})

            a4 = random.randint(-349,349)
            a5 = random.randint(-118,118)
            pose = self.state.get("nextjpose")
            pose[3] = a4
            pose[4] = a5
            self.state.update({"nextjpose":pose})    
            print(pose)
        if message[0] == 147:
            self.state.update({"linmode":False})
            # move to a new pos, randomwalk
            pose = self.state.get("nextjpose")
            basepose = pose[0]
            limit = self.state.get("limitadjust1")
            basepose+=random.randint(-limit,limit)
            pose[0] = basepose
            if pose[0] < self.state.get("limits")[0][0]:
                pose[0] = self.state.get("limits")[0][0]
            if pose[0] > self.state.get("limits")[0][1]:
                pose[0] = self.state.get("limits")[0][1]   
            pose[4] = -(pose[2] + self.state.get("nextjpose")[1])
            pose[4] = checklimits(4,pose[4],self.state.get("limits")) 
            self.state.update({"nextjpose":pose})    
            print(pose)
        if message[0] == 148:
            self.state.update({"linmode":False})
            
            pose = self.state.get("nextjpose")
            basepose = pose[1]
            limit = self.state.get("limitadjust2")
            basepose+=random.randint(-limit,limit)
            pose[1] = basepose
            if pose[1] < self.state.get("limits")[1][0]:
                pose[1] = self.state.get("limits")[1][0]
            if pose[1] > self.state.get("limits")[1][1]:
                pose[1] = self.state.get("limits")[1][1]  
            pose[4] = -(pose[1] + self.state.get("nextjpose")[2])
            pose[4] = checklimits(4,pose[4],self.state.get("limits"))
            self.state.update({"nextjpose":pose})    
            print(pose)
        if message[0] == 149:
            if not self.state.get("linmode"):
                gotopos = [34.0, -104.0, 90.0, 0.0, 90.0, 216720.0]
                robot.move("joint", gotopos , 100)
                self.state.update({"linmode":True}) 
            else:         
                pose = self.state.get("linposes")["pos1"][random.randint(0,3)]
                print(pose)
                
                self.state.update({"nextpose":pose})    

        else:
            # print("unkown command")
            pass
        # print(self.state.state)




kukastate = KukaState()


robot = Robot(port=18735)
robot.connect()

def initKuka(robot):
    j = robot.get_curjpos()
    p = robot.get_curpos()  
    print(j)
    print(p)


async def kukaLoop(kukastate):

    initKuka(robot)
    state = kukastate.state
    count = 0
    while True:
        # if (comparelist(robot.get_curjpos(),state.get("nextjpose"),0.1,5)):
        # if (state.get("nextjpose"))
        # print(state.get("nextjpose"))
        # print(robot.get_curjpos())
        # count+=1
        # if (comparelist(robot.get_curjpos(), state.get("nextjpose"),0.1,5)):
        #     print("getting close")
        # else:
        #     print("stil moving")
        # print(count)
        if state.get("linmode"):
            # print(state)
            robot.move("pose", state.get("nextpose") , 100, linear=True) 
            pass  
        else:
            robot.move("joint", state.get("nextjpose") , 100)   
            # pass

def queue_handler():
    running = True
    while running:
        try:
            msg = kukastate_queue.get_nowait()
            # print(type(msg))
            kukastate.state.update(msg)
        except queue.Empty:
            time.sleep(0.01)



async def main():
    midiin = rtmidi.MidiIn()
    available_inports = midiin.get_ports()

    print(available_inports)

    midiin.open_port(0)
    port_name = available_inports[0]
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