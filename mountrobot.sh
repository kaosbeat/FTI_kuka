#!/bin/bash
sudo mount -t cifs //192.168.10.201/sharefolder /home/kaos/Documents/kaotec/FTI_kuka/robotshare/ -o username=KukaUser,password=68kuka1secpw59,vers=1.0,uid=$(id -u kaos),gid=$(id -g kaos),file_mode=0660,dir_mode=0770
