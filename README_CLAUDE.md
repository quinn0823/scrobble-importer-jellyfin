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

| 变量名                 | 类型  | 值                             | 说明                                       |
| ---------------------- | ----- | ------------------------------ | ------------------------------------------ |
| `MEDIA_BROWSER_CLIENT` | `str` | `'Scrobble Importer Jellyfin'` | 客户端名称（鉴权请求头和 ListenBrainz 用） |
| `VERSION`              | `str` | `'0.1.0'`                      | 客户端版本（鉴权请求头和 ListenBrainz 用） |

### 环境变量

环境变量或存储于 `.env`。有默认值或特殊声明的为可选配置项，允许此环境变量为空或不存在；`{}` 为占位符，用于程序自动生成。

| 变量名                    | 类型  | 默认值                           | 说明                                  |
| ------------------------- | ----- | -------------------------------- | ------------------------------------- |
| `DEVICE`                  | `str` | `'Python'`                       | 设备名（鉴权请求头用）                |
| `JELLYFIN_URL`            | `str` |                                  | Jellyfin URL                          |
| `USERNAME`                | `str` |                                  | Jellyfin 用户名                       |
| `PASSWORD`                | `str` |                                  | Jellyfin 密码                         |
| `PLAYBACK_REPORTING_DB`   | `str` |                                  | 播放记录数据库路径                    |
| `LISTENBRAINZ_API_URL`    | `str` | `'https://api.listenbrainz.org'` | ListenBrainz API URL                  |
| `LISTENBRAINZ_USER_TOKEN` | `str` |                                  | ListenBrainz user token               |
| `TZ`                      | `str` | `'UTC'`                          | 时区                                  |
| `START_DATE`          | `str` |                                  | 开始日期 (YYYY-MM-DD)（可选） |
| `END_DATE`            | `str` |                                  | 结束日期 (YYYY-MM-DD)（可选） |

## 解释

- “艺人”、`Artist`、`Artists` 指的是**曲目艺人**，用于从 Jellyfin 获取 MusicBrainz Id 并参与提交，是索引内的几对键值。
- “专辑艺人”、`AlbumArtist`、`AlbumArtists` 指的是**专辑艺人**，只用于和播放记录数据库匹配，是索引键的一部分。

## 运行模式

### 直接运行或 `--check`

此模式不执行流程 3.6 的提交操作，要执行 3.5 操作以检查属性完整度。用于检查播放记录项目和 Jellyfin 的匹配情况和属性完整度。

输出未匹配和 payload 构建属性缺失的项目。统计正常、未匹配、匹配但属性缺失的项目数量。

### `--submit`

此模式执行完整流程，提交到 ListenBrainz。

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

### 2. 处理曲目属性

从 Jellyfin 数据库获取所有曲目数据，包含 MusicBrainz Id (`ProviderIds`)；曲目数据的艺人
 Id 不支持多艺人，故还需完整获取所有艺人数据。然后，将艺人和艺人 MusicBrainz Id 对应；将曲目和曲目属性对应，建立索引，格式尽量统一和保持 Jellyfin 格式。

1. 使用 API 获取用户所有艺人。
    - Curl 示例：
        ```bash
        curl -X 'GET' \
          '${URL}/Artists?fields=ProviderIds&userId=${user_id}' \
          -H 'accept: application/json' \
          -H 'Authorization: MediaBrowser Client="${MEDIA_BROWSER_CLIENT}", Device="${DEVICE}", DeviceId="${device_id}", Version="${VERSION}", Token="${access_token}"'
        ```
    - 响应示例： @samples/response_artists.json 。
        - 其中 `ProviderIds` 示例：
            ```
            "ProviderIds": {
                "MusicBrainzArtist": "7eb1ce54-a355-41f9-8d68-e018b096d427",
            }
            ```
2. 构建艺人 Jellyfin Id 的 MusicBrainz Id 索引，仅用于下一步。
    ```json
    {
        "{Id}": {
            "ProviderIds": [{ProviderIds}]
        }
    }
    ```
