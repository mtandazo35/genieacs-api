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
        # --- WiFi 5 GHz (WLANConfiguration.2) ---
        "wifi_5g_ssid": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.SSID",
        "wifi_5g_password": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.X_CUDY_Password",
        "wifi_5g_enable": ("InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.Enable", "xsd:boolean"),
        "wifi_5g_channel": ("InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.Channel", "xsd:unsignedInt"),
        "wifi_5g_hidden": ("InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.SSIDAdvertisementEnabled", "xsd:boolean"),
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
        # --- PPPoE (WANPPPConnection.1) ---
        "pppoe_enable": ("InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Enable", "xsd:boolean"),
        "pppoe_user": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username",
        "pppoe_password": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Password",
        # --- Hora / fecha ---
        "tz": "InternetGatewayDevice.Time.LocalTimeZone",
        "ntp1": "InternetGatewayDevice.Time.NTPServer1",
        "ntp2": "InternetGatewayDevice.Time.NTPServer2",
        # --- Solo lectura utiles para status ---
        "uptime": "InternetGatewayDevice.DeviceInfo.UpTime",
        "firmware": "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
        "cpu": "InternetGatewayDevice.DeviceInfo.ProcessStatus.CPUUsage",
        "wan_ip": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress",
    },
}


def pick_map(device: dict) -> dict:
    """Elige el mapa segun el modelo. Por ahora todo cae a TR098."""
    # Aqui, a futuro: mirar DeviceInfo.Manufacturer/ProductClass y devolver otro mapa.
    return TR098


def resolve(pmap: dict, key: str):
    """Devuelve (path, tipo_xsd) para una clave logica."""
    spec = pmap["params"].get(key)
    if spec is None:
        return None
    if isinstance(spec, tuple):
        return spec[0], spec[1]
    return spec, pmap["type"]
