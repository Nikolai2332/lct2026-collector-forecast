**Бэкенд (контейнер api, Python 3.12.14)** — прямых зависимостей 14, всего установлено пакетов 36.

| Компонент | Версия | Назначение | Лицензия |
|---|---|---|---|
| alembic | 1.20.0 | миграции схемы PostgreSQL | MIT |
| APScheduler | 3.11.3 | планировщик: такт симуляции, разметка исходов, прогрев кэша | MIT |
| defusedxml | 0.7.1 | безопасный разбор XML (защита от XXE) | PSFL |
| fastapi | 0.141.1 | веб-фреймворк REST API, схема OpenAPI | MIT |
| ldap3 | 2.9.1 | вход через LDAP/Active Directory | LGPL v3 |
| openpyxl | 3.1.5 | чтение и запись XLSX (импорт, выгрузки) | MIT |
| psycopg | 3.3.6 | драйвер PostgreSQL | LGPL-3.0-only |
| pydantic | 2.13.5 | схемы данных, проверка входа и выхода API | MIT |
| pydantic-settings | 2.15.0 | настройки из переменных окружения | MIT |
| PyJWT | 2.15.0 | JWT-токены входа | MIT |
| python-multipart | 0.0.32 | загрузка файлов (импорт) | Apache-2.0 |
| PyYAML | 6.0.3 | правила рекомендаций rules.yaml | MIT |
| SQLAlchemy | 2.0.54 | ORM и построитель SQL-запросов | MIT |
| uvicorn | 0.53.0 | ASGI-сервер для FastAPI | BSD-3-Clause |

**ML-конвейер (окружение ml/, Python 3.14.0)** — прямых зависимостей 13, всего установлено пакетов 38.

| Компонент | Версия | Назначение | Лицензия |
|---|---|---|---|
| duckdb | 1.5.5 | ETL журналов (313 млн строк) и расчёт признаков | MIT License |
| lightgbm | 4.7.0 | модель градиентного бустинга | MIT |
| matplotlib | 3.11.2 | графики отчёта ML | Python Software Foundation License |
| numpy | 2.5.3 | численные массивы | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| openpyxl | 3.1.5 | чтение и запись XLSX (импорт, выгрузки) | MIT |
| pandas | 3.0.6 | таблицы данных | BSD License |
| polars | 1.44.2 | быстрые таблицы данных | MIT License |
| psycopg | 3.3.6 | драйвер PostgreSQL | LGPL-3.0-only |
| psycopg-binary | 3.3.6 | скомпилированная часть драйвера PostgreSQL | LGPL-3.0-only |
| pyarrow | 25.0.1 | формат Parquet | Apache-2.0 |
| scikit-learn | 1.9.1 | метрики, калибровка вероятностей | BSD-3-Clause |
| scipy | 1.18.1 | статистика | BSD License |
| shap | 0.52.0 | вклад признаков в прогноз (причины) | MIT License |

**Фронтенд (Node.js, package-lock.json)** — прямых зависимостей 23 (8 для работы, 15 для сборки и проверки), всего пакетов в lock-файле 343.

| Компонент | Версия | Назначение | Для чего | Лицензия |
|---|---|---|---|---|
| @ant-design/icons | 5.6.1 | иконки интерфейса | работа | MIT |
| @eslint/js | 9.39.5 | правила ESLint | сборка и проверка | MIT |
| @tanstack/react-query | 5.103.2 | загрузка и кэш данных API | работа | MIT |
| @types/node | 22.20.4 | типы Node.js для TypeScript | сборка и проверка | MIT |
| @types/react | 18.3.31 | типы React | сборка и проверка | MIT |
| @types/react-dom | 18.3.7 | типы React DOM | сборка и проверка | MIT |
| @vitejs/plugin-react | 6.1.1 | React для сборщика Vite | сборка и проверка | MIT |
| antd | 5.29.3 | компоненты интерфейса Ant Design | работа | MIT |
| dayjs | 1.11.23 | даты и время | работа | MIT |
| echarts | 6.1.0 | графики и схема коллектора | работа | Apache-2.0 |
| eslint | 9.39.5 | проверка кода (lint) | сборка и проверка | MIT |
| eslint-plugin-react-hooks | 7.1.1 | правила хуков React | сборка и проверка | MIT |
| eslint-plugin-react-refresh | 0.5.7 | правила горячей перезагрузки | сборка и проверка | MIT |
| globals | 17.12.0 | глобальные имена для ESLint | сборка и проверка | MIT |
| msw | 2.15.0 | моки API для разработки без бэкенда | сборка и проверка | MIT |
| openapi-typescript | 7.13.0 | типы TypeScript из openapi.json | сборка и проверка | MIT |
| playwright | 1.63.0 | проход по экранам и скриншоты | сборка и проверка | Apache-2.0 |
| react | 18.3.1 | библиотека интерфейса | работа | MIT |
| react-dom | 18.3.1 | вывод React в браузер | работа | MIT |
| react-router-dom | 6.30.6 | маршрутизация экранов | работа | MIT |
| typescript | 5.9.3 | язык TypeScript, проверка типов | сборка и проверка | Apache-2.0 |
| typescript-eslint | 8.70.1 | ESLint для TypeScript | сборка и проверка | MIT |
| vite | 8.3.1 | сборщик фронтенда | сборка и проверка | MIT |