3. 使用 API 获取用户所有曲目。
    - Curl 示例：
        ```bash
        curl -X 'GET' \
          '${URL}/Items?userId=${user_id}&recursive=true&fields=ProviderIds&includeItemTypes=Audio' \
          -H 'accept: application/json' \
          -H 'Authorization: MediaBrowser Client="${MEDIA_BROWSER_CLIENT}", Device="${DEVICE}", DeviceId="${device_id}", Version="${VERSION}", Token="${access_token}"'
        ```
    - 响应示例： @samples/response_items_audio.json 。
        - 其中 `ProviderIds` 示例。此处 `MusicBrainzArtist` 和 `MusicBrainzAlbumArtist` 不要后续使用。
            ```
            "ProviderIds": {
                "MusicBrainzArtist": "7eb1ce54-a355-41f9-8d68-e018b096d427",
                "MusicBrainzAlbumArtist": "7eb1ce54-a355-41f9-8d68-e018b096d427",
                "MusicBrainzAlbum": "7ec22411-8195-4630-97ae-dc0b82380c19",
                "MusicBrainzReleaseGroup": "d712ba0e-25fa-4c03-aecd-ec0c100deba5",
                "MusicBrainzTrack": "e0d6b2a9-5328-4750-977a-4a0d355ad197"
            }
            ```
4. 构建曲目索引。键名称构建为播放记录数据库的格式；`RunTimeTicks` 的单位为 100ns，转换为 ms；通过 `Artist[i].Id` 获取艺人的 MusicBrainz Id，并构建列表。属性缺失则不包含此键；如果有重复键，控制台输出。
    - JSON 格式：
        ```json
        {
            "{AlbumArtist} - {Name} ({Album})": {
                "Name": "Name",
                "RunTimeMs": {RunTimeTicks / 10000},
                "IndexNumber": {IndexNumber},
                "ParentIndexNumber": {ParentIndexNumber},
                "ProviderIds": [{ProviderIds}],
                "Artists": [{Artists}],
                "ArtistMusicBrainzIds": [{ArtistIndex.ProviderIds.MusicBrainzArtist}],
                "Album": "{Album}"
            }
        }
        ```
    - JSON 示例：
        ```json
        {
            "Various Artists - Shake It Off (Sing: Original Motion Picture Soundtrack)": {
                "Name": "Shake It Off",
                "RunTimeMs": 120607,
                "IndexNumber": 13,
                "ParentIndexNumber": 1,
                "ProviderIds": {
                    "MusicBrainzArtist": "816c4b57-b0a7-40cd-a652-45ac9491ca80",
                    "MusicBrainzAlbumArtist": "89ad4ac3-39f7-470e-963a-56509c546377",
                    "MusicBrainzAlbum": "0a8e5eb0-70b7-4b89-9a93-926e7afe332f",
                    "MusicBrainzReleaseGroup": "143792fa-0a91-4a9b-82e3-45b6c592c933",
                    "MusicBrainzTrack": "6ad2831f-6e18-422e-8353-5e33616a3028"
                },
                "Artists": ["Nick Kroll", "Reese Witherspoon"],
                "ArtistMusicBrainzIds": ["816c4b57-b0a7-40cd-a652-45ac9491ca80", "2316a4da-365a-4e46-85bf-febc6537cce9"],
                "Album": "Sing: Original Motion Picture Soundtrack"
            }
        }
        ```

### 3. 获取播放记录和提交

使用播放记录数据库从构建的索引获取数据，每 1000 个分组获取并提交到 ListenBrainz。

**注意**！`{TZ}` 时区配置只用于时间戳转换相关操作。其他操作（如范围限定），直接使用时间，不转换时区。

1. 从 `{PLAYBACK_REPORTING_DB}` (SQLite) 读取 `PlaybackActivity` 表。
    - 数据表结构示例： @samples/playback_reporting.PlaybackActivity.json 。
