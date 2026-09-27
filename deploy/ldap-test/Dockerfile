# OpenLDAP для живой проверки входа: тестовый каталог (ldif/) встроен в образ — так его загружает osixia/openldap.
FROM osixia/openldap:1.5.0
COPY ldif/ /container/service/slapd/assets/config/bootstrap/ldif/custom/
# Свой тестовый УЦ и сертификат сервера (CN и SAN = ldap), новые при каждой сборке: встроенный в образ УЦ 2021 года
# просрочен, и API (проверка сертификата обязательна) к нему справедливо не подключается. Ключ УЦ сразу удаляется.
# ca.crt API читает через общий том ldapcerts
RUN set -e; cd /container/service/slapd/assets/certs \
 && openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=Collector LDAP test CA" -keyout ca.key -out ca.crt \
 && openssl req -newkey rsa:2048 -nodes -subj "/CN=ldap" -keyout ldap.key -out ldap.csr \
 && printf 'subjectAltName=DNS:ldap\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n' > ext.cnf \
 && openssl x509 -req -in ldap.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -extfile ext.cnf -out ldap.crt \
 && rm -f ca.key ca.srl ldap.csr ext.cnf && chmod 644 ca.crt ldap.crt && chmod 600 ldap.key && chown -R 911:911 .
