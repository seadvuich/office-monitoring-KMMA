"""
Автоматична конфигурация на Zabbix 7.0 чрез API.

Създава:
  * групи хостове (Сървъри / Работни станции)
  * 4 сървъра + 15 работни станции
  * проверки: ICMP ping, DNS, HTTP (+ уеб сценарий), SMTP, FTP
  * тригери (със зависимости) и графика за времето за отговор
  * карта на мрежата
  * известявания по Email (през офисния Postfix) и Telegram

Скриптът е идемпотентен: може да се пуска многократно.
"""
import json
import os
import time
import urllib.request

URL = os.environ.get("ZABBIX_URL", "http://zabbix-web:8080/api_jsonrpc.php")
USER = os.environ.get("ZABBIX_USER", "Admin")
PASSWORD = os.environ.get("ZABBIX_PASSWORD", "zabbix")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

MAP_NAME = "Офис мрежа 10.10.0.0/24"
GRAPH_NAME = "Време за отговор на услугите"

# Типове елементи (items) в Zabbix
SIMPLE_CHECK = 3
EXTERNAL_CHECK = 10
FLOAT, UINT = 0, 3
# Приоритети на тригери
WARNING, HIGH = 2, 4

SERVERS = [
    {"host": "dns-server", "name": "DNS сървър (bind9)", "ip": "10.10.0.53", "svc": "dns"},
    {"host": "web-server", "name": "Web сървър (nginx)", "ip": "10.10.0.20", "svc": "http"},
    {"host": "mail-server", "name": "Mail сървър (Postfix)", "ip": "10.10.0.25", "svc": "smtp"},
    {"host": "ftp-server", "name": "FTP сървър (vsftpd)", "ip": "10.10.0.21", "svc": "ftp"},
]
WORKSTATIONS = [
    {"host": f"ws{i:02d}", "name": f"Работна станция {i:02d}", "ip": f"10.10.0.{100 + i}"}
    for i in range(1, 16)
]

token = None
_req_id = 0


class ApiError(Exception):
    pass


