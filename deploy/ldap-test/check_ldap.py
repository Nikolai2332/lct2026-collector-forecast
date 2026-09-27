"""Живая проверка входа через LDAP против OpenLDAP из deploy/ldap-test/docker-compose.yml.

    python deploy/ldap-test/check_ldap.py [адрес API]      (по умолчанию http://localhost:19100)

Только стандартная библиотека Python. Код 1 — если какой-то исход не совпал с ожидаемым.
"""

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:19100"

CASES = [
    # логин, пароль, ожидаемый код, ожидаемая роль, что проверяем
    ("ivanov", "Disp-Test-2026", 200, "dispatcher", "dispatchers + district-main → диспетчер района"),
    ("smirnov", "Tech-Test-2026", 200, "technician", "technicians + complex-alpha → техник, один комплекс"),
    ("orlova", "Ods-Test-2026", 200, "dispatcher_ods", "группа ods → диспетчер ОДС, всё"),
    ("volkov", "Tech-Nog-2026", 401, None, "техник без группы комплекса — вход закрыт (fail-closed)"),
    ("petrova", "Eng-Test-2026", 200, "engineer", "группа engineers → инженер"),
    ("sidorov", "Adm-Test-2026", 200, "admin", "группа admins → администратор"),
    ("ivanov", "wrong-password", 401, None, "неверный пароль"),
    ("kuznetsov", "Nog-Test-2026", 401, None, "нет группы из LDAP_ROLE_GROUPS"),
    ("nobody", "whatever-1", 401, None, "нет в каталоге"),
    ("*)(uid=*", "whatever-1", 401, None, "попытка LDAP-инъекции"),
    ("admin", "admin123", 401, None, "локальный пароль при включённом LDAP не принимается"),
]


def login(username: str, password: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{BASE}/api/auth/login", data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    bad = 0
    for username, password, code, role, what in CASES:
        got, body = login(username, password)
        got_role = (body.get("user") or {}).get("role")
        ok = got == code and got_role == role
        bad += not ok
        name = (body.get("user") or {}).get("full_name", "")
        print(f"{'OK ' if ok else 'ОШИБКА'} {username:<10} → {got} {got_role or '':<10} {name:<22} {what}")
        if ok and code == 200:
            auth = {"Authorization": f"Bearer {body['access_token']}"}
            with urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/auth/me", headers=auth), timeout=10) as r:
                me = json.loads(r.read())
            assert me["username"] == username
            with urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/objects", headers=auth), timeout=30) as r:
                tree = json.loads(r.read())["items"]
            complexes = [c["name"] for root in tree for c in root["children"]]
            print(f"    область: {me['scope_label']}; комплексов в дереве: {len(complexes)}"
                  + (f" ({', '.join(complexes)})" if len(complexes) <= 2 else ""))
            if username == "smirnov" and complexes != ["объект Альфа"]:
                print("ОШИБКА  техник видит не один комплекс")
                bad += 1
    print("Проблем не найдено" if not bad else f"Не совпало: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