<!--LICENSES-->
| Лицензия | Пакетов |
|---|---|
| MIT | 307 |
| Apache-2.0 | 23 |
| ISC | 19 |
| BSD-3-Clause | 16 |
| MPL-2.0 | 12 |
| BSD-2-Clause | 8 |
| MIT License | 5 |
| BSD License | 5 |
| LGPL-3.0-only | 4 |
| (MIT OR CC0-1.0) | 2 |
| PSFL | 1 |
| MIT AND PSF-2.0 | 1 |
| LGPL v3 | 1 |
| PSF-2.0 | 1 |
| BSD-2-Clause AND Apache-2.0 WITH LLVM-exception | 1 |
| Python Software Foundation License | 1 |
| BSD | 1 |
| BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | 1 |
| Apache-2.0 OR BSD-2-Clause | 1 |
| MIT-CMU | 1 |
| Dual License | 1 |
| MPL-2.0 AND MIT | 1 |
| Python-2.0 | 1 |
| CC-BY-4.0 | 1 |
| BlueOak-1.0.0 | 1 |
| 0BSD | 1 |
<!--APPENDIX-->
**Бэкенд (контейнер api, Python 3.12.14), все установленные пакеты (36)**

| Пакет | Версия | Лицензия |
|---|---|---|
| alembic | 1.20.0 | MIT |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| APScheduler | 3.11.3 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| defusedxml | 0.7.1 | PSFL |
| et_xmlfile | 2.0.0 | MIT |
| fastapi | 0.141.1 | MIT |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| h11 | 0.16.0 | MIT |
| httptools | 0.8.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| ldap3 | 2.9.1 | LGPL v3 |
| Mako | 1.4.3 | MIT |
| MarkupSafe | 3.0.3 | BSD-3-Clause |
| openpyxl | 3.1.5 | MIT |
| psycopg | 3.3.6 | LGPL-3.0-only |
| psycopg-binary | 3.3.6 | LGPL-3.0-only |
| pyasn1 | 0.6.4 | BSD-2-Clause |
| pydantic | 2.13.5 | MIT |
| pydantic_core | 2.46.5 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| PyJWT | 2.15.0 | MIT |
| python-dotenv | 1.2.3 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| PyYAML | 6.0.3 | MIT |
| SQLAlchemy | 2.0.54 | MIT |
| starlette | 1.7.0 | BSD-3-Clause |
| typing_extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| tzlocal | 5.4.4 | MIT |
| uvicorn | 0.53.0 | BSD-3-Clause |
| uvloop | 0.22.1 | MIT License |
| watchfiles | 1.3.0 | MIT |
| websockets | 17.1 | BSD-3-Clause |

**ML-конвейер (окружение ml/, Python 3.14.0), все установленные пакеты (38)**

