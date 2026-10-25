# Scrobble Importer Jellyfin

此文件为 Claude Code (claude.ai/code) 讲解此工具的规范和逻辑。

## 声明

- 文内伪变量引用使用 Python f-string 格式 `{foo}` 表示；Curl 示例 Bash 代码块内用 `${bar}` 表示。
- Curl Bash 脚本仅展示请求内容，实际代码可以使用 Requests 库等方式。
- 本文涉及 Python、SQLite、Jellyfin API 等多种变量格式。实际代码变量名统一使用 Python 惯例的 `snake` 或 `constant` 格式。
- 虽本文用中文书写，但用户前端展示内容和文稿需使用英文。

## 变量

### 本地存储

保存到以 `. + {变量名}` 命名的无扩展名文本文件。有此文件时，优先从本地读取。

| 变量名      | 文件名       | 类型  | 值        | 说明                        |
| ----------- | ------------ | ----- | --------- | --------------------------- |
| `device_id` | `.device_id` | `str` | 随机 UUID | 设备唯一 ID（鉴权请求头用） |

### 内部变量

| 变量名                 | 类型  | 值                             | 说明                       |
| ---------------------- | ----- | ------------------------------ | -------------------------- |
| `MEDIA_BROWSER_CLIENT` | `str` | `'Scrobble Importer Jellyfin'` | 客户端名称（鉴权请求头用） |
| `VERSION`              | `str` | `'0.1.0'`                      | 客户端版本（鉴权请求头用） |

### 环境变量

环境变量或存储于 `.env`。有默认值的为可选配置项，允许此环境变量为空或不存在；`{}` 为占位符，用于程序自动生成。

| 变量名                  | 类型  | 默认值     | 说明                   |
| ----------------------- | ----- | ---------- | ---------------------- |
| `DEVICE`                | `str` | `'Python'` | 设备名（鉴权请求头用） |
| `URL`                   | `str` |            | Jellyfin URL           |
| `USERNAME`              | `str` |            | Jellyfin 用户名        |
| `PASSWORD`              | `str` |            | Jellyfin 密码          |
| `PLAYBACK_REPORTING_DB` | `str` |            | 播放记录数据库路径     |

## 流程

### 1. 登录用户

API 密钥连接没有指定用户，无法更新 / 删除项目，故使用用户名和密码登录。

1. 使用 API，携带 `MediaBrowser Client="{MEDIA_BROWSER_CLIENT}", Device="{DEVICE}", DeviceId="{device_id}", Version="{VERSION}"` 鉴权请求头登录。
    - Curl 示例：
        ```bash
        curl -X 'POST' \
          '${URL}/Users/AuthenticateByName' \
          -H 'accept: application/json' \
          -H 'Content-Type: application/json' \
          -H 'Authorization: MediaBrowser Client="${MEDIA_BROWSER_CLIENT}", Device="${DEVICE}", DeviceId="${device_id}", Version="${VERSION}"' \
          -d '{
          "Username": "${USERNAME}",
          "Pw": "${PASSWORD}"
          }'
        ```
    - 响应示例： @samples/response_users_authenticatebyname.json 。
2. 从响应中获取：
    - `AccessToken`
    - 用户 `Id`，用于限定后续操作范围。

此后所有请求，需携带 `MediaBrowser Client="{MEDIA_BROWSER_CLIENT}", Device="{DEVICE}", DeviceId="{device_id}", Version="{VERSION}", Token="{access_token}"` (`snake` 写法的 `AccessToken`) 鉴权请求头。

### 2. 获取数据

从 Jellyfin 数据库获取所有曲目数据，包含 MusicBrainz Id;曲目数据只包含艺人指向数据库表内 Id，故还需完整获取所有艺人数据。

1. 使用 API 获取用户所有音乐。
    - Curl 示例：
        ```bash
        curl -X 'GET' \
          '${URL}/Items?userId=${user_id}&recursive=true&includeItemTypes=Audio' \
          -H 'accept: application/json' \
          -H 'Authorization: MediaBrowser Client="${MEDIA_BROWSER_CLIENT}", Device="${DEVICE}", DeviceId="${device_id}", Version="${VERSION}", Token="${access_token}"'
        ```
    - 响应示例： @samples/response_items_audio.json 。
2.
