# Docker 部署

部署包含四个服务：`mysql`、一次性任务 `migrate`、Web 服务 `aigc-studio` 和视频任务服务 `video-worker`。镜像内已安装并校验 `ffmpeg` 与 `ffprobe`。

## 1. 准备配置

需要 Docker Engine 和 Docker Compose v2。

```bash
cp deploy/.env.example deploy/.env
```

至少修改 `DJANGO_SECRET_KEY`、域名、MySQL 密码，以及文本、图片和视频模型凭据。`DJANGO_SECRET_KEY` 用于加密保存在数据库中的模型 Token，投入使用后不要更换。不要提交 `deploy/.env`。

## 2. 构建基础镜像

基础镜像只包含 Python、FFmpeg、字体等系统运行环境，不包含项目代码或 Python 项目依赖。首次部署时先构建：

```bash
docker build \
  -f deploy/Dockerfile.base \
  -t registry.cn-hangzhou.aliyuncs.com/docker-registry-cache/aigc-studio-base:py3.12-v1 \
  .
```

日常修改 Python、模板、静态文件或 `requirements.txt` 时，不需要重新构建基础镜像；应用镜像会根据当前 `requirements.txt` 安装依赖。只有修改 Python 版本或系统包后，才应使用新标签重新构建基础镜像，并同步修改 `deploy/.env` 中的 `AIGC_BASE_IMAGE`。

在同一台机器上继续构建应用镜像时，本地的上述标签可以直接使用。需要在其他部署机器上拉取时，先登录并推送基础镜像：

```bash
docker login registry.cn-hangzhou.aliyuncs.com
docker push registry.cn-hangzhou.aliyuncs.com/docker-registry-cache/aigc-studio-base:py3.12-v1
```

## 3. 构建应用镜像

构建流程只生成 `AIGC_IMAGE` 指定的应用镜像，不会创建或启动业务容器：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.build.yml build --pull
```

## 4. 校验并启动

运行 Compose 只引用已构建的 `AIGC_IMAGE`，其中不包含 `build:` 配置。先检查展开结果：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml config
```

再启动服务；此命令不会构建应用镜像：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
```

查看状态：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
```

`migrate` 正常状态是 `Exited (0)`；其余三个服务应保持运行。MySQL 默认不暴露宿主机端口，只允许 Compose 内部服务访问。

访问地址：

```text
http://<服务器 IP 或域名>:8080/
```

## 5. 验证视频环境

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec aigc-studio ffmpeg -version
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec video-worker ffprobe -version
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f --tail=200 video-worker
```

成片导出提示“未找到 FFmpeg”通常说明仍在运行旧镜像。先用 `docker-compose.build.yml`
重新构建，再执行运行 Compose 的 `up -d`，然后检查版本。

## 6. 数据位置

运行数据保存在 Docker 命名卷中：

- `mysql_data`：项目、剧本、分镜、任务状态和模型配置。
- `aigc_media`：角色图片、镜头视频和成片文件。
- `aigc_logs`：模型请求日志。
- `aigc_workspace`：兼容性工作区和临时文件。

`docker compose down` 不会删除这些卷。生产环境不要使用 `down -v`，它会删除数据库和媒体数据。

## 7. 更新版本

```bash
git pull
docker compose --env-file deploy/.env -f deploy/docker-compose.build.yml build --pull
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
```

每次启动会先执行一次数据库迁移，成功后 Web 和 worker 才会更新。

上面的更新命令会重建应用镜像；`requirements.txt` 发生变化时会在应用镜像中重新安装依赖，无需重建基础镜像。

## 8. 备份

备份数据库：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec -T mysql \
  sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' \
  > aigc-studio.sql
```

先通过 `docker volume ls` 确认媒体卷名称，再备份媒体文件：

```bash
docker run --rm \
  -v <media-volume-name>:/data:ro \
  -v "$PWD":/backup \
  alpine tar czf /backup/aigc-media.tar.gz -C /data .
```

恢复前应停止 Web 和 worker，并同时确认数据库备份与媒体备份完整。

## 9. 常用排查

查看全部日志：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f --tail=200
```

如果 `migrate` 日志出现 MySQL `1045 Access denied`，说明数据库中的应用账号密码与
`deploy/.env` 中的 `MYSQL_PASSWORD` 不一致。MySQL 只在首次创建数据卷时读取这些
初始化变量，之后修改 `.env` 不会自动修改已有账号密码。

先用 root 账号进入 MySQL：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec mysql \
  mysql -uroot -p
```

输入 `MYSQL_ROOT_PASSWORD` 后，在 MySQL 中执行以下语句；将占位值替换为
`deploy/.env` 中的实际值：

```sql
CREATE USER IF NOT EXISTS 'aigc_studio'@'%' IDENTIFIED BY '<MYSQL_PASSWORD>';
ALTER USER 'aigc_studio'@'%' IDENTIFIED BY '<MYSQL_PASSWORD>';
GRANT ALL PRIVILEGES ON AIGC_STUDIO.* TO 'aigc_studio'@'%';
FLUSH PRIVILEGES;
```

上面使用的是默认用户名和数据库名；如果修改过 `MYSQL_USER` 或 `MYSQL_DATABASE`，SQL
中的值也要相应替换。

如果是全新部署且数据库中没有需要保留的数据，也可以停止服务后仅删除 MySQL 数据卷，
再重新启动，让 MySQL 按当前 `.env` 初始化。不要使用 `docker compose down -v`，它还会
删除媒体、日志和工作区卷。

重新运行迁移：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml run --rm migrate
```

检查 Django 配置：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec aigc-studio python manage.py check
```

停止并保留数据：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml down
```
