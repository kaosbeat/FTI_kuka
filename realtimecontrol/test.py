from py_openshowvar import openshowvar
client = openshowvar('192.168.10.201', 7000)
client.can_connect
ov = client.read('$POS_ACT', debug=True)
print(ov)
client.close()