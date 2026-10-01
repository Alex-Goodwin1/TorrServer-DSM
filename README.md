# TorrServer DSM (Uncensored Build)

Synology DSM package for TorrServer.

TorrServer DSM provides a native DSM interface for managing TorrServer on Synology NAS.

Этот репозиторий является модифицированной сборкой, обеспечивающей работу TorrServer без встроенных ограничений оригинала.

## Что сделано и зачем

1. **Отключение блокировок (WAF Referer Block)**  
      В оригинальном коде жестко прописан массив `defaultBlockedReferers`. Он принудительно блокирует HTTP-запросы (возвращая ошибку `403 Forbidden`) от ряда сторонних клиентов и веб-порталов.
   В этот черный список входят домены, связанные с альтернативными сборками Lampa и другими медиа-порталами: `lampa.click`, `lampa.land`, `lampa1.ru`, `bylampa.online`, `akter.black`, `tvigl.info`, `nnmtv.pw`, `line.pm`, `uspeh.sbs`, `usph.xyz`, `xabb.ru`, `abhq.ru`, `abmsx.tech`.  
     
2. **Сборка Android-клиента (TorrServe)**  
   В каждый релиз дополнительно собирается APK-файл клиента с патчем, меняющим URL проверки обновлений на этот репозиторий. Это гарантирует использование чистого сервера даже при автономной работе сервера на клиенте.

## Features

- DSM interface for package management

### Settings

- Port configuration for HTTP / HTTPS
- TorrServer cache configuration
- HTTPS
- Force HTTPS
- SSL certificate management
- HTTP authentication
- Logs and package status
- Restart TorrServer from DSM

## Requirements

- Synology DSM 7.0 or newer
- Supported architectures:
  - `amd64`
  - `arm64`
  - `arm7`

## Как настроить автоматические обновления на Synology

1. Откройте **Центр пакетов** (Package Center) на Synology.
2. Нажмите **Настройки** (иконка шестеренки) → вкладка **Источники пакетов**.
3. Нажмите **Добавить**:
   - **Имя:** `TorrServer Uncensored`
   - **Расположение:** `https://alex-goodwin1.github.io/TorrServer-DSM/`
4. Нажмите **ОК**.

Теперь при появлении новой версии в разделе "Обновления" Центра пакетов появится кнопка **Обновить**.

## Credits

- Core TorrServer: [YouROK](https://github.com/YouROK/TorrServer)
- Original Synology SPK packaging: [vladlenas](https://github.com/vladlenas/TorrServer-DSM)
- Uncensored build, Android client patching and automation: [Alex-Goodwin1](https://github.com/Alex-Goodwin1/TorrServer-DSM)
