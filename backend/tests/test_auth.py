def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["model_version"] == "lgbm-2026.09-demo"


def test_login_returns_token_and_user(client):
    r = client.post("/api/auth/login", json={"username": "dispatcher", "password": "dispatcher123"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "dispatcher"
    assert body["user"]["role_label"] == "Диспетчер района"


def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"username": "dispatcher", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Неверный логин или пароль"


def test_swagger_form_login(client):
    r = client.post("/api/auth/token", data={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "admin"


def test_me_for_every_role(client, auth):
    for role in ("dispatcher", "engineer", "manager", "admin"):
        r = client.get("/api/auth/me", headers=auth(role))
        assert r.status_code == 200
        assert r.json()["role"] == role


def test_protected_endpoint_requires_token(client):
    r = client.get("/api/predictions")
    assert r.status_code == 401
    assert r.json()["detail"] == "Требуется вход в систему"


def test_invalid_token_rejected(client):
    r = client.get("/api/predictions", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Недействительный токен"


def test_manager_is_read_only(client, auth):
    assert client.get("/api/journal", headers=auth("manager")).status_code == 200
    r = client.post("/api/predictions/1/decision", json={"decision_type": "monitor", "reason_id": 11},
                    headers=auth("manager"))
    assert r.status_code == 403
    assert r.json()["detail"] == "Недостаточно прав для этого действия"


def test_dispatcher_cannot_import(client, auth):
    r = client.post("/api/import", data={"kind": "events"},
                    files={"file": ("e.csv", b"a,b\n1,2\n", "text/csv")}, headers=auth("dispatcher"))
    assert r.status_code == 403


def test_login_is_audited(client, auth, session_factory):
    from sqlalchemy import select

    from app.models import AuditLog

    with session_factory() as s:
        actions = set(s.scalars(select(AuditLog.action)))
    assert {"login", "login_failed"} <= actions
