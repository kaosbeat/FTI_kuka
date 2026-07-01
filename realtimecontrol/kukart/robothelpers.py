from typing import List, Optional


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
    for i,p in enumerate(pos):
        if  not checklimits(i, p, limits):
            return False
    return True






def activateZone(zone, state):
    robot = state.get("robot")
    # get current condition:
    currentzone = state.get("currentzone")
    # block state changes
    state.update({"modechange":True})
    # get current pos    
    if not posSafe(robot.get_curjpos(), state.get("zones")[zone]["safezone"]): # is current pos in safe zone of new limits?
        pose = state.get("zones")[currentzone]["exitpos"] # goto exit pos 
        state.update({"nextjpose":pose}) 
        while not (comparelist(robot.get_curjpos(), state.get("nextjpose"),0.1,5)):
            print("moving to exit position")
        if not posSafe(robot.get_curjpos(), state.get("zones")[zone]["safezone"]): # is current pos in safe zone?
            pose = state.get("zones")["init"]["exitpos"] # goto neutral pos 
            state.update({"nextjpose":pose}) 
            while not (comparelist(robot.get_curjpos(), state.get("nextjpose"),0.1,5)):
                print("moving to neutral position")
    # yes so lets move to startpos for new state
    pose = state.get("zones")[zone]["startpos"]
    state.update({"nextjpose":pose}) 
    # get current pos, check if new state near
    while not(comparelist(robot.get_curjpos(), state.get("nextjpose"),0.1,5)):
        print("moving to "+ zone +" position")
    # set new state
    state.update({"currentzone":zone})
     # unblock state changes 
    state.update({"modechange":False})