| Пакет | Версия | Лицензия |
|---|---|---|
| cloudpickle | 3.1.2 | BSD-3-Clause |
| colorama | 0.4.6 | BSD License |
| contourpy | 1.4.0 | BSD-3-Clause |
| cycler | 0.12.1 | BSD License |
| duckdb | 1.5.5 | MIT License |
| et_xmlfile | 2.0.0 | MIT |
| fonttools | 4.66.0 | MIT |
| iniconfig | 2.3.0 | MIT |
| joblib | 1.6.0 | BSD-3-Clause |
| kiwisolver | 1.5.1 | BSD License |
| lightgbm | 4.7.0 | MIT |
| llvmlite | 0.49.0 | BSD-2-Clause AND Apache-2.0 WITH LLVM-exception |
| matplotlib | 3.11.2 | Python Software Foundation License |
| narwhals | 2.26.0 | MIT |
| numba | 0.67.0 | BSD |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| openpyxl | 3.1.5 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pandas | 3.0.6 | BSD License |
| pillow | 12.3.0 | MIT-CMU |
| pluggy | 1.6.0 | MIT |
| polars | 1.44.2 | MIT License |
| polars-runtime-32 | 1.44.2 | MIT |
| psycopg | 3.3.6 | LGPL-3.0-only |
| psycopg-binary | 3.3.6 | LGPL-3.0-only |
| pyarrow | 25.0.1 | Apache-2.0 |
| Pygments | 2.21.0 | BSD-2-Clause |
| pyparsing | 3.3.3 | MIT |
| pytest | 9.1.1 | MIT |
| python-dateutil | 2.9.0.post0 | Dual License |
| scikit-learn | 1.9.1 | BSD-3-Clause |
| scipy | 1.18.1 | BSD License |
| shap | 0.52.0 | MIT License |
| six | 1.17.0 | MIT |
| slicer | 0.0.8 | MIT License |
| threadpoolctl | 3.7.0 | BSD-3-Clause |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| tzdata | 2026.4 | Apache-2.0 |

**Фронтенд, все пакеты package-lock.json (343)**

