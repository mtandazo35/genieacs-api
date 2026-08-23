"""Mapa de parametros por modelo de datos.

Abstrae las diferencias entre fabricantes: la API habla en conceptos
(wifi_2g_ssid, lan_ip, pppoe_user...) y aqui se traducen a los paths TR-069
reales. Para soportar un modelo nuevo: agregar otro dict y mapearlo en pick_map().
"""

# Modelo TR-098 estilo Cudy / OpenWrt-easycwmp (probado en WR3000/AX3000).
TR098 = {
    "root": "InternetGatewayDevice",
    "type": "xsd:string",
    "params": {
        # --- WiFi 2.4 GHz (WLANConfiguration.1) ---
        "wifi_2g_ssid": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID",
        "wifi_2g_password": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.X_CUDY_Password",
        "wifi_2g_enable": ("InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.Enable", "xsd:boolean"),
        "wifi_2g_channel": ("InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.Channel", "xsd:unsignedInt"),
        "wifi_2g_hidden": ("InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSIDAdvertisementEnabled", "xsd:boolean"),
        "wifi_2g_clients": ("InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.AssociatedDeviceNumberOfEntries", "xsd:unsignedInt"),
        # --- WiFi 5 GHz (WLANConfiguration.2) ---
        "wifi_5g_ssid": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.SSID",
        "wifi_5g_password": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.X_CUDY_Password",
        "wifi_5g_enable": ("InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.Enable", "xsd:boolean"),
        "wifi_5g_channel": ("InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.Channel", "xsd:unsignedInt"),
        "wifi_5g_hidden": ("InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.SSIDAdvertisementEnabled", "xsd:boolean"),
        "wifi_5g_clients": ("InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.AssociatedDeviceNumberOfEntries", "xsd:unsignedInt"),
        # --- LAN / IP ---
        "lan_ip": "InternetGatewayDevice.LANDevice.1.LANHostConfigManagement.IPInterface.1.IPInterfaceIPAddress",
        "lan_mask": "InternetGatewayDevice.LANDevice.1.LANHostConfigManagement.IPInterface.1.IPInterfaceSubnetMask",
        "dhcp_enable": ("InternetGatewayDevice.LANDevice.1.LANHostConfigManagement.DHCPServerEnable", "xsd:boolean"),
        "dhcp_min": "InternetGatewayDevice.LANDevice.1.LANHostConfigManagement.MinAddress",
        "dhcp_max": "InternetGatewayDevice.LANDevice.1.LANHostConfigManagement.MaxAddress",
        "dhcp_lease": ("InternetGatewayDevice.LANDevice.1.LANHostConfigManagement.DHCPLeaseTime", "xsd:int"),
        # --- DNS ---
        "lan_dns": "InternetGatewayDevice.LANDevice.1.LANHostConfigManagement.DNSServers",   # DNS entregado por DHCP
        "wan_dns": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.DNSServers",
        # --- WAN (IP estatica / DHCP) sobre WANIPConnection.1 ---
        "wan_mode": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.AddressingType",  # 'Static'/'DHCP'
        "wan_ip": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress",
        "wan_mask": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.SubnetMask",
        "wan_gateway": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.DefaultGateway",
        "wan_mtu": ("InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.MaxMTUSize", "xsd:unsignedInt"),
        # --- PPPoE (WANPPPConnection.1) ---
        "pppoe_enable": ("InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Enable", "xsd:boolean"),
        "pppoe_user": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username",
        "pppoe_password": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Password",
        "pppoe_status": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ConnectionStatus",
        # --- Hora / fecha ---
        "tz": "InternetGatewayDevice.Time.LocalTimeZone",
        "ntp1": "InternetGatewayDevice.Time.NTPServer1",
        "ntp2": "InternetGatewayDevice.Time.NTPServer2",
        # --- Solo lectura utiles para status ---
        "uptime": "InternetGatewayDevice.DeviceInfo.UpTime",
        "firmware": "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
        "cpu": "InternetGatewayDevice.DeviceInfo.ProcessStatus.CPUUsage",
        "mac": "InternetGatewayDevice.LANDevice.1.LANEthernetInterfaceConfig.1.MACAddress",
    },
}


