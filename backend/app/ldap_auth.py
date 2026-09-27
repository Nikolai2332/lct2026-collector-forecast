"""Вход через корпоративный каталог LDAP / Active Directory (ТЗ, раздел 11: «интеграция с LDAP/AD»).

Порядок (закрыт по умолчанию — любая неясность означает отказ):
1. Bind сервисной учёткой LDAP_BIND_DN (ldaps:// или StartTLS; в production открытый ldap:// без StartTLS не стартует).
2. Поиск пользователя по LDAP_USER_FILTER; логин экранируется (escape_filter_chars) — защита от LDAP-инъекции.
   Найдено не ровно одна запись — отказ.
3. Проверка пароля: bind найденным DN и паролем пользователя. Пустой пароль отвергается заранее
   (в LDAP bind с пустым паролем — «анонимный» и считается успешным).
4. Группы: атрибут memberOf и поиск групп по LDAP_GROUP_FILTER (member / uniqueMember = DN пользователя).
   Роли — по LDAP_ROLE_GROUPS: все подходящие (роли складываются, как в AD заказчика); нет ни одной — отказ.
   Область видимости — по LDAP_SCOPE_GROUPS («id объекта:группа»): объединение узлов всех подходящих групп.
   Если у пользователя только роли с областью (диспетчер района, техник, руководитель), а группы области нет
   (или она ссылается на несуществующий объект) — отказ: закрыто по умолчанию, «видеть всё» по ошибке нельзя.
5. Локальный профиль users (auth_source=ldap) создаётся или обновляется: ФИО, роли и область — из каталога при
   каждом входе.
   Локальную учётку с тем же логином LDAP не перехватывает — отказ (и запись в журнал).

Результат: User — вход разрешён; None — неверный логин/пароль, нет группы, конфликт (вызывающий код: 401);
LdapUnavailable — каталог недоступен или настроен неверно (503). Пароли в журнал не пишутся.
"""

import logging
import re
import ssl
from dataclasses import dataclass

from ldap3 import NONE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import ROLES, Object, User

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 5
USERNAME_RE = re.compile(r"^[\w.@-]{1,64}$")


class LdapUnavailable(Exception):
    """Каталог недоступен или настроен неверно — вход невозможен (503), локальные пароли не проверяются."""


@dataclass(frozen=True)
class RoleGroup:
    role: str
    group: str  # полный DN группы или его первая часть: cn=dispatchers

    def matches(self, group_dn: str) -> bool:
        dn = _norm(group_dn)
        want = _norm(self.group)
        return dn == want or dn.split(",", 1)[0] == want


def _norm(dn: str) -> str:
    return ",".join(part.strip() for part in str(dn).lower().split(","))


@dataclass(frozen=True)
class ScopeGroup:
    object_id: int
    group: str

    def matches(self, group_dn: str) -> bool:
        return RoleGroup("", self.group).matches(group_dn)


def parse_scope_groups(spec: str) -> list[ScopeGroup]:
    """«5773:cn=district-south,5:cn=complex-alpha» или с полными DN через «;»:
    «5:cn=complex-alpha,ou=groups,dc=example,dc=local;5773:cn=district-south,ou=groups,dc=example,dc=local».
    Пустая строка — областей из каталога нет (роли с областью тогда войти не смогут)."""
    parts = spec.split(";") if ";" in spec else re.split(r",(?=\s*\d+\s*:)", spec)
    result = []
    for part in parts:
        if not part.strip():
            continue
        oid, sep, group = part.partition(":")
        if not sep or not oid.strip().isdigit() or not group.strip():
            raise LdapUnavailable(f"LDAP_SCOPE_GROUPS: не разобрать «{part.strip()}» (нужно id_объекта:группа)")
        result.append(ScopeGroup(int(oid.strip()), group.strip()))
    return result


def parse_role_groups(spec: str) -> list[RoleGroup]:
    """«dispatcher:cn=dispatchers,engineer:cn=engineers» или с полными DN через «;»:
    «admin:cn=admins,ou=groups,dc=example,dc=local;dispatcher:cn=dispatchers,ou=groups,dc=example,dc=local»."""
    parts = spec.split(";") if ";" in spec else re.split(r",(?=\s*(?:%s)\s*:)" % "|".join(ROLES), spec)
    result = []
    for part in parts:
        if not part.strip():
            continue
        role, sep, group = part.partition(":")
        role = role.strip()
        if not sep or role not in ROLES or not group.strip():
            raise LdapUnavailable(f"LDAP_ROLE_GROUPS: не разобрать «{part.strip()}» (нужно роль:группа)")
        result.append(RoleGroup(role, group.strip()))
    if not result:
        raise LdapUnavailable("LDAP_ROLE_GROUPS пуст — роли назначить нельзя")
    return result


def make_server(s: Settings) -> Server:
    if not s.ldap_url:
        raise LdapUnavailable("LDAP_URL не задан")
    tls = None
    if s.ldap_url.lower().startswith("ldaps://") or s.ldap_start_tls:
        tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=s.ldap_ca_file or None)
    return Server(s.ldap_url, get_info=NONE, connect_timeout=TIMEOUT_SECONDS, tls=tls)


