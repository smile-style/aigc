# Docker 部署

默认使用 `deploy/docker-compose.yml` 部署 MySQL 版本，共包含五个服务：`mysql`、一次性数据库迁移任务 `migrate`、Web 服务 `aigc-studio`、视频任务服务 `video-worker` 和发布任务服务 `publish-worker`。应用镜像内已安装并校验 `ffmpeg` 与 `ffprobe`。

以下命令都在项目根目录执行。

## 1. 拉取代码并准备配置

服务器需要安装 Git、Docker Engine 和 Docker Compose v2，并配置好访问 GitHub 仓库的 SSH Key。

```bash
git clone git@github.com:smile-style/aigc.git
cd aigc
cp deploy/.env.example deploy/.env
```

编辑 `deploy/.env`，至少完成以下配置：

```dotenv
AIGC_IMAGE=registry.cn-hangzhou.aliyuncs.com/docker-registry-cache/aigc-studio:latest
AIGC_BASE_IMAGE=registry.cn-hangzhou.aliyuncs.com/docker-registry-cache/aigc-studio-base:py3.12-v1

DJANGO_SECRET_KEY=<生成一个足够长的随机字符串>
DJANGO_ALLOWED_HOSTS=<服务器 IP 或域名>
DJANGO_CSRF_TRUSTED_ORIGINS=http://<服务器 IP 或域名>:8080

MYSQL_ROOT_PASSWORD=<MySQL root 密码>
MYSQL_PASSWORD=<MySQL 应用账号密码>
```

同时检查文本、图片和视频模型凭据。`DJANGO_SECRET_KEY` 用于加密保存在数据库中的模型 Token，投入使用后不要更换。`deploy/.env` 包含密钥和密码，不要提交到 Git。

示例使用 `latest` 标签。生产发布建议改成不可变版本标签，例如 `v1.0.0` 或 Git Commit SHA；构建机与部署机的 `AIGC_IMAGE` 必须保持一致。

## 2. 登录镜像仓库

先在阿里云容器镜像服务中确认命名空间 `docker-registry-cache` 下已创建 `aigc-studio` 和 `aigc-studio-base` 两个镜像仓库，并且当前账号有推送权限。然后在构建机和部署机上登录：

```bash
docker login registry.cn-hangzhou.aliyuncs.com
```

按提示输入阿里云镜像仓库用户名和密码。不要把密码直接写在命令或脚本中。

## 3. 首次构建并推送基础镜像

应用镜像依赖基础镜像。基础镜像只包含 Python、FFmpeg、字体等系统运行环境，不包含项目代码或 Python 项目依赖。镜像仓库中已有对应版本时，可以跳过本节。

```bash
docker build \
  -f deploy/Dockerfile.base \
  -t registry.cn-hangzhou.aliyuncs.com/docker-registry-cache/aigc-studio-base:py3.12-v1 \
  .

docker push registry.cn-hangzhou.aliyuncs.com/docker-registry-cache/aigc-studio-base:py3.12-v1
```

日常修改 Python 代码、模板、静态文件或 `requirements.txt` 时，不需要重新构建基础镜像。只有 Python 版本或基础系统包发生变化时，才使用新标签重建基础镜像，并同步修改 `deploy/.env` 中的 `AIGC_BASE_IMAGE`。

## 4. 构建并推送应用镜像

下面的构建只生成 `AIGC_IMAGE` 指定的应用镜像，不会启动业务容器：

```bash
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.build.yml build --pull
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.build.yml push
```

推送完成后，可确认镜像已存在于本地且名称正确：

```bash
docker image inspect registry.cn-hangzhou.aliyuncs.com/docker-registry-cache/aigc-studio:latest
```

如果使用的不是 `latest`，将命令中的标签替换为 `deploy/.env` 里 `AIGC_IMAGE` 的实际标签。

## 5. 使用 Docker Compose 启动

部署 Compose 只引用已推送的 `AIGC_IMAGE`，不包含 `build:` 配置。先拉取镜像并检查最终配置：

```bash
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml pull
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml config
```

确认无误后启动：

```bash
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml up -d
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml ps
```

`migrate` 正常状态是 `Exited (0)`；`mysql`、`aigc-studio`、`video-worker` 和 `publish-worker` 应保持运行或健康。若迁移失败，先查看日志：

```bash
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml logs migrate
```

MySQL 默认不暴露宿主机端口，只允许 Compose 内部服务访问。默认访问地址为：

```text
http://<服务器 IP 或域名>:8080/
```

## 6. 验证视频环境

```bash
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml exec aigc-studio ffmpeg -version
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml exec video-worker ffprobe -version
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml logs -f --tail=200 video-worker
```

成片导出提示“未找到 FFmpeg”通常说明仍在运行旧镜像。先用 `docker-compose.build.yml`
重新构建，再执行运行 Compose 的 `up -d`，然后检查版本。

## 7. 数据位置

运行数据保存在 Docker 命名卷中：

- `mysql_data`：项目、剧本、分镜、任务状态和模型配置。
- `aigc_media`：角色图片、镜头视频和成片文件。
- `aigc_logs`：模型请求日志。
- `aigc_workspace`：兼容性工作区和临时文件。

`docker compose down` 不会删除这些卷。生产环境不要使用 `down -v`，它会删除数据库和媒体数据。

## 8. 更新版本

在构建机拉取代码、构建并推送新镜像：

```bash
git pull --ff-only
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.build.yml build --pull
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.build.yml push
```

在部署机拉取新镜像并重建容器：

```bash
git pull --ff-only
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml pull
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml up -d
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml ps
```

如果构建和部署在同一台机器，两组命令按顺序执行即可。每次启动会先执行数据库迁移，成功后 Web 和 worker 才会更新。`requirements.txt` 发生变化时会在应用镜像中重新安装依赖，无需重建基础镜像。

## 9. 备份

备份数据库：

```bash
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml exec -T mysql \
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

## 10. 常用排查

查看全部日志：

```bash
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml logs -f --tail=200
```

如果 `migrate` 日志出现 MySQL `1045 Access denied`，说明数据库中的应用账号密码与
`deploy/.env` 中的 `MYSQL_PASSWORD` 不一致。MySQL 只在首次创建数据卷时读取这些
初始化变量，之后修改 `.env` 不会自动修改已有账号密码。

先用 root 账号进入 MySQL：

```bash
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml exec mysql \
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
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml run --rm migrate
```

检查 Django 配置：

```bash
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml exec aigc-studio python manage.py check
```

停止并保留数据：

```bash
docker compose --env-file /data/aigc/deploy/.env -f /data/aigc/deploy/docker-compose.yml down
```