def api(method, params=None, auth=True):
    global _req_id
    _req_id += 1
    body = {"jsonrpc": "2.0", "method": method,
            "params": params if params is not None else {}, "id": _req_id}
    headers = {"Content-Type": "application/json-rpc"}
    if auth and token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(URL, data=json.dumps(body).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        err = data["error"]
        raise ApiError(f"{method}: {err.get('message')} {err.get('data')}")
    return data["result"]


def wait_and_login():
    global token
    print("Изчакване на Zabbix API (първото стартиране отнема 1-3 минути)...")
    last_err = None
    for _ in range(120):
        try:
            version = api("apiinfo.version", {}, auth=False)
            token = api("user.login", {"username": USER, "password": PASSWORD}, auth=False)
            print(f"Свързан със Zabbix {version}")
            return
        except Exception as exc:
            last_err = exc
            time.sleep(5)
    raise SystemExit(f"Zabbix API не е достъпен: {last_err}")


def group(name):
    found = api("hostgroup.get", {"filter": {"name": [name]}})
    if found:
        return found[0]["groupid"]
    return api("hostgroup.create", {"name": name})["groupids"][0]


def item(hostid, ifid, name, key, value_type, delay, itype=SIMPLE_CHECK, units=""):
    params = {"hostid": hostid, "interfaceid": ifid, "name": name, "key_": key,
              "type": itype, "value_type": value_type, "delay": delay}
    if units:
        params["units"] = units
    return api("item.create", params)["itemids"][0]


def trigger(name, expression, priority, depends_on=None, comment=""):
    params = {"description": name, "expression": expression,
              "priority": priority, "comments": comment}
    if depends_on:
        params["dependencies"] = [{"triggerid": t} for t in depends_on]
    return api("trigger.create", params)["triggerids"][0]


def create_host(h, groupid, tag):
    """Връща (hostid, interfaceid, created)."""
    found = api("host.get", {"filter": {"host": [h["host"]]}, "output": ["hostid"]})
    if found:
        hostid = found[0]["hostid"]
        created = False
    else:
        hostid = api("host.create", {
            "host": h["host"], "name": h["name"],
            "groups": [{"groupid": groupid}],
            "interfaces": [{"type": 1, "main": 1, "useip": 1, "ip": h["ip"],
                            "dns": "", "port": "10050"}],
            "tags": [{"tag": "office", "value": tag}],
        })["hostids"][0]
        created = True
    ifid = api("hostinterface.get", {"hostids": hostid, "output": ["interfaceid"]})[0]["interfaceid"]
    return hostid, ifid, created


def add_ping(h, hostid, ifid):
    item(hostid, ifid, "ICMP ping (достъпност)", "icmpping", UINT, "10s")
    item(hostid, ifid, "ICMP време за отговор", "icmppingsec", FLOAT, "10s", units="s")
    item(hostid, ifid, "ICMP загуба на пакети", "icmppingloss", FLOAT, "10s", units="%")
    t_down = trigger(f"{h['name']}: няма връзка (ICMP ping)",
                     f"max(/{h['host']}/icmpping,#3)=0", HIGH,
                     comment="Хостът не отговаря на 3 поредни ping проверки.")
    trigger(f"{h['name']}: висока загуба на пакети (>20%)",
            f"min(/{h['host']}/icmppingloss,#3)>20", WARNING, depends_on=[t_down])
    return t_down


def add_service(h, hostid, ifid, t_down):
    """Добавя проверки за услугата. Връща itemid за графиката (време за отговор)."""
    hn, svc = h["host"], h["svc"]
    if svc == "dns":
        key = f'dns_check.sh["{h["ip"]}","www.office.local"]'
        item(hostid, ifid, "DNS: резолвинг на www.office.local", key, UINT, "15s",
             itype=EXTERNAL_CHECK)
        perf = item(hostid, ifid, "DNS: време за отговор (TCP 53)",
                    "net.tcp.service.perf[tcp,,53]", FLOAT, "15s", units="s")
        trigger(f"{h['name']}: DNS услугата не отговаря", f"last(/{hn}/{key})=0", HIGH,
                depends_on=[t_down], comment="DNS сървърът не резолвва www.office.local.")
        return perf

    port = {"http": 80, "smtp": 25, "ftp": 21}[svc]
    label = {"http": "HTTP", "smtp": "SMTP (Mail)", "ftp": "FTP"}[svc]
    key = f"net.tcp.service[{svc},,{port}]"
    item(hostid, ifid, f"{label}: състояние на услугата (порт {port})", key, UINT, "15s")
    perf_key = f"net.tcp.service.perf[{svc},,{port}]"
    perf = item(hostid, ifid, f"{label}: време за отговор", perf_key, FLOAT, "15s", units="s")
    t_svc = trigger(f"{h['name']}: {label} услугата не работи", f"last(/{hn}/{key})=0", HIGH,
                    depends_on=[t_down])
    trigger(f"{h['name']}: бавен отговор на {label} (>1s)", f"avg(/{hn}/{perf_key},#3)>1",
            WARNING, depends_on=[t_svc])

    if svc == "http":
        scenario = "Office portal"
        api("httptest.create", {
            "name": scenario, "hostid": hostid, "delay": "30s", "retries": 1,
            "steps": [{"name": "Начална страница", "no": 1, "url": "http://10.10.0.20/",
                       "status_codes": "200", "required": "Office Portal", "timeout": "5s"}],
        })
        trigger(f"{h['name']}: уеб порталът не се зарежда правилно",
                f"last(/{hn}/web.test.fail[{scenario}])<>0", HIGH, depends_on=[t_svc],
                comment="Уеб сценарият не получава HTTP 200 или текста 'Office Portal'.")
    return perf


def create_graph(itemids):
    if not itemids or api("graph.get", {"filter": {"name": [GRAPH_NAME]}}):
        return
    colors = ["1A7C11", "F63100", "2774A4", "A54F10"]
    api("graph.create", {
        "name": GRAPH_NAME, "width": 900, "height": 250,
        "gitems": [{"itemid": i, "color": colors[n % 4], "drawtype": 0}
                   for n, i in enumerate(itemids)],
    })
    print("Графиката е създадена")


def create_map(hostids):
    if api("map.get", {"filter": {"name": [MAP_NAME]}}):
        return
    icons = api("image.get", {"output": ["imageid", "name"], "filter": {"imagetype": 1}})

    def icon(name):
        for size in ("(96)", "(64)", "(128)"):
            for img in icons:
                if img["name"] == f"{name}_{size}":
                    return img["imageid"]
        return icons[0]["imageid"]

    selements, links = [], []
    selements.append({"selementid": "sw", "elementtype": 4, "iconid_off": icon("Switch"),
                      "x": 660, "y": 300, "label": "Switch\n10.10.0.0/24"})
    selements.append({"selementid": "zbx", "elementtype": 4, "iconid_off": icon("Server"),
                      "x": 100, "y": 60, "label": "Zabbix сървър\n10.10.0.10"})
    links.append({"selementid1": "sw", "selementid2": "zbx"})
    for n, h in enumerate(SERVERS):
        sid = f"s{n}"
        selements.append({"selementid": sid, "elementtype": 0,
                          "elements": [{"hostid": hostids[h["host"]]}],
                          "iconid_off": icon("Server"), "x": 360 + n * 230, "y": 60,
                          "label": "{HOST.NAME}\n{HOST.CONN}"})
        links.append({"selementid1": "sw", "selementid2": sid})
    for n, h in enumerate(WORKSTATIONS):
        sid = f"w{n}"
        row, col = divmod(n, 8)
        selements.append({"selementid": sid, "elementtype": 0,
                          "elements": [{"hostid": hostids[h["host"]]}],
                          "iconid_off": icon("Workstation"), "x": 70 + col * 165,
                          "y": 500 + row * 150, "label": "{HOST.HOST}\n{HOST.CONN}"})
        links.append({"selementid1": "sw", "selementid2": sid})
    api("map.create", {"name": MAP_NAME, "width": 1400, "height": 800, "label_type": 0,
                       "expandproblem": 1, "highlight": 1,
                       "selements": selements, "links": links})
    print("Картата на мрежата е създадена")


def setup_notifications():
    admin = api("user.get", {"filter": {"username": [USER]}, "output": ["userid"]})[0]["userid"]
    medias = []

    email = api("mediatype.get", {"filter": {"name": ["Email"]}, "output": ["mediatypeid"]})
    if email:
        mtid = email[0]["mediatypeid"]
        api("mediatype.update", {"mediatypeid": mtid, "status": 0,
                                 "smtp_server": "10.10.0.25", "smtp_port": 25,
                                 "smtp_helo": "office.local",
                                 "smtp_email": "zabbix@office.local"})
        medias.append({"mediatypeid": mtid, "sendto": ["admin@office.local"], "active": 0,
                       "severity": 63, "period": "1-7,00:00-24:00"})
        print("Известяване по Email: admin@office.local (чрез mail-server)")

    if TG_TOKEN and TG_CHAT:
        tg = api("mediatype.get", {"filter": {"name": ["Telegram"]}, "output": "extend"})
        if tg:
            mt = tg[0]
            params = [{"name": p["name"],
                       "value": TG_TOKEN if p["name"] == "api_token" else p["value"]}
                      for p in mt.get("parameters", [])]
            api("mediatype.update", {"mediatypeid": mt["mediatypeid"], "status": 0,
                                     "parameters": params})
            medias.append({"mediatypeid": mt["mediatypeid"], "sendto": TG_CHAT, "active": 0,
                           "severity": 63, "period": "1-7,00:00-24:00"})
            print("Известяването в Telegram е включено")
    else:
        print("Telegram е пропуснат (липсват TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID в .env)")

    if medias:
        api("user.update", {"userid": admin, "medias": medias})

    actions = api("action.get", {"filter": {"name": ["Report problems to Zabbix administrators"]},
                                 "output": ["actionid"]})
    if actions:
        api("action.update", {"actionid": actions[0]["actionid"], "status": 0})
        print("Действието 'Report problems to Zabbix administrators' е включено")


def disable_default_host():
    # Подразбиращият се хост "Zabbix server" очаква агент, който тук не се ползва.
    found = api("host.get", {"filter": {"host": ["Zabbix server"]}, "output": ["hostid"]})
    if found:
        api("host.update", {"hostid": found[0]["hostid"], "status": 1})


def safe(step, func, *args):
    try:
        func(*args)
    except Exception as exc:
        print(f"ПРЕДУПРЕЖДЕНИЕ ({step}): {exc}")


def main():
    wait_and_login()
    g_srv = group("Офис/Сървъри")
    g_ws = group("Офис/Работни станции")

    hostids, perf_items = {}, []
    for h in SERVERS:
        hostid, ifid, created = create_host(h, g_srv, "server")
        hostids[h["host"]] = hostid
        if created:
            t_down = add_ping(h, hostid, ifid)
            perf_items.append(add_service(h, hostid, ifid, t_down))
            print(f"+ {h['host']} ({h['ip']})")
    for h in WORKSTATIONS:
        hostid, ifid, created = create_host(h, g_ws, "workstation")
        hostids[h["host"]] = hostid
        if created:
            add_ping(h, hostid, ifid)
            print(f"+ {h['host']} ({h['ip']})")

    safe("графика", create_graph, perf_items)
    safe("карта", create_map, hostids)
    safe("известяване", setup_notifications)
    safe("Zabbix server host", disable_default_host)
    print("\nГотово! Zabbix: http://localhost:8080  (Admin / zabbix)")


if __name__ == "__main__":
    main()
