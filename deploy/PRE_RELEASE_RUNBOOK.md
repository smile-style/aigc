# Pre 环境发布操作手册

固定流程：在构建机拉取最新 Git 代码、构建镜像并推送到镜像仓库，然后在 Pre 机器拉取该镜像并运行。

构建机与 Pre 机器可以相同，但构建代码目录与 Pre 部署目录必须分别配置、分别使用。

## 固定环境配置

首次使用前填写以下配置。后续发布直接读取这些值，禁止临时猜测。任何尖括号占位符未替换时不得执行发布。

```text
# 构建机 SSH 地址，例如 root@192.168.1.10
BUILD_HOST=xuzr@192.168.40.130

# 构建机上的 Git 仓库根目录
BUILD_CODE_DIR=/data/docker-build/pre

# 默认发布分支，例如 main
GIT_BRANCH=codex/production-workflow-optimization

# Pre 机器 SSH 地址，允许与 BUILD_HOST 相同
PRE_HOST=xuzr@192.168.40.130

# Pre 部署目录，必须包含 docker-compose.yaml 和 .env
PRE_DEPLOY_DIR=/data/aigc-pre/deploy/pre

# Pre 持久化数据根目录
PRE_DATA_DIR=/data/aigc_data/pre

# 镜像仓库登录地址
REGISTRY_HOST=registry.cn-hangzhou.aliyuncs.com

# 镜像仓库用户名
REGISTRY_USERNAME=15620776103

# 镜像仓库密码
REGISTRY_PASSWORD=U7wDUVs%

# 不含标签的应用镜像地址
IMAGE_REPOSITORY=registry.cn-hangzhou.aliyuncs.com/docker-registry-cache/aigc-studio

# Pre 对外访问地址
PRE_URL=http://192.168.40.130:8080/
```

`REGISTRY_PASSWORD` 是明文敏感信息。按照当前约定保存在本文档中时，必须限制该文件及仓库的访问权限，不得在聊天消息、命令输出、日志或发布报告中打印密码。数据库密码、Django 密钥和 API 密钥不得写入本文档。

## 一、发布前确认

- 固定环境配置均已填写，没有尖括号占位符。
- 构建机和 Pre 机器可以通过 SSH 访问。
- 本次发布使用 `GIT_BRANCH` 最新代码，或用户明确指定的 commit。
- Pre 是否允许短暂停机。
- 本次是否修改了数据库迁移、`docker-compose.yaml` 或环境变量结构。

常规发布不会在 Pre 部署目录执行 `git pull`。如果 Compose 或环境变量结构发生变化，必须单独同步并审核部署配置。

## 二、在构建机拉取最新代码

```bash
ssh <BUILD_HOST>
cd <BUILD_CODE_DIR>

git fetch origin <GIT_BRANCH> --tags --prune
git checkout <GIT_BRANCH>
git pull --ff-only origin <GIT_BRANCH>
```

确认工作区干净并记录版本：

```bash
test -z "$(git status --porcelain)" || {
  echo "工作区存在未提交或未跟踪文件，停止发布。"
  exit 1
}

GIT_COMMIT=$(git rev-parse HEAD)
GIT_SHA=$(git rev-parse --short=12 HEAD)
git log -1 --format='commit=%H%ncommitted_at=%cI%nsubject=%s'
```

用户指定 commit 时，在 `git fetch` 后执行：

```bash
git checkout --detach <full-git-commit>
```

## 三、在构建机构建镜像

构建机已经预先完成镜像仓库认证，本阶段不执行 `docker login`。如果构建或推送提示未认证，停止发布并修复构建机认证状态。

生成不可变镜像标签：

```bash
RELEASE_TIME=$(date +%Y%m%d-%H%M%S)
IMAGE_REPOSITORY=<IMAGE_REPOSITORY>
AIGC_IMAGE="${IMAGE_REPOSITORY}:${RELEASE_TIME}-${GIT_SHA}"

echo "GIT_COMMIT=${GIT_COMMIT}"
echo "AIGC_IMAGE=${AIGC_IMAGE}"
```

构建应用镜像：

```bash
AIGC_IMAGE="${AIGC_IMAGE}" \
docker compose \
  --env-file deploy/build/.env \
  -f deploy/build/docker-compose.yaml \
  build --pull aigc-studio-image
```

只有修改 `deploy/build/Dockerfile.base` 或基础系统依赖时，才重新构建并推送基础镜像：

```bash
docker compose \
  --env-file deploy/build/.env \
  -f deploy/build/docker-compose.yaml \
  build --pull aigc-base

docker compose \
  --env-file deploy/build/.env \
  -f deploy/build/docker-compose.yaml \
  push aigc-base
```

验证应用镜像：

```bash
docker image inspect "${AIGC_IMAGE}" >/dev/null
docker run --rm "${AIGC_IMAGE}" python manage.py check
docker run --rm "${AIGC_IMAGE}" pytest -q
```

任一检查失败时停止发布。

## 四、从构建机推送镜像

构建机不再执行登录，直接推送：

