"""Вход через LDAP/AD на mock-сервере ldap3 (MOCK_SYNC): bind, поиск, группы → роль, профиль, отказы.

Живая проверка против OpenLDAP — deploy/ldap-test/ (docs/SECURITY.md).
"""

import pytest
from ldap3 import MOCK_SYNC, OFFLINE_SLAPD_2_4, Connection, Server
from sqlalchemy import select

from app import ldap_auth
from app.config import get_settings
from app.models import AuditLog, User

BASE = "dc=collector,dc=test"
SERVICE = f"cn=svc-collector,ou=services,{BASE}"


@pytest.fixture
def directory(monkeypatch):
    """Каталог в памяти: сервисная учётка, пользователи, группы (groupOfNames и memberOf)."""
    server = Server("mock-ldap", get_info=OFFLINE_SLAPD_2_4)
    seed = Connection(server, user=SERVICE, password="svc-pass", client_strategy=MOCK_SYNC)
    add = seed.strategy.add_entry
    add(SERVICE, {"objectClass": ["person"], "cn": "svc-collector", "userPassword": "svc-pass"})
    users = {
        "ivanov": ("Иванов Иван", "disp-pass-1", [f"cn=dispatchers,ou=groups,{BASE}", f"cn=district-main,ou=groups,{BASE}"]),
        # Техник: группа роли и группа комплекса «объект Альфа» (id 2 в демо-сиде) → видит один комплекс
        "smirnov": ("Смирнов Олег", "tech-pass-1", [f"cn=technicians,ou=groups,{BASE}", f"cn=complex-alpha,ou=groups,{BASE}"]),
        # Техник без группы комплекса — роль с областью без области: вход закрыт
        "volkov": ("Волков без комплекса", "tech-pass-2", [f"cn=technicians,ou=groups,{BASE}"]),
        # Техник, чья группа комплекса указывает на несуществующий объект
        "zaitsev": ("Зайцев", "tech-pass-3", [f"cn=technicians,ou=groups,{BASE}", f"cn=complex-ghost,ou=groups,{BASE}"]),
        "petrova": ("Петрова Анна", "eng-pass-1", []),  # группа — только через groupOfNames
        "sidorov": ("Сидоров Пётр", "adm-pass-1", [f"cn=admins,ou=groups,{BASE}", f"cn=dispatchers,ou=groups,{BASE}"]),
        "nogroup": ("Без группы", "nog-pass-1", [f"cn=others,ou=groups,{BASE}"]),
        "admin": ("Двойник локального", "dup-pass-1", [f"cn=admins,ou=groups,{BASE}"]),
    }
    for uid, (name, pwd, member_of) in users.items():
        add(f"uid={uid},ou=people,{BASE}", {"objectClass": ["inetOrgPerson"], "uid": uid, "cn": name, "displayName": name,
                                            "userPassword": pwd, **({"memberOf": member_of} if member_of else {})})
    # Два пользователя с одним uid — неоднозначность
    add(f"uid=twin,ou=people,{BASE}", {"objectClass": ["inetOrgPerson"], "uid": "twin", "cn": "Близнец 1", "userPassword": "twin-pass"})
    add(f"uid=twin,ou=other,{BASE}", {"objectClass": ["inetOrgPerson"], "uid": "twin", "cn": "Близнец 2", "userPassword": "twin-pass"})
    add(f"cn=engineers,ou=groups,{BASE}", {"objectClass": ["groupOfNames"], "cn": "engineers",
                                           "member": [f"uid=petrova,ou=people,{BASE}"]})

    s = get_settings()
    for key, value in {
        "ldap_enabled": True, "ldap_url": "ldap://mock-ldap", "ldap_base_dn": BASE, "ldap_bind_dn": SERVICE,
        "ldap_bind_password": "svc-pass", "ldap_user_filter": "(uid={username})",
        "ldap_role_groups": "admin:cn=admins;dispatcher:cn=dispatchers,ou=groups,dc=collector,dc=test;engineer:cn=engineers;"
                            "technician:cn=technicians",
        "ldap_scope_groups": "1:cn=district-main,2:cn=complex-alpha,999999:cn=complex-ghost",
        "ldap_group_filter": "(member={user_dn})", "ldap_start_tls": False,
    }.items():
        monkeypatch.setattr(s, key, value)
    monkeypatch.setattr(ldap_auth, "make_server", lambda _s: server)
    monkeypatch.setattr(ldap_auth, "connect", lambda srv, user, password: Connection(
        srv, user=user, password=password, client_strategy=MOCK_SYNC, raise_exceptions=False))
    return server


