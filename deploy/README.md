# Docker 部署

部署包含四个服务：`mysql`、一次性任务 `migrate`、Web 服务 `aigc-studio` 和视频任务服务 `video-worker`。镜像内已安装并校验 `ffmpeg` 与 `ffprobe`。

## 1. 准备配置

需要 Docker Engine 和 Docker Compose v2。

```bash
cp deploy/.env.example deploy/.env
```

至少修改 `DJANGO_SECRET_KEY`、域名、MySQL 密码，以及文本、图片和视频模型凭据。`DJANGO_SECRET_KEY` 用于加密保存在数据库中的模型 Token，投入使用后不要更换。不要提交 `deploy/.env`。

## 2. 构建基础镜像

基础镜像包含 Python、FFmpeg 和 `requirements.txt` 中的依赖。首次部署时先构建：

```bash
docker build \
  -f deploy/Dockerfile.base \
  -t aigc-studio-base:py3.12-v1 \
  .
```

日常只修改 Python、模板或静态文件时，不需要重新构建基础镜像。修改 `requirements.txt`、Python 版本或系统包后，应使用新标签重新构建，并同步修改 `deploy/.env` 中的 `AIGC_BASE_IMAGE`。

生产环境可将基础镜像推送到私有镜像仓库，再把 `AIGC_BASE_IMAGE` 设置为完整仓库地址，部署机器便不需要重复构建基础层。

## 3. 校验并启动

先检查 Compose 展开结果：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml config
```

构建应用代码层并启动：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
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

## 4. 验证视频环境

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec aigc-studio ffmpeg -version
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec video-worker ffprobe -version
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f --tail=200 video-worker
```

成片导出提示“未找到 FFmpeg”通常说明仍在运行旧镜像。重新执行 `up -d --build` 后再检查版本。

## 5. 数据位置

运行数据保存在 Docker 命名卷中：

- `mysql_data`：项目、剧本、分镜、任务状态和模型配置。
- `aigc_media`：角色图片、镜头视频和成片文件。
- `aigc_logs`：模型请求日志。
- `aigc_workspace`：兼容性工作区和临时文件。

`docker compose down` 不会删除这些卷。生产环境不要使用 `down -v`，它会删除数据库和媒体数据。

## 6. 更新版本

```bash
git pull
docker compose --env-file deploy/.env -f deploy/docker-compose.yml build --pull
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
```

每次启动会先执行一次数据库迁移，成功后 Web 和 worker 才会更新。

上面的更新命令只重建应用代码层。依赖发生变化时，先按照“构建基础镜像”步骤生成新标签，再更新 `AIGC_BASE_IMAGE`。

## 7. 备份

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

## 8. 常用排查

查看全部日志：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f --tail=200
```

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
