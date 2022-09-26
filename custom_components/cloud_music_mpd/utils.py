import socket

def check_port(ip, port):
    is_alive = True
    # 检测当前IP是否在线
    sk = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    sk.settimeout(1)
    try:
        sk.connect((ip, port))
    except Exception:
        is_alive = False
    sk.close()
    return is_alive