def connect(server: Server, user: str | None, password: str | None) -> Connection:
    """Соединение без автоматического bind. Тесты подменяют эту функцию (ldap3 MOCK_SYNC)."""
    return Connection(server, user=user, password=password, receive_timeout=TIMEOUT_SECONDS, raise_exceptions=False)


def _open(server: Server, s: Settings, user: str | None, password: str | None) -> Connection | None:
    """Соединение после успешного bind; None — неверные учётные данные. Сетевые ошибки — исключение ldap3."""
    conn = connect(server, user, password)
    if s.ldap_start_tls and not s.ldap_url.lower().startswith("ldaps://"):
        conn.open()
        if not conn.start_tls():
            raise LdapUnavailable("StartTLS не удался")
    return conn if conn.bind() else None


NONE_ATTRS = ["1.1"]  # атрибут «1.1» — вернуть только DN


def _groups(conn: Connection, s: Settings, entry, user_dn: str, username: str) -> list[str]:
    groups = [str(g) for g in (entry["memberOf"].values if "memberOf" in entry else [])]
    if s.ldap_group_filter:
        flt = s.ldap_group_filter.format(user_dn=escape_filter_chars(user_dn), username=escape_filter_chars(username))
        if conn.search(s.ldap_group_base_dn or s.ldap_base_dn, flt, SUBTREE, attributes=NONE_ATTRS, size_limit=500):
            groups += [e.entry_dn for e in conn.entries]
    return groups


def _display_name(entry, username: str) -> str:
    for attr in ("displayName", "cn"):
        if attr in entry and entry[attr].value:
            value = entry[attr].value
            return str(value[0] if isinstance(value, list) else value)[:255]
    return username


def ldap_login(db: Session, username: str, password: str) -> User | None:
    s = get_settings()
    username = username.strip()
    if not password or not USERNAME_RE.fullmatch(username):
        return None
    roles = parse_role_groups(s.ldap_role_groups)
    scopes = parse_scope_groups(s.ldap_scope_groups)
    try:
        server = make_server(s)
        service = _open(server, s, s.ldap_bind_dn or None, s.ldap_bind_password or None)
        if service is None:
            raise LdapUnavailable("сервисная учётка LDAP_BIND_DN не прошла bind")
        flt = s.ldap_user_filter.format(username=escape_filter_chars(username))
        service.search(s.ldap_base_dn, flt, SUBTREE, attributes=["cn", "displayName", "memberOf"], size_limit=2)
        entries = list(service.entries)
        if len(entries) != 1:
            log.info("LDAP: пользователь %s не найден или не единственный (%d)", username, len(entries))
            return None
        entry = entries[0]
        user_dn = entry.entry_dn
        # Проверка пароля — bind самим пользователем, отдельным соединением
        user_conn = _open(server, s, user_dn, password)
        if user_conn is None:
            return None
        user_conn.unbind()
        groups = _groups(service, s, entry, user_dn, username)
        service.unbind()
    except (LDAPException, OSError) as e:  # сеть, TLS, неверный CA-файл — вход закрыт
        raise LdapUnavailable(f"каталог LDAP недоступен или TLS настроен неверно: {type(e).__name__}") from e

    from app.access import GLOBAL_ROLES, ordered_roles, set_access

    user_roles = ordered_roles({rg.role for rg in roles if any(rg.matches(g) for g in groups)})
    if not user_roles:
        log.info("LDAP: у %s нет группы из LDAP_ROLE_GROUPS", username)
        return None
    existing = set(db.scalars(select(Object.id).where(Object.id.in_([sg.object_id for sg in scopes])))) if scopes else set()
    scope_ids = sorted({sg.object_id for sg in scopes if sg.object_id in existing and any(sg.matches(g) for g in groups)})
    if not (GLOBAL_ROLES & set(user_roles)) and not scope_ids:
        log.info("LDAP: у %s роль с областью, но нет группы из LDAP_SCOPE_GROUPS — вход отклонён", username)
        return None
    # Логин в AD не различает регистр: «ADMIN» — тот же пользователь, что «admin». Профиль ищем без учёта регистра,
    # иначе «ADMIN» из каталога обошёл бы запрет на перехват локальной учётки и завёл бы второй профиль
    user = db.scalars(select(User).where(func.lower(User.username) == username.lower()).order_by(User.id)).first()
    if user is not None and user.auth_source != "ldap":
        log.warning("LDAP: логин %s совпадает с локальной учётной записью — вход через LDAP отклонён", username)
        return None
    if user is None:
        user = User(username=username, full_name=_display_name(entry, username), role=user_roles[0],
                    password_hash=None, auth_source="ldap", is_active=True)
        db.add(user)
    else:
        user.full_name = _display_name(entry, username)
    db.flush()
    set_access(db, user, user_roles, scope_ids)
    return user