# Parametros de configuracion "restaurables" (se respaldan y se reaplican tras
# un factory reset). Solo valores que definen como debe quedar el equipo.
CONFIG_KEYS = [
    "wifi_2g_ssid", "wifi_2g_password", "wifi_2g_enable", "wifi_2g_channel",
    "wifi_5g_ssid", "wifi_5g_password", "wifi_5g_enable", "wifi_5g_channel",
    "lan_ip", "lan_mask", "dhcp_enable", "dhcp_min", "dhcp_max", "dhcp_lease",
    "lan_dns", "wan_dns", "wan_mode", "wan_ip", "wan_mask", "wan_gateway", "wan_mtu",
    "pppoe_enable", "pppoe_user", "pppoe_password", "tz", "ntp1", "ntp2",
]


# Modelo TR-181 (raiz Device.), probado en TP-Link EX511.
# WiFi: SSID y AccessPoint son instancias separadas ligadas por referencia.
# Primaria 2.4G = SSID.1/AccessPoint.1 ; primaria 5G = SSID.3/AccessPoint.3.
TR181 = {
    "root": "Device",
    "type": "xsd:string",
    "params": {
        "wifi_2g_ssid": "Device.WiFi.SSID.1.SSID",
        "wifi_2g_password": "Device.WiFi.AccessPoint.1.Security.KeyPassphrase",
        "wifi_2g_enable": ("Device.WiFi.SSID.1.Enable", "xsd:boolean"),
        "wifi_2g_channel": ("Device.WiFi.Radio.1.Channel", "xsd:unsignedInt"),
        "wifi_2g_clients": ("Device.WiFi.AccessPoint.1.AssociatedDeviceNumberOfEntries", "xsd:unsignedInt"),
        "wifi_5g_ssid": "Device.WiFi.SSID.3.SSID",
        "wifi_5g_password": "Device.WiFi.AccessPoint.3.Security.KeyPassphrase",
        "wifi_5g_enable": ("Device.WiFi.SSID.3.Enable", "xsd:boolean"),
        "wifi_5g_channel": ("Device.WiFi.Radio.2.Channel", "xsd:unsignedInt"),
        "wifi_5g_clients": ("Device.WiFi.AccessPoint.3.AssociatedDeviceNumberOfEntries", "xsd:unsignedInt"),
        "lan_ip": "Device.IP.Interface.1.IPv4Address.1.IPAddress",
        "lan_mask": "Device.IP.Interface.1.IPv4Address.1.SubnetMask",
        "pppoe_user": "Device.PPP.Interface.1.Username",
        "pppoe_password": "Device.PPP.Interface.1.Password",
        "pppoe_status": "Device.PPP.Interface.1.ConnectionStatus",
        "tz": "Device.Time.LocalTimeZone",
        "ntp1": "Device.Time.NTPServer1",
        "ntp2": "Device.Time.NTPServer2",
        "uptime": "Device.DeviceInfo.UpTime",
        "firmware": "Device.DeviceInfo.SoftwareVersion",
        "cpu": "Device.DeviceInfo.ProcessStatus.CPUUsage",
        "mac": "Device.Ethernet.Interface.2.MACAddress",
    },
}


def pick_map(device: dict) -> dict:
    """Elige el mapa segun la raiz del arbol que reporta el equipo:
    InternetGatewayDevice -> TR-098 ; Device -> TR-181."""
    if isinstance(device, dict):
        if "InternetGatewayDevice" in device:
            return TR098
        if "Device" in device:
            return TR181
    return TR098


def resolve(pmap: dict, key: str):
    """Devuelve (path, tipo_xsd) para una clave logica."""
    spec = pmap["params"].get(key)
    if spec is None:
        return None
    if isinstance(spec, tuple):
        return spec[0], spec[1]
    return spec, pmap["type"]
