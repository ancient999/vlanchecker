#!/usr/bin/env python3
"""
vlan_checker.py — определяет в каком VLAN находится хост по IP адресу.

Как работает:
  1. Читает /proc/net/vlan/config — список VLAN интерфейсов на машине
  2. Получает IP адреса этих интерфейсов через 'ip addr show'
  3. Сравнивает IP хоста с подсетями VLAN интерфейсов
  4. Пингует хост — проверяет доступность

Требования: Linux, права на чтение /proc/net/vlan/config (обычно root)
"""

import subprocess
import ipaddress
import sys
import os


def get_vlan_interfaces():
    """
    Читает /proc/net/vlan/config.
    Возвращает словарь: { "eth0.10": {"vlan_id": 10, "parent": "eth0"} }
    """
    vlans = {}
    vlan_config = "/proc/net/vlan/config"

    if not os.path.exists(vlan_config):
        return vlans

    try:
        with open(vlan_config) as f:
            lines = f.readlines()
    except PermissionError:
        print("[!] Нет прав на чтение /proc/net/vlan/config — запусти с sudo")
        return vlans

    # Первые 2 строки — заголовок, пропускаем
    for line in lines[2:]:
        parts = line.split()
        if len(parts) >= 3:
            iface   = parts[0]       # eth0.10
            vlan_id = parts[1]       # 10
            parent  = parts[2]       # eth0
            vlans[iface] = {
                "vlan_id": int(vlan_id),
                "parent": parent
            }

    return vlans


def get_interface_ips():
    """
    Парсит вывод 'ip addr show'.
    Возвращает словарь: { "eth0.10": ["192.168.10.1/24"] }
    """
    try:
        result = subprocess.run(
            ["ip", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=5
        )
    except FileNotFoundError:
        print("[ERROR] Команда 'ip' не найдена — установи iproute2")
        sys.exit(1)

    interfaces = {}
    current_iface = None

    for line in result.stdout.splitlines():
        # Строка с именем интерфейса начинается без пробела
        if line and not line.startswith(" "):
            parts = line.split(":")
            if len(parts) >= 2:
                # Убираем @parent из имени (например eth0.10@eth0 -> eth0.10)
                current_iface = parts[1].strip().split("@")[0]
                interfaces[current_iface] = []
        # Строка с IPv4 адресом
        elif "inet " in line and current_iface:
            parts = line.strip().split()
            if len(parts) >= 2:
                interfaces[current_iface].append(parts[1])  # "192.168.10.1/24"

    return interfaces


def find_vlan_for_host(host_ip, vlans, iface_ips):
    """
    Ищет в каком VLAN находится host_ip.
    Сравнивает IP хоста с подсетями VLAN интерфейсов.
    Возвращает список совпадений.
    """
    try:
        target = ipaddress.ip_address(host_ip)
    except ValueError:
        print(f"[ERROR] Некорректный IP адрес: {host_ip}")
        sys.exit(1)

    results = []

    for iface, vlan_info in vlans.items():
        cidrs = iface_ips.get(iface, [])
        for cidr in cidrs:
            try:
                network = ipaddress.ip_network(cidr, strict=False)
                if target in network:
                    results.append({
                        "interface": iface,
                        "vlan_id":   vlan_info["vlan_id"],
                        "parent":    vlan_info["parent"],
                        "network":   str(network),
                    })
            except ValueError:
                continue

    return results


def check_host_reachable(host_ip):
    """Пингует хост. Возвращает True если доступен."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", host_ip],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def print_separator(char="─", width=56):
    print(char * width)


def print_vlan_table(vlans, iface_ips):
    """Выводит таблицу всех найденных VLAN."""
    print_separator()
    print(f"  {'Интерфейс':<14} {'VLAN ID':<9} {'Родитель':<10} Сеть")
    print_separator()

    if not vlans:
        print("  Нет VLAN интерфейсов.")
        print("  Причины: не настроены / нужен sudo / не поддерживается ОС")
        print_separator()
        return

    for iface, info in sorted(vlans.items(), key=lambda x: x[1]["vlan_id"]):
        nets = iface_ips.get(iface, [])
        net_str = nets[0] if nets else "—"
        print(f"  {iface:<14} {info['vlan_id']:<9} {info['parent']:<10} {net_str}")

    print_separator()


def main():
    print_separator("=")
    print("  VLAN Checker — определение VLAN по IP хоста")
    print_separator("=")

    # Шаг 1: собираем данные
    vlans     = get_vlan_interfaces()
    iface_ips = get_interface_ips()

    # Шаг 2: показываем все VLAN на машине
    print("\n[*] VLAN интерфейсы на этой машине:")
    print_vlan_table(vlans, iface_ips)

    # Шаг 3: если IP не передан — показываем помощь
    if len(sys.argv) < 2:
        print("\n[!] Укажи IP хоста для поиска:")
        print("    python3 vlan_checker.py <IP>")
        print("\n    Примеры:")
        print("    python3 vlan_checker.py 192.168.10.5")
        print("    python3 vlan_checker.py 10.0.20.100\n")
        sys.exit(0)

    host_ip = sys.argv[1]

    # Шаг 4: валидация IP
    try:
        ipaddress.ip_address(host_ip)
    except ValueError:
        print(f"\n[ERROR] '{host_ip}' — не валидный IP адрес\n")
        sys.exit(1)

    print(f"\n[*] Ищу VLAN для хоста: {host_ip}")

    # Шаг 5: проверяем доступность
    print("[*] Пингую хост...", end=" ", flush=True)
    reachable = check_host_reachable(host_ip)
    print("OK — доступен" if reachable else "FAIL — недоступен (ping timeout)")

    # Шаг 6: ищем VLAN
    results = find_vlan_for_host(host_ip, vlans, iface_ips)

    print()
    if results:
        print(f"[+] Хост {host_ip} находится в VLAN:")
        print_separator()
        for r in results:
            print(f"    VLAN ID   : {r['vlan_id']}")
            print(f"    Интерфейс : {r['interface']}")
            print(f"    Родитель  : {r['parent']}")
            print(f"    Сеть      : {r['network']}")
        print_separator()
    else:
        print(f"[-] Хост {host_ip} не найден ни в одном известном VLAN")
        print()
        print("    Возможные причины:")
        print("    — хост находится в другом сегменте сети")
        print("    — VLAN интерфейсы не настроены на этой машине")
        print("    — нужны права sudo для чтения /proc/net/vlan/config")

    print()


if __name__ == "__main__":
    main()