2. 从旧到新（即默认数据排序）筛选 `ItemType` 为 `Audio` 的项目。如果配置了 `{START_DATE}` 或 `{END_DATE}`，仅保留区间内的播放记录，只配置其中一个则单向保留。
5. 使用 `ItemName` 从构建的索引获取数据，如果未找到，控制台输出。播放记录数据库的 `PlayDuration` 单位为 sec。过滤并只保留满足以下条件之一的项目：
    - `PlayDuration` >= 240
    - `PlayDuration` \* 1000 >= `RunTimeMs` / 2
4. `DateCreated` 为 SQLite DATETIME 格式，形如 `2025-02-25 00:49:19.9841297`，字段内不带时区，视作是以 `{TZ}` 时区记录的，将其转换为时间戳 `DateCreatedTimestamp`。
6. 有用的数据：
    - `DateCreatedTimestamp`：开始播放时间戳。
    - `PlayDuration`：实际播放时长 (sec)。暂停记录在内，允许大于曲目总时长。
    - `ClientName`：Jellyfin 播放客户端名称。
    - 来自索引的属性。
7. 构建 payload。`duration_played` 单位为 sec。任何属性缺失，控制台输出并跳过此项。
    - JSON 格式：
        ```json
        {
            "listened_at": {DateCreatedTimestamp},
            "track_metadata": {
                "artist_name": "{', '.join(Artists)}",
                "release_name": "{Album}",
                "track_name": "{Name}",
                "additional_info": {
                    "artist_mbids": [{ArtistMusicBrainzIds}],
                    "release_group_mbid": "{ProviderIds.MusicBrainzReleaseGroup}",
                    "release_mbid": "{ProviderIds.MusicBrainzAlbum}",
                    "track_mbid": "{ProviderIds.MusicBrainzTrack}",
                    "tracknumber": {IndexNumber},
                    "discnumber": {ParentIndexNumber},
                    "media_player": "{ClientName}",
                    "submission_client": "{MEDIA_BROWSER_CLIENT}",
                    "submission_client_version": "{VERSION}",
                    "duration_ms": {RunTimeMs},
                    "duration_played": {PlayDuration}
                }
            }
        }
        ```
    - JSON 示例：
        ```json
        {
            "listened_at": 1680000000,
            "track_metadata": {
                "artist_name": "Nick Kroll, Reese Witherspoon",
                "release_name": "Sing: Original Motion Picture Soundtrack",
                "track_name": "Shake It Off",
                "additional_info": {
                    "artist_mbids": ["816c4b57-b0a7-40cd-a652-45ac9491ca80", "2316a4da-365a-4e46-85bf-febc6537cce9"],
                    "release_group_mbid": "143792fa-0a91-4a9b-82e3-45b6c592c933",
                    "release_mbid": "0a8e5eb0-70b7-4b89-9a93-926e7afe332f",
                    "track_mbid": "6ad2831f-6e18-422e-8353-5e33616a3028",
                    "tracknumber": 13,
                    "discnumber": 1,
                    "media_player": "Jellyfin Media Player",
                    "submission_client": "Scrobble Importer Jellyfin",
                    "submission_client_version": "0.1.0",
                    "duration_ms": 120607,
                    "duration_played": 147
                }
            }
        }
        ```
8. 1000 个或结束时构建完整请求体，并提交到 ListenBrainz。
    - 请求体：
        ```json
        {
            "listen_type": "import",
            "payload": [{payloads}]
        }
        ```
    - Curl 示例：
        ```bash
        curl -X 'POST' \
          '${LISTENBRAINZ_API_URL}/1/submit-listens' \
          -H 'accept: application/json' \
          -H 'Content-Type: application/json' \
          -H 'Authorization: Token ${LISTENBRAINZ_USER_TOKEN}' \
          -d '{
          "listen_type": "import",
          "payload": [{payloads}]
          }'
        ```
