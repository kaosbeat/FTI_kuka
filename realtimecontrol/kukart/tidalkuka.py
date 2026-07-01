
import sys
import os
import json
import asyncio
from itertools import product as iproduct
from threading import Thread
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation as _Rot
import rtmidi
import time
import queue
import random
from robotstates import kukastate
from robothelpers import *  
# sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from kukapy.robot import Robot
# wandermode = 0  # 0 == bored, 1 == active
kukastate_queue = queue.Queue()



class KukaState:
    """Manages all shared state variables for the robot."""
    def __init__(self, robot):
        self.state = kukastate
        self.state["robot"] = robot
        

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
            if message[1] == 1: 
                if message[2] == 1: # conditions
                    print("init")
                    activateZone("init", self.state)
                if message[2] == 2:
                    print("rest")
                    activateZone("rest", self.state)
                if message[2] == 3:
                    print("wakeup")
                    activateZone("wakeup", self.state)
                if message[2] == 4:
                    print("stretch")
                    activateZone("stretch", self.state)
                if message[2] == 5:
                    print("wander")
                    activateZone("wander", self.state)

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
                self.state.update({"nextjpos":self.ch1poses[0]})
            if message[1] == 62:
                self.state.update({"nextjpos":self.ch1poses[1]})
            if message[1] == 63:
                self.state.update({"nextjpos":self.ch1poses[2]})
            if message[1] == 64:
                self.state.update({"nextjpos":self.ch1poses[3]})
        if message[0] == 145:
            self.state.update({"linmode":False})
            #channel 2 (tidal1)
            if message[1] == 61:
                self.state.update({"nextjpos":self.ch1poses[0]})
            if message[1] == 62:
                self.state.update({"nextjpos":self.ch1poses[1]})
            if message[1] == 63:
                self.state.update({"nextjpos":self.ch1poses[2]})
            if message[1] == 64:
                self.state.update({"nextjpos":self.ch1poses[3]})  
        if message[0] == 146:
            #channel 3 (tidal2)
            self.state.update({"linmode":False})

            a4 = random.randint(-349,349)
            a5 = random.randint(-118,118)
            pose = self.state.get("nextjpos")
            pose[3] = a4
            pose[4] = a5
            self.state.update({"nextjpos":pose})    
            print(pose)
        if message[0] == 147:
            #channel 4 (tidal3)
            self.state.update({"linmode":False})
            # move to a new pos, randomwalk
            pose = self.state.get("nextjpos")
            basepose = pose[0]
            limit = self.state.get("limitadjust1")
            basepose+=random.randint(-limit,limit)
            pose[0] = basepose
            if pose[0] < self.state.get("limits")[0][0]:
                pose[0] = self.state.get("limits")[0][0]
            if pose[0] > self.state.get("limits")[0][1]:
                pose[0] = self.state.get("limits")[0][1]   
            pose[4] = -(pose[2] + self.state.get("nextjpos")[1])
            pose[4] = fitlimits(4,pose[4],self.state.get("limits")) 
            self.state.update({"nextjpos":pose})    
            print(pose)
        if message[0] == 148:
            #channel 5 (tidal4)
            self.state.update({"linmode":False})
            pose = self.state.get("nextjpos")
            basepose = pose[1]
            limit = self.state.get("limitadjust2")
            basepose+=random.randint(-limit,limit)
            pose[1] = basepose
            if pose[1] < self.state.get("limits")[1][0]:
                pose[1] = self.state.get("limits")[1][0]
            if pose[1] > self.state.get("limits")[1][1]:
                pose[1] = self.state.get("limits")[1][1]  
            pose[4] = -(pose[1] + self.state.get("nextjpos")[2])
            pose[4] = fitlimits(4,pose[4],self.state.get("limits"))
            self.state.update({"nextjpos":pose})    
            print(pose)
        if message[0] == 149:
            #channel 4 (tidal5)
            if not self.state.get("linmode"):
                gotopos = [34.0, -104.0, 90.0, 0.0, 90.0, 216720.0]
                robot.move("joint", gotopos , 100)
                self.state.update({"linmode":True}) 
            else:         
                pose = self.state.get("linposes")["pos1"][random.randint(0,3)]
                print(pose)
                
                self.state.update({"nextpos":pose})    

        else:
            # print("unkown command")
            pass
        # print(self.state.state)




robot = Robot(port=18735)
robot.connect()
kukastate = KukaState(robot)


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
        # if (comparelist(robot.get_curjpos(),state.get("nextjpos"),0.1,5)):
        # if (state.get("nextjpos"))
        # print(state.get("nextjpos"))
        # print(robot.get_curjpos())
        # count+=1
        # if (comparelist(robot.get_curjpos(), state.get("nextjpos"),0.1,5)):
        #     print("getting close")
        # else:
        #     print("stil moving")
        # print(count)
        state.update({"currentjpose":robot.get_curjpos()})
        state.update({"currentpose":robot.get_curpos()})
        if state.get("linmode"):
            # print(state)
            robot.move("pose", state.get("nextpos") , 100, linear=True) 
            pass  
        else:
            robot.move("joint", state.get("nextjpos") , 100)   
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