```bash
AIGC_IMAGE="${AIGC_IMAGE}" \
docker compose \
  --env-file deploy/build/.env \
  -f deploy/build/docker-compose.yaml \
  push aigc-studio-image
```

记录镜像地址和 digest：

```bash
echo "${AIGC_IMAGE}"
docker image inspect "${AIGC_IMAGE}" --format='{{json .RepoDigests}}'
```

镜像推送成功后，保存以下信息，再进入 Pre 阶段：

```text
GIT_COMMIT=<完整 Git commit>
AIGC_IMAGE=<完整镜像地址和版本标签>
IMAGE_DIGEST=<镜像仓库 digest>
```

## 五、连接 Pre 机器

```bash
ssh <PRE_HOST>
cd <PRE_DEPLOY_DIR>

pwd
test -f docker-compose.yaml
test -f .env
docker --version
docker compose version
```

构建机和 Pre 机器相同时可以继续使用当前 SSH 会话，但必须切换到 `PRE_DEPLOY_DIR`。不得在该目录构建镜像或执行 `git pull`。

## 六、备份已有 Pre 环境

首次部署且确认没有历史数据时可以跳过。更新已有环境时先停止写入服务：

```bash
docker compose stop \
  aigc-studio \
  generation-worker \
  video-worker \
  publish-worker

BACKUP_TIME=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="<PRE_DATA_DIR>/backups/${BACKUP_TIME}"
sudo mkdir -p "${BACKUP_DIR}"
```

使用 MySQL 时备份数据库：

```bash
docker compose exec -T aigc-db sh -c \
  'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers "$MYSQL_DATABASE"' \
  | sudo tee "${BACKUP_DIR}/mysql.sql" >/dev/null
```

备份持久化文件并校验：

```bash
sudo tar -C <PRE_DATA_DIR> \
  -czf "${BACKUP_DIR}/files.tar.gz" \
  media workspace

sudo test -s "${BACKUP_DIR}/mysql.sql"
sudo test -s "${BACKUP_DIR}/files.tar.gz"
sudo ls -lh "${BACKUP_DIR}"
```

使用 SQLite 时，不执行 MySQL 备份及其校验，改为：

```bash
sudo cp -a <PRE_DATA_DIR>/db.sqlite3 "${BACKUP_DIR}/db.sqlite3"
sudo test -s "${BACKUP_DIR}/db.sqlite3"
```

备份失败时停止发布并恢复旧服务：

```bash
docker compose up -d
```

## 七、在 Pre 机器登录镜像仓库

使用固定环境配置中的账号密码登录。关闭 Shell 命令回显，禁止打印密码：

```bash
set +x
printf '%s' '<REGISTRY_PASSWORD>' | docker login <REGISTRY_HOST> \
  --username '<REGISTRY_USERNAME>' \
  --password-stdin
```

登录失败时停止发布。

## 八、在 Pre 机器拉取并运行镜像

将 `<AIGC_IMAGE>` 替换为构建阶段实际推送成功的完整镜像地址：

```bash
cp -a .env ".env.before-${BACKUP_TIME}"

if grep -q '^AIGC_IMAGE=' .env; then
  sed -i 's|^AIGC_IMAGE=.*$|AIGC_IMAGE=<AIGC_IMAGE>|' .env
else
  echo 'AIGC_IMAGE=<AIGC_IMAGE>' >> .env
fi
```

只确认镜像配置，不输出 `.env` 全文：

```bash
grep '^AIGC_IMAGE=' .env
docker compose config --quiet
```

拉取并运行：

```bash
docker compose pull
docker compose up -d --remove-orphans
```

Compose 会执行 `python manage.py migrate --noinput`，迁移成功后再启动 Web 和各 Worker。

## 九、验证 Pre 发布结果

```bash
docker compose ps -a
docker compose logs --tail=100 migrate

docker compose logs --tail=200 \
  aigc-studio \
  generation-worker \
  video-worker \
  publish-worker
```

`migrate` 应以状态码 `0` 退出。执行内部健康检查：

```bash
PRE_PORT=$(awk -F= '$1=="AIGC_HTTP_PORT" {print $2}' .env)
curl -fsS "http://127.0.0.1:${PRE_PORT}/" >/dev/null
```

访问 `<PRE_URL>`，验收本次发布影响的功能。标准验收范围包括首页、系统设置、已有项目数据、文本与图片生成、视频任务、字幕导出和平台发布任务。

## 十、回滚

数据库向后兼容时，将 `.env` 中的 `AIGC_IMAGE` 改回上一个不可变版本，然后执行：

```bash
docker compose config --quiet
docker compose pull
docker compose up -d --remove-orphans
docker compose ps -a
```

如果已经执行不兼容的数据库迁移，仅回滚镜像不够。恢复数据库或持久化文件前必须获得用户明确批准。

## 十一、发布报告

报告构建机和目录、Pre 机器和目录、Git 分支与 commit、镜像地址与 digest、备份位置、迁移结果、容器状态、健康检查、业务验收及回滚情况。禁止在报告中包含镜像仓库密码或其他密钥。