| Пакет | Версия | Лицензия |
|---|---|---|
| @ant-design/colors | 7.2.1 | MIT |
| @ant-design/cssinjs | 1.24.0 | MIT |
| @ant-design/cssinjs-utils | 1.1.3 | MIT |
| @ant-design/fast-color | 2.0.6 | MIT |
| @ant-design/icons | 5.6.1 | MIT |
| @ant-design/icons-svg | 4.6.0 | MIT |
| @ant-design/react-slick | 1.1.2 | MIT |
| @babel/code-frame | 7.29.7 | MIT |
| @babel/compat-data | 7.29.7 | MIT |
| @babel/core | 7.29.7 | MIT |
| @babel/generator | 7.29.8 | MIT |
| @babel/helper-compilation-targets | 7.29.7 | MIT |
| @babel/helper-globals | 7.29.7 | MIT |
| @babel/helper-module-imports | 7.29.7 | MIT |
| @babel/helper-module-transforms | 7.29.7 | MIT |
| @babel/helper-string-parser | 7.29.7 | MIT |
| @babel/helper-validator-identifier | 7.29.7 | MIT |
| @babel/helper-validator-option | 7.29.7 | MIT |
| @babel/helpers | 7.29.7 | MIT |
| @babel/parser | 7.29.9 | MIT |
| @babel/runtime | 7.29.7 | MIT |
| @babel/template | 7.29.7 | MIT |
| @babel/traverse | 7.29.8 | MIT |
| @babel/types | 7.29.8 | MIT |
| @emotion/hash | 0.8.0 | MIT |
| @emotion/unitless | 0.7.5 | MIT |
| @eslint-community/eslint-utils | 4.10.1 | MIT |
| @eslint-community/regexpp | 4.12.2 | MIT |
| @eslint/config-array | 0.21.2 | Apache-2.0 |
| @eslint/config-helpers | 0.4.2 | Apache-2.0 |
| @eslint/core | 0.17.0 | Apache-2.0 |
| @eslint/eslintrc | 3.3.7 | MIT |
| @eslint/js | 9.39.5 | MIT |
| @eslint/object-schema | 2.1.7 | Apache-2.0 |
| @eslint/plugin-kit | 0.4.1 | Apache-2.0 |
| @humanfs/core | 0.19.2 | Apache-2.0 |
| @humanfs/node | 0.16.8 | Apache-2.0 |
| @humanfs/types | 0.15.0 | Apache-2.0 |
| @humanwhocodes/module-importer | 1.0.1 | Apache-2.0 |
| @humanwhocodes/retry | 0.4.3 | Apache-2.0 |
| @inquirer/ansi | 2.0.8 | MIT |
| @inquirer/confirm | 6.3.2 | MIT |
| @inquirer/core | 12.0.3 | MIT |
| @inquirer/figures | 2.0.9 | MIT |
| @inquirer/type | 4.1.1 | MIT |
| @jridgewell/gen-mapping | 0.3.13 | MIT |
| @jridgewell/remapping | 2.3.5 | MIT |
| @jridgewell/resolve-uri | 3.1.2 | MIT |
| @jridgewell/sourcemap-codec | 1.6.0 | MIT |
| @jridgewell/trace-mapping | 0.3.31 | MIT |
| @mswjs/interceptors | 0.41.9 | MIT |
| @open-draft/deferred-promise | 2.2.0 | MIT |
| @open-draft/deferred-promise | 3.0.0 | MIT |
| @open-draft/logger | 0.3.0 | MIT |
| @open-draft/until | 2.1.0 | MIT |
| @oxc-project/types | 0.151.0 | MIT |
| @rc-component/async-validator | 5.1.2 | MIT |
| @rc-component/color-picker | 2.0.1 | MIT |
| @rc-component/context | 1.4.0 | MIT |
| @rc-component/mini-decimal | 1.1.4 | MIT |
| @rc-component/mutate-observer | 1.1.0 | MIT |
| @rc-component/portal | 1.1.2 | MIT |
| @rc-component/qrcode | 1.1.3 | MIT |
| @rc-component/tour | 1.15.1 | MIT |
| @rc-component/trigger | 2.3.1 | MIT |
| @redocly/ajv | 8.11.2 | MIT |
| @redocly/config | 0.22.0 | MIT |
| @redocly/openapi-core | 1.34.20 | MIT |
| @remix-run/router | 1.23.4 | MIT |
| @rolldown/binding-android-arm-eabi | 1.2.10 | MIT |
| @rolldown/binding-android-arm64 | 1.2.10 | MIT |
| @rolldown/binding-darwin-arm64 | 1.2.10 | MIT |
| @rolldown/binding-darwin-x64 | 1.2.10 | MIT |
| @rolldown/binding-freebsd-x64 | 1.2.10 | MIT |
| @rolldown/binding-linux-arm-gnueabihf | 1.2.10 | MIT |
| @rolldown/binding-linux-arm64-gnu | 1.2.10 | MIT |
| @rolldown/binding-linux-arm64-musl | 1.2.10 | MIT |
| @rolldown/binding-linux-ppc64-gnu | 1.2.10 | MIT |
| @rolldown/binding-linux-s390x-gnu | 1.2.10 | MIT |
| @rolldown/binding-linux-x64-gnu | 1.2.10 | MIT |
| @rolldown/binding-linux-x64-musl | 1.2.10 | MIT |
| @rolldown/binding-openharmony-arm64 | 1.2.10 | MIT |
| @rolldown/binding-win32-arm64-msvc | 1.2.10 | MIT |
| @rolldown/binding-win32-x64-msvc | 1.2.10 | MIT |
| @rolldown/pluginutils | 1.0.1 | MIT |
| @tanstack/query-core | 5.103.2 | MIT |
| @tanstack/react-query | 5.103.2 | MIT |
| @types/estree | 1.0.9 | MIT |
| @types/json-schema | 7.0.15 | MIT |
| @types/node | 22.20.4 | MIT |
| @types/prop-types | 15.7.15 | MIT |
| @types/react | 18.3.31 | MIT |
| @types/react-dom | 18.3.7 | MIT |
| @types/set-cookie-parser | 2.4.10 | MIT |
| @types/statuses | 2.0.6 | MIT |
| @typescript-eslint/eslint-plugin | 8.70.1 | MIT |
| @typescript-eslint/parser | 8.70.1 | MIT |
| @typescript-eslint/project-service | 8.70.1 | MIT |
| @typescript-eslint/scope-manager | 8.70.1 | MIT |
| @typescript-eslint/tsconfig-utils | 8.70.1 | MIT |
| @typescript-eslint/type-utils | 8.70.1 | MIT |
| @typescript-eslint/types | 8.70.1 | MIT |
| @typescript-eslint/typescript-estree | 8.70.1 | MIT |
| @typescript-eslint/utils | 8.70.1 | MIT |
| @typescript-eslint/visitor-keys | 8.70.1 | MIT |
| @vitejs/plugin-react | 6.1.1 | MIT |
| acorn | 8.18.0 | MIT |
| acorn-jsx | 5.3.2 | MIT |
| agent-base | 7.1.4 | MIT |
| ajv | 6.15.0 | MIT |
| ansi-colors | 4.1.3 | MIT |
| ansi-regex | 5.0.1 | MIT |
| ansi-styles | 4.3.0 | MIT |
| antd | 5.29.3 | MIT |
| argparse | 2.0.1 | Python-2.0 |
| balanced-match | 4.0.4 | MIT |
| balanced-match | 1.0.2 | MIT |
| baseline-browser-mapping | 2.11.25 | Apache-2.0 |
| brace-expansion | 2.1.7 | MIT |
| brace-expansion | 5.0.12 | MIT |
| brace-expansion | 1.1.21 | MIT |
| browserslist | 4.29.0 | MIT |
| callsites | 3.1.0 | MIT |
| caniuse-lite | 1.0.30001812 | CC-BY-4.0 |
| chalk | 4.1.2 | MIT |
| change-case | 5.4.4 | MIT |
| classnames | 2.5.1 | MIT |
| cli-width | 4.1.0 | ISC |
| cliui | 8.0.1 | ISC |
| color-convert | 2.0.1 | MIT |
| color-name | 1.1.4 | MIT |
| colorette | 1.4.0 | MIT |
| compute-scroll-into-view | 3.1.1 | MIT |
| concat-map | 0.0.1 | MIT |
| convert-source-map | 2.0.0 | MIT |
| cookie | 1.1.1 | MIT |
| copy-to-clipboard | 3.3.3 | MIT |
| cross-spawn | 7.0.6 | MIT |
| csstype | 3.2.3 | MIT |
| dayjs | 1.11.23 | MIT |
| debug | 4.4.3 | MIT |
| deep-is | 0.1.4 | MIT |
| detect-libc | 2.1.2 | Apache-2.0 |
| echarts | 6.1.0 | Apache-2.0 |
| electron-to-chromium | 1.5.438 | ISC |
| emoji-regex | 8.0.0 | MIT |
| escalade | 3.2.0 | MIT |
| escape-string-regexp | 4.0.0 | MIT |
| eslint | 9.39.5 | MIT |
| eslint-plugin-react-hooks | 7.1.1 | MIT |
| eslint-plugin-react-refresh | 0.5.7 | MIT |
| eslint-scope | 8.4.0 | BSD-2-Clause |
| eslint-visitor-keys | 3.4.3 | Apache-2.0 |
| eslint-visitor-keys | 5.0.1 | Apache-2.0 |
| eslint-visitor-keys | 4.2.1 | Apache-2.0 |
| espree | 10.4.0 | BSD-2-Clause |
| esquery | 1.7.0 | BSD-3-Clause |
| esrecurse | 4.3.0 | BSD-2-Clause |
| estraverse | 5.3.0 | BSD-2-Clause |
| esutils | 2.0.3 | BSD-2-Clause |
| fast-deep-equal | 3.1.3 | MIT |
| fast-json-stable-stringify | 2.1.0 | MIT |
| fast-levenshtein | 2.0.6 | MIT |
| fast-string-truncated-width | 3.0.3 | MIT |
| fast-string-width | 3.0.2 | MIT |
| fast-wrap-ansi | 0.2.2 | MIT |
| fdir | 6.5.0 | MIT |
| file-entry-cache | 8.0.0 | MIT |
| find-up | 5.0.0 | MIT |
| flat-cache | 4.0.1 | MIT |
| flatted | 3.4.4 | ISC |
| fsevents | 2.3.3 | MIT |
| gensync | 1.0.0-beta.2 | MIT |
| get-caller-file | 2.0.5 | ISC |
| glob-parent | 6.0.2 | ISC |
| globals | 14.0.0 | MIT |
| globals | 17.12.0 | MIT |
| graphql | 16.14.2 | MIT |
| has-flag | 4.0.0 | MIT |
| headers-polyfill | 5.0.1 | MIT |
| hermes-estree | 0.25.1 | MIT |
| hermes-parser | 0.25.1 | MIT |
| https-proxy-agent | 7.0.6 | MIT |
| ignore | 7.0.10 | MIT |
| ignore | 5.3.2 | MIT |
| import-fresh | 3.3.1 | MIT |
| imurmurhash | 0.1.4 | MIT |
| index-to-position | 1.2.0 | MIT |
| is-extglob | 2.1.1 | MIT |
| is-fullwidth-code-point | 3.0.0 | MIT |
| is-glob | 4.0.3 | MIT |
| is-node-process | 1.2.0 | MIT |
| isexe | 2.0.0 | ISC |
| js-levenshtein | 1.1.6 | MIT |
| js-tokens | 4.0.0 | MIT |
| js-yaml | 4.3.2 | MIT |
| jsesc | 3.1.0 | MIT |
| json-buffer | 3.0.1 | MIT |
| json-schema-traverse | 1.0.0 | MIT |
| json-schema-traverse | 0.4.1 | MIT |
| json-stable-stringify-without-jsonify | 1.0.1 | MIT |
| json2mq | 0.2.0 | MIT |
| json5 | 2.2.3 | MIT |
| keyv | 4.5.4 | MIT |
| levn | 0.4.1 | MIT |
| lightningcss | 1.33.0 | MPL-2.0 |
| lightningcss-android-arm64 | 1.33.0 | MPL-2.0 |
| lightningcss-darwin-arm64 | 1.33.0 | MPL-2.0 |
| lightningcss-darwin-x64 | 1.33.0 | MPL-2.0 |
| lightningcss-freebsd-x64 | 1.33.0 | MPL-2.0 |
| lightningcss-linux-arm-gnueabihf | 1.33.0 | MPL-2.0 |
| lightningcss-linux-arm64-gnu | 1.33.0 | MPL-2.0 |
| lightningcss-linux-arm64-musl | 1.33.0 | MPL-2.0 |
| lightningcss-linux-x64-gnu | 1.33.0 | MPL-2.0 |
| lightningcss-linux-x64-musl | 1.33.0 | MPL-2.0 |
| lightningcss-win32-arm64-msvc | 1.33.0 | MPL-2.0 |
| lightningcss-win32-x64-msvc | 1.33.0 | MPL-2.0 |
| locate-path | 6.0.0 | MIT |
| lodash.merge | 4.6.2 | MIT |
| loose-envify | 1.4.0 | MIT |
| lru-cache | 5.1.1 | ISC |
| minimatch | 5.1.9 | ISC |
| minimatch | 10.2.6 | BlueOak-1.0.0 |
| minimatch | 3.1.5 | ISC |
| ms | 2.1.3 | MIT |
| msw | 2.15.0 | MIT |
| mute-stream | 3.0.0 | ISC |
| nanoid | 3.3.19 | MIT |
| natural-compare | 1.4.0 | MIT |
| node-releases | 2.0.57 | MIT |
| openapi-typescript | 7.13.0 | MIT |
| optionator | 0.9.4 | MIT |
| outvariant | 1.4.3 | MIT |
| p-limit | 3.1.0 | MIT |
| p-locate | 5.0.0 | MIT |
| parent-module | 1.0.1 | MIT |
| parse-json | 8.3.0 | MIT |
| path-exists | 4.0.0 | MIT |
| path-key | 3.1.1 | MIT |
| path-to-regexp | 6.3.0 | MIT |
| picocolors | 1.1.1 | ISC |
| picomatch | 4.0.7 | MIT |
| playwright | 1.63.0 | Apache-2.0 |
| playwright-core | 1.63.0 | Apache-2.0 |
| pluralize | 8.0.0 | MIT |
| postcss | 8.5.28 | MIT |
| prelude-ls | 1.2.1 | MIT |
| punycode | 2.3.1 | MIT |
| rc-cascader | 3.34.0 | MIT |
| rc-checkbox | 3.5.0 | MIT |
| rc-collapse | 3.9.0 | MIT |
| rc-dialog | 9.6.0 | MIT |
| rc-drawer | 7.3.0 | MIT |
| rc-dropdown | 4.2.1 | MIT |
| rc-field-form | 2.7.1 | MIT |
| rc-image | 7.12.0 | MIT |
| rc-input | 1.8.0 | MIT |
| rc-input-number | 9.5.0 | MIT |
| rc-mentions | 2.20.0 | MIT |
| rc-menu | 9.16.1 | MIT |
| rc-motion | 2.9.5 | MIT |
| rc-notification | 5.6.4 | MIT |
| rc-overflow | 1.5.0 | MIT |
| rc-pagination | 5.1.0 | MIT |
| rc-picker | 4.11.3 | MIT |
| rc-progress | 4.0.0 | MIT |
| rc-rate | 2.13.1 | MIT |
| rc-resize-observer | 1.4.3 | MIT |
| rc-segmented | 2.7.1 | MIT |
| rc-select | 14.16.8 | MIT |
| rc-slider | 11.1.9 | MIT |
| rc-steps | 6.0.1 | MIT |
| rc-switch | 4.1.0 | MIT |
| rc-table | 7.54.0 | MIT |
| rc-tabs | 15.7.0 | MIT |
| rc-textarea | 1.10.2 | MIT |
| rc-tooltip | 6.4.0 | MIT |
| rc-tree | 5.13.1 | MIT |
| rc-tree-select | 5.27.0 | MIT |
| rc-upload | 4.11.0 | MIT |
| rc-util | 5.44.4 | MIT |
| rc-virtual-list | 3.19.2 | MIT |
| react | 18.3.1 | MIT |
| react-dom | 18.3.1 | MIT |
| react-is | 18.3.1 | MIT |
| react-router | 6.30.6 | MIT |
| react-router-dom | 6.30.6 | MIT |
| require-directory | 2.1.1 | MIT |
| require-from-string | 2.0.2 | MIT |
| resize-observer-polyfill | 1.5.1 | MIT |
| resolve-from | 4.0.0 | MIT |
| rettime | 0.11.11 | MIT |
| rolldown | 1.2.10 | MIT |
| scheduler | 0.23.2 | MIT |
| scroll-into-view-if-needed | 3.1.0 | MIT |
| semver | 7.8.5 | ISC |
| semver | 6.3.1 | ISC |
| set-cookie-parser | 3.1.2 | MIT |
| shebang-command | 2.0.0 | MIT |
| shebang-regex | 3.0.0 | MIT |
| signal-exit | 4.1.0 | ISC |
| source-map-js | 1.2.1 | BSD-3-Clause |
| statuses | 2.0.2 | MIT |
| strict-event-emitter | 0.5.1 | MIT |
| string-convert | 0.2.1 | MIT |
| string-width | 4.2.3 | MIT |
| strip-ansi | 6.0.1 | MIT |
| strip-json-comments | 3.1.1 | MIT |
| stylis | 4.4.0 | MIT |
| supports-color | 10.2.2 | MIT |
| supports-color | 7.2.0 | MIT |
| tagged-tag | 1.0.0 | MIT |
| throttle-debounce | 5.0.2 | MIT |
| tinyglobby | 0.2.17 | MIT |
| tldts | 7.4.15 | MIT |
| tldts-core | 7.4.15 | MIT |
| toggle-selection | 1.0.6 | MIT |
| tough-cookie | 6.0.2 | BSD-3-Clause |
| ts-api-utils | 2.5.0 | MIT |
| tslib | 2.3.0 | 0BSD |
| type-check | 0.4.0 | MIT |
| type-fest | 4.41.0 | (MIT OR CC0-1.0) |
| type-fest | 5.10.0 | (MIT OR CC0-1.0) |
| typescript | 5.9.3 | Apache-2.0 |
| typescript-eslint | 8.70.1 | MIT |
| undici-types | 6.21.0 | MIT |
| until-async | 3.0.2 | MIT |
| update-browserslist-db | 1.3.3 | MIT |
| uri-js | 4.4.1 | BSD-2-Clause |
| uri-js-replace | 1.0.1 | MIT |
| vite | 8.3.1 | MIT |
| which | 2.0.2 | ISC |
| word-wrap | 1.2.5 | MIT |
| wrap-ansi | 7.0.0 | MIT |
| y18n | 5.0.8 | ISC |
| yallist | 3.1.1 | ISC |
| yaml-ast-parser | 0.0.43 | Apache-2.0 |
| yargs | 17.7.3 | MIT |
| yargs-parser | 21.1.1 | ISC |
| yocto-queue | 0.1.0 | MIT |
| zod | 4.6.5 | MIT |
| zod-validation-error | 4.0.2 | MIT |
| zrender | 6.1.0 | BSD-3-Clause |