def login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_login_creates_profile_with_role_from_member_of(client, directory, session_factory):
    r = login(client, "ivanov", "disp-pass-1")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"] == "dispatcher" and r.json()["user"]["full_name"] == "Иванов Иван"
    token = r.json()["access_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["username"] == "ivanov"
    with session_factory() as s:
        u = s.scalar(select(User).where(User.username == "ivanov"))
        assert (u.auth_source, u.password_hash, u.role) == ("ldap", None, "dispatcher")


def test_role_from_group_search(client, directory):
    r = login(client, "petrova", "eng-pass-1")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"] == "engineer"


def test_roles_add_up_primary_is_widest(client, directory):
    user = login(client, "sidorov", "adm-pass-1").json()["user"]
    assert user["role"] == "admin" and user["roles"] == ["admin", "dispatcher"] and user["unrestricted"] is True


def test_technician_scope_from_complex_group(client, directory, session_factory):
    r = login(client, "smirnov", "tech-pass-1")
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["roles"] == ["technician"] and [n["id"] for n in user["scope"]] == [2]
    assert user["scope_label"] == "объект Альфа" and user["unrestricted"] is False
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    tree = client.get("/api/objects", headers=headers).json()["items"]
    assert [c["name"] for c in tree[0]["children"]] == ["объект Альфа"]


@pytest.mark.parametrize("username,password", [("volkov", "tech-pass-2"), ("zaitsev", "tech-pass-3")])
def test_scoped_role_without_scope_group_is_denied(client, directory, username, password):
    r = login(client, username, password)
    assert r.status_code == 401 and "access_token" not in r.text


def test_parse_scope_groups():
    sg = ldap_auth.parse_scope_groups("5773:cn=district-south,5:cn=complex-alpha")
    assert [(g.object_id, g.group) for g in sg] == [(5773, "cn=district-south"), (5, "cn=complex-alpha")]
    full = ldap_auth.parse_scope_groups("5:cn=alpha,ou=g,dc=x;7:cn=beta,ou=g,dc=x")
    assert full[1].matches("CN=beta, OU=g, DC=x") and not full[1].matches("cn=alpha,ou=g,dc=x")
    assert ldap_auth.parse_scope_groups("") == []
    with pytest.raises(ldap_auth.LdapUnavailable):
        ldap_auth.parse_scope_groups("alpha:cn=x")


def test_role_updated_on_next_login(client, directory, monkeypatch):
    assert login(client, "ivanov", "disp-pass-1").json()["user"]["role"] == "dispatcher"
    monkeypatch.setattr(get_settings(), "ldap_role_groups", "manager:cn=dispatchers")
    user = login(client, "ivanov", "disp-pass-1").json()["user"]
    assert user["roles"] == ["manager"] and [n["id"] for n in user["scope"]] == [1]


@pytest.mark.parametrize("username,password", [
    ("ivanov", "wrong"),          # неверный пароль
    ("ivanov", ""),               # пустой пароль: анонимный bind не считается входом
    ("nobody", "x"),              # нет в каталоге
    ("nogroup", "nog-pass-1"),    # нет группы из LDAP_ROLE_GROUPS
    ("twin", "twin-pass"),        # две записи — неоднозначно
    ("admin", "dup-pass-1"),      # совпадает с локальной учёткой — не перехватываем
    ("*)(uid=*", "x"),            # попытка LDAP-инъекции
])
def test_denied_is_401(client, directory, username, password, session_factory):
    r = login(client, username, password)
    assert r.status_code in (401, 422), r.text  # пустой пароль отсекает ещё схема запроса (422)
    assert "access_token" not in r.text
    with session_factory() as s:
        local_admin = s.scalar(select(User).where(User.username == "admin"))
        assert local_admin.auth_source == "local"


def test_injection_is_escaped(directory):
    from ldap3.utils.conv import escape_filter_chars

    assert escape_filter_chars("*)(uid=*") == "\\2a\\29\\28uid=\\2a"


def test_local_password_not_accepted_when_ldap_on(client, directory):
    assert login(client, "dispatcher", "dispatcher123").status_code == 401


def test_service_bind_failure_is_503_fail_closed(client, directory, monkeypatch, session_factory):
    monkeypatch.setattr(get_settings(), "ldap_bind_password", "wrong")
    r = login(client, "ivanov", "disp-pass-1")
    assert r.status_code == 503 and "access_token" not in r.text
    with session_factory() as s:
        last = s.scalars(select(AuditLog).where(AuditLog.action == "login_failed").order_by(AuditLog.id.desc())).first()
        assert last.details["reason"] == "ldap_unavailable" and "disp-pass-1" not in str(last.details)


def test_unreachable_server_is_503(client, monkeypatch):
    s = get_settings()
    for key, value in {"ldap_enabled": True, "ldap_url": "ldap://127.0.0.1:1", "ldap_role_groups": "admin:cn=admins",
                       "ldap_base_dn": BASE, "ldap_bind_dn": SERVICE, "ldap_bind_password": "x"}.items():
        monkeypatch.setattr(s, key, value)
    r = login(client, "ivanov", "disp-pass-1")
    assert r.status_code == 503


def test_blocked_ldap_user_is_401(client, directory, session_factory):
    assert login(client, "petrova", "eng-pass-1").status_code == 200
    with session_factory() as s:
        u = s.scalar(select(User).where(User.username == "petrova"))
        u.is_active = False
        s.commit()
    assert login(client, "petrova", "eng-pass-1").status_code == 401


def test_parse_role_groups():
    rg = ldap_auth.parse_role_groups("dispatcher:cn=dispatchers,engineer:cn=engineers,manager:cn=managers,admin:cn=admins")
    assert [(g.role, g.group) for g in rg] == [("dispatcher", "cn=dispatchers"), ("engineer", "cn=engineers"),
                                              ("manager", "cn=managers"), ("admin", "cn=admins")]
    full = ldap_auth.parse_role_groups("admin:cn=admins,ou=g,dc=x;manager:cn=m,ou=g,dc=x")
    assert full[0].matches("CN=Admins, OU=g, DC=x") and not full[0].matches("cn=admins2,ou=g,dc=x")
    assert full[0].matches("cn=admins,ou=g,dc=x")
    with pytest.raises(ldap_auth.LdapUnavailable):
        ldap_auth.parse_role_groups("superuser:cn=x")


def test_production_requires_encrypted_ldap(monkeypatch):
    from app.config import Settings, production_problems

    s = Settings(app_env="production", ldap_enabled=True, ldap_url="ldap://dc.example.local")
    assert any("ldaps://" in p for p in production_problems(s))
    ok = Settings(app_env="production", ldap_enabled=True, ldap_url="ldaps://dc.example.local")
    assert not any("LDAP" in p for p in production_problems(ok))


def test_login_case_does_not_bypass_local_account_or_duplicate_profile(client, directory, monkeypatch, session_factory):
    """Аудит 2, S-33: AD не различает регистр логина. «ADMIN» из каталога не должен завести второй профиль
    в обход запрета на перехват локального admin; «IVANOV» — тот же профиль, что «ivanov»."""
    real_search = Connection.search

    def case_insensitive_search(self, search_base, search_filter, *args, **kwargs):
        return real_search(self, search_base, search_filter.replace("(uid=ADMIN)", "(uid=admin)")
                           .replace("(uid=IVANOV)", "(uid=ivanov)"), *args, **kwargs)

    monkeypatch.setattr(Connection, "search", case_insensitive_search)
    assert login(client, "ADMIN", "dup-pass-1").status_code == 401
    assert login(client, "ivanov", "disp-pass-1").status_code == 200
    r = login(client, "IVANOV", "disp-pass-1")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["username"] == "ivanov"
    with session_factory() as s:
        names = list(s.scalars(select(User.username).where(User.username.in_(["ADMIN", "IVANOV", "ivanov", "admin"]))))
        assert sorted(names) == ["admin", "ivanov"]
