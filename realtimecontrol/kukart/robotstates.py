kukastate = {  "robot": {},
                "modechange": False, # are we currently changing modes?
                "actionmode": False,
                "randommode":True,
                "currentaction": None,
                "actionindex" : 0,
                "currentzone": "init", 
                "wandermode": 0,
                "wanderspeed" :0,
                "speed":100,
                "dynmode": 0,
                "randomwristmode": 0,
                "reachmode":0,
                "dancemode":1,
                "limitadjust":[5,5,5],
                "softwarelimits": [(-95,123), (-135,35),(-120,158),(-350,350),(-119,119),(-358,358)],
                "limits" : [(-94, 122), (-134,34), (-119,157), (-349,349), (-118,118), (-357,357)],
                "zones" : {"init": {
                                "startpos": [  0, -90,  90,   0,  15,   0],
                                "safezone": [(-94, 122), (-105,-65), (89,115), (-15,15), (-15,45), (-357,357)],
                                "actions":[],
                                "exitpos": [  0, -131,  155,   3,  -10,   0], ##exit to rest position  
                                "exits" : ["rest"],
                                "speed": 20          
                                },
                            "rest" :{
                                "startpos": [-3, -133, 156, -2 , 0, 0],
                                "safezone": [(-5, 5), (-134,-130), (155,157), (-3,3), (-10,10), (-357,357)],
                                "actions": {"breathe":[[-3, -130, 155, -2 , 0, 0],[-3, -134, 157, -2 , 0, 0]]},
                                "exitpos": [-3, -134, 156, -2 , 0, 0],    ##exit to wakeup position                        
                                "exits" : ["wakeup"] ,
                                "speed": 20                 
                            },
                            "wakeup":{
                                "startpos": [-3, -134, 156, -2 , 0, 0],
                                "safezone": [(-94, 122), (-134,-123), (136,157), (-23,23), (-25,25), (-357,357)],
                                "exitpos": [-3, -97, 16, -2 , 90, 0],   ##exit to stretch position                   
                                "exits" : ["stretch", "wander"]  ,
                                "speed": 40                

                            },
                            "stretch": {
                                "startpos": [-3, -97, 16, -2 , 0, 0],
                                "safezone": [(-94, 122), (-98,-96), (13,19), (-2,2), (85,95), (-357,357)],
                                "exitpos": [-60, -65, 157, -2 , 0, 0],   ##exit to wander position
                                "exits" : ["wander"] ,
                                "speed": 50         
                            },
                            "wander": {
                                "startpos": [60, -65, 60, 0, 45, 0],
                                "safezone" : [(-94, 122), (-70,-60), (29,100), (-4,4), (-118,118), (-357,357)],
                                "exitpos": [-3, -97, 157, -2 , 0, 0],
                                "exits" : ["stretch", "wildwander"],
                                "speed": 30                                    
                                },
                            "wildwander": {
                                "startpos": [60, -22.5, -112.5, 0, 45, 0],
                                "safezone" : [(-94, 122), (-124,-60), (-19,157), (-1,1), (-118,118), (-357,357)],
                                "exitpos": [-3, -97, 157, -2 , 0, 0] ,                        
                                "exits" : ["stretch", "wander"] ,
                                "speed": 100         
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
                "nextjpos":[  0, -90,  90,   0,  90,   0],
                "curjpos":[  0, -90,  90,   0,  90,   0],
                "nextpos": [855.209656, -577.229004, 1515.260742, 145.978836, -27.013065, -179.960007],
                "curpos": [855.209656, -577.229004, 1515.260742, 145.978836, -27.013065, -179.960007],
            } 