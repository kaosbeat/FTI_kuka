from typing import List, Optional
import numpy as np


def rotation_matrix(axis, theta):
    c, s = np.cos(theta), np.sin(theta)
    if axis == 'x': return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == 'y': return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    if axis == 'z': return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])

def get_wrist_position(q1, q2, q3, L1, L2):
    """ 
    Simplified FK to find wrist position (x, y, z).
    L1, L2 are the lengths of the upper arm and forearm.
    """
    # Distance from base center to wrist in the XY plane
    r = L1 * np.cos(q2) + L2 * np.cos(q2 + q3)
    x = r * np.cos(q1)
    y = r * np.sin(q1)
    z = L1 * np.sin(q2) + L2 * np.sin(q2 + q3)
    return np.array([x, y, z])

def calculate_away_and_horizon_angle(q1, q2, q3, q4, L1, L2):

    # 1. Find where the wrist is in space
    p_wrist = get_wrist_position(q1, q2, q3, L1, L2)
    # print(p_wrist)
    # 2. Define Target Vector: Pointing from base to wrist, but flattened to Z=0
    v_target = np.array([p_wrist[0], p_wrist[1], 0])
    v_target = v_target / np.linalg.norm(v_target) # Normalize
    
    # 3. Get current tool orientation (FK rotation up to axis 4)
    R0_4 = rotation_matrix('z', q1) @ rotation_matrix('y', q2) @ \
           rotation_matrix('y', q3) @ rotation_matrix('x', q4)
    
    v_tool = R0_4[:, 2] # The local Z-axis of the tool
    
    # 4. Calculate the angle between current tool vector and target vector
    # We use the dot product: cos(theta) = (A . B) / (|A||B|)
    dot_product = np.dot(v_tool, v_target)
    angle_diff = np.arccos(np.clip(dot_product, -1.0, 1.0))
    
    # Determine direction of rotation (up or down)
    # We check the Z component of the tool to see if we need to rotate + or -
    direction = -1 if v_tool[2] > 0 else 1
    print(np.rad2deg(angle_diff * direction))
    return np.rad2deg(angle_diff * direction)


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
    print("+"*80)
    print("comparing:")
    print(list1)
    print(list2)
    print("+"*80)
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

def fitlimits(joint,angle,limits):
    """
    check if a joint angle is withing the limits
    joint 0 - 5
    angle -360 - 360 
    limits array of joint limit tuples eg [(-94, 122), (-134,34), (-119,157), (-349,349), (-118,118), (-357,357)]  
    eg limits: self.state.get("limits") or self.state.get("zones")["init"]["safezone"]
    """
    if angle < limits[joint][0]:
        angle = limits[joint][0]
    if angle > limits[joint][1]:
        angle = limits[joint][1]
    return angle

def checklimits(joint,angle,limits):
    """
    check if a joint angle is withing the limits
    joint 0 - 5
    angle -360 - 360 
    limits array of joint limit tuples eg [(-94, 122), (-134,34), (-119,157), (-349,349), (-118,118), (-357,357)]  
    eg limits: self.state.get("limits") or self.state.get("zones")["init"]["safezone"]
    """
    if angle < limits[joint][0]:
        return False
    elif angle > limits[joint][1]:
        return False
    else:
        return True

def posSafe(pos, limits):
    # print(limits)
    for i,p in enumerate(pos):
        # print(i,p)
        if i < 5: # A6 is always out of limits
            if (not (checklimits(i, p, limits))):
                return False
    return True



def activateZone(zone, state):
    # robot = state.get("robot")
    # print(robot)
    # get current condition:
    currentzone = state.get("currentzone")
    print("currentzone= ", currentzone)
    # print(state.get("zones")[zone]["safezone"])
    # block state changes
    state.update({"modechange":True})
    # get current pos    
    print(posSafe(state.get("curjpos"), state.get("zones")[zone]["safezone"]))
    if (not posSafe(state.get("curjpos"), state.get("zones")[zone]["safezone"])): # is current pos in safe zone of new limits?
        print(" not in safezone ")
        pose = state.get("zones")[currentzone]["exitpos"] # goto exit pos 
        state.update({"nextjpos":pose}) 
        # while (not (comparelist(state.get("curjpos"), state.get("nextjpos"),0.1,5))):
            # print("moving to exit position")
            # robot.move("joint", state.get("nextjpos") , 100)   
        print("moved to exit position")
        # if (not posSafe(state.get("curjpos"), state.get("zones")[zone]["safezone"])): # is current pos in safe zone?
        #     pose = state.get("zones")[currentzone]["exitpos"] # goto neutral pos 
        #     state.update({"nextjpos":pose}) 
        #     # while (not (comparelist(state.get("curjpos"), state.get("nextjpos"),0.1,5))):
        #     #     print("moving to neutral position")
        #         # robot.move("joint", state.get("nextjpos") , 100) 
        #     print("moved to neutral position")
              
    print("safezone exit possible")
    # yes so lets move to startpos for new state
    pose = state.get("zones")[zone]["startpos"]
    print(pose)
    state.update({"nextjpos":pose}) 
    # get current pos, check if new state near
    while not(comparelist(state.get("curjpos"), state.get("nextjpos"),0.1,5)):
        print("moving to "+ zone +" position")
        print(comparelist(state.get("curjpos"), state.get("nextjpos"),0.1,5))
        print(state.get("robot").get_curjpos())

        print(state.get("robot"))
        # state.get("robot").move("joint", state.get("nextjpos") , 100)   
    print("moving to "+ zone +" position")

    # set new state
    state.update({"currentzone":zone})
    print(state.get("currentzone"))
    state.update({"speed":state.get("zones")[zone]["speed"]})
     # unblock state changes 
    state.update({"modechange":False})

