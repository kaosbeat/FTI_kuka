
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
        # print("[%s] @%0.6f %r" % (self.port, self._wallclock, message))

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
                if message[2] == 6:
                    print("wildwander")
                    activateZone("wildwander", self.state)

            if message[1] == 2: # action mode
                if message[2] == 0:
                    self.state.update(({"actionmode" :False}))
                    self.state.update(({"currentaction" :None}))

                if message[2] == 1:
                    self.state.update(({"actionmode" :True}))
                    currentzone = self.state.get("currentzone")
                    currentaction = self.state.get("zones")[curren:currentactiontzone]["actions"].keys()[0]
                    self.state.update(({"currentaction" :currentaction}))
                    

                

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
            if message[1] == 20:
                limitadjust = self.state.get("limitadjust")
                limitadjust[0] = message[2]/4
                self.state.update({"limitadjust":limitadjust})
            if message[1] == 21:
                limitadjust = self.state.get("limitadjust")
                limitadjust[1] = message[2]/4
                self.state.update({"limitadjust":limitadjust})
            if message[1] == 22:
                limitadjust = self.state.get("limitadjust")
                limitadjust[2] = message[2]/4
                self.state.update({"limitadjust":limitadjust})

            if message[1] == 30:
                if message[2] == 1:
                    randommode = True
                if message[2] == 2:
                    randommode = False
                self.state.update({"randommode":randommode })

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
            #channel 6 (tidal5)
            if not self.state.get("linmode"):
                gotopos = [34.0, -104.0, 90.0, 0.0, 90.0, 216720.0]
                robot.move("joint", gotopos , 100)
                self.state.update({"linmode":True}) 
            else:         
                pose = self.state.get("linposes")["pos1"][random.randint(0,3)]
                print(pose)
                
                self.state.update({"nextpos":pose})   
        if message[0] == 150:
            #channel 7 (tidal6)
            

            if (not self.state.get("modechange")  and not self.state.get("actionmode")):
                # move all joints within limits of mode 
                # get mode
                pose = self.state.get("curjpos")
                currentzone = self.state.get("currentzone")
                # print(currentzone, pose)
                # print(self.state.get("zones")[currentzone]["safezone"])
                for i,jointlimit in enumerate(self.state.get("zones")[currentzone]["safezone"]):
                    # print(" joint ", i, " limit", jointlimit)
                    if self.state.get("actionmode"):
                        currentaction = self.state.get("currentaction")
                        actionindex = self.state.get("actionindex")
                        actionlength = len(self.state.get("zones")[currentzone]["actions"][currentaction])
                        pose[i] = self.state.get("zones")[currentzone]["actions"][currentaction][actionindex][i]
                        actionindex+=1
                        if actionindex >= actionlength-1:
                            actionindex = 0
                        self.state.update({"actionindex":actionindex})

                    elif self.state.get("randommode"):
                        joint = random.randint(jointlimit[0], jointlimit[1])
                        pose[i] = joint
                    # elif self.state.get("actionmode"): 
                    #     currentaction = self.state.get("currentaction") 
                    #     if currentaction == currentaction None:
                    #         currentaction = self.state.get("zones")[currentzone]["actions"].keys()[0]
                        
                    else:
                        # print(self.state.get("limitadjust"))
                        for i in range(3):
                            val = (0.5 - random.random())*self.state.get("limitadjust")[i]
                            pose[i]=pose[i] + val
                            pose[i] = fitlimits(i,pose[i],self.state.get("zones")[currentzone]["safezone"])
                        # pass
                pose[4] = -(pose[1] + pose[2])
                pose[4] = fitlimits(4,pose[4],self.state.get("zones")[currentzone]["safezone"])
                # a5_required = calculate_away_and_horizon_angle(pose[0], pose[1],pose[2], pose[3], 1, 1)
                # pose[4] = a5_required
                self.state.update({"nextjpos":pose})
            if (not self.state.get("modechange")  and self.state.get("actionmode")):
                currentzone = self.state.get("currentzone")
                pose = self.state.get("curjpos")



            


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
        state.update({"curjpos":robot.get_curjpos()})
        state.update({"curpos":robot.get_curpos()})
        if state.get("linmode"):
            # print(state)
            robot.move("pose", state.get("nextpos") , 100, linear=True) 
            pass  
        else:
            robot.move("joint", state.get("nextjpos") , state.get("speed"))   
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
    port_name = 'Midi Through:Midi Through Port-0 14:0'
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