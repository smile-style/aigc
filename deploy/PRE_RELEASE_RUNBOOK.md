# Pre 环境发布操作手册

固定流程：在构建机拉取最新 Git 代码、构建并推送镜像，再到 Pre 机器拉取该镜像并运行。构建机与 Pre 机器可以相同，但构建目录与部署目录必须分开。

## 固定环境配置

```text
BUILD_HOST=xuzr@192.168.40.130
SSH_PASSWORD=123456789
BUILD_CODE_DIR=/data/docker-build/pre
GIT_BRANCH=codex/production-workflow-optimization

PRE_HOST=xuzr@192.168.40.130
PRE_DEPLOY_DIR=/data/aigc-pre/deploy/pre
PRE_DATA_DIR=/data/aigc_data/pre
PRE_URL=http://192.168.40.130:8080/

REGISTRY_HOST=registry.cn-hangzhou.aliyuncs.com
REGISTRY_USERNAME=15620776103
REGISTRY_PASSWORD=U7wDUVs%
IMAGE_REPOSITORY=registry.cn-hangzhou.aliyuncs.com/docker-registry-cache/aigc-studio
```

`SSH_PASSWORD` 和 `REGISTRY_PASSWORD` 是明文敏感信息。必须限制本文件及仓库访问权限，禁止在聊天消息、命令输出、日志或发布报告中打印密码。数据库密码、Django 密钥和 API 密钥不得写入本文档。

任何固定配置为空或仍有尖括号占位符时，必须停止发布。执行命令时应使用不会回显密码的认证方式。

### 强制命令约定

- 构建命令统一读取构建代码目录中的 `deploy/pre/.env`，不得使用 `deploy/build/.env`。
- 构建机和 Pre 机器上的所有 Docker CLI 命令统一使用 `sudo docker ...`，不得直接执行无 `sudo` 的 Docker 命令。
- 远程自动化使用密码 sudo 时必须分配伪终端，例如 `plink -t` 或 `ssh -t`，使 `sudo -v` 在同一会话中生效。
- 通过 sudo 执行 Compose 且需要覆盖镜像变量时，必须使用 `sudo env AIGC_IMAGE="${AIGC_IMAGE}" docker compose ...`，禁止把变量写在 `sudo` 前面。
- 每次新建 SSH 会话后，先关闭命令回显并执行以下命令刷新 sudo 凭据，再运行 Docker 命令：

```bash
set +x
printf '%s\n' '<SSH_PASSWORD>' | sudo -S -v
```

sudo 认证失败时立即停止发布。

## 一、发布前确认

- 构建机和 Pre 机器可使用固定 SSH 凭据访问。
- 本次发布使用 `GIT_BRANCH` 最新代码，或用户明确指定的 commit。
- 构建机已预先登录镜像仓库，构建阶段不再执行 `sudo docker login`。
- Pre 是否允许短暂停机。
- 本次是否修改数据库迁移、`docker-compose.yaml` 或环境变量结构。

常规发布不在 Pre 部署目录执行 `git pull`。如果 Compose 或环境变量结构变化，必须单独同步并审核部署配置。

### 发布模式

- **标准发布**：默认模式。用户说“发布到 Pre”但没有明确指定模式时，必须执行完整数据备份。
- **快速发布**：只有用户明确说“快速发布”或“快速发布到 Pre”时启用。快速发布跳过 MySQL、`media` 和 `workspace` 备份，直接进入镜像仓库登录、镜像拉取和运行步骤。
- 快速发布仍然必须执行代码拉取、镜像构建、测试、推送、配置校验、数据库迁移、健康检查和发布验收。
- 快速发布没有本次发布前的数据备份；如果数据库迁移不兼容或持久化数据损坏，无法依靠本次发布备份恢复。发布报告必须明确标注“快速发布：未备份数据”。

## 二、在构建机拉取最新代码

使用固定 SSH 凭据连接构建机，进入：

```bash
cd /data/docker-build/pre
```

拉取目标分支：

```bash
git fetch origin codex/production-workflow-optimization --tags --prune
git checkout codex/production-workflow-optimization
git pull --ff-only origin codex/production-workflow-optimization
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

用户指定 commit 时，在 `git fetch` 后改为执行：

```bash
git checkout --detach <full-git-commit>
```

## 三、在构建机构建镜像

构建机的 root Docker 配置已经预先完成镜像仓库认证，不执行 `sudo docker login`。认证失败时停止发布。构建命令统一读取构建代码目录中的 `deploy/pre/.env`，执行前必须确认该文件存在。

```bash
RELEASE_TIME=$(date +%Y%m%d-%H%M%S)
IMAGE_REPOSITORY=registry.cn-hangzhou.aliyuncs.com/docker-registry-cache/aigc-studio
AIGC_IMAGE="${IMAGE_REPOSITORY}:${RELEASE_TIME}-${GIT_SHA}"

echo "GIT_COMMIT=${GIT_COMMIT}"
echo "AIGC_IMAGE=${AIGC_IMAGE}"

sudo env AIGC_IMAGE="${AIGC_IMAGE}" \
  docker compose \
  --env-file deploy/pre/.env \
  -f deploy/build/docker-compose.yaml \
  build --pull aigc-studio-image
```

只有修改 `deploy/build/Dockerfile.base` 或基础系统依赖时，才重新构建并推送基础镜像：

```bash
sudo docker compose --env-file deploy/pre/.env \
  -f deploy/build/docker-compose.yaml build --pull aigc-base
sudo docker compose --env-file deploy/pre/.env \
  -f deploy/build/docker-compose.yaml push aigc-base
```

验证应用镜像：

```bash
sudo docker image inspect "${AIGC_IMAGE}" >/dev/null
sudo docker run --rm "${AIGC_IMAGE}" python manage.py check
sudo docker run --rm "${AIGC_IMAGE}" pytest -q
```

任一检查失败时停止发布。

## 四、从构建机推送镜像

```bash
sudo env AIGC_IMAGE="${AIGC_IMAGE}" \
  docker compose \
  --env-file deploy/pre/.env \
  -f deploy/build/docker-compose.yaml \
  push aigc-studio-image

echo "${AIGC_IMAGE}"
sudo docker image inspect "${AIGC_IMAGE}" --format='{{json .RepoDigests}}'
```

记录完整 Git commit、镜像地址和 digest。只有推送成功后才能进入 Pre 阶段。

## 五、连接 Pre 机器

使用固定 SSH 凭据连接 Pre 机器。如果与构建机相同，可以复用当前会话，但必须切换目录：

```bash
cd /data/aigc-pre/deploy/pre
pwd
test -f docker-compose.yaml
test -f .env
sudo docker --version
sudo docker compose version
```

不得在此目录构建镜像或执行 `git pull`。

## 六、备份已有 Pre 环境（仅标准发布）

快速发布必须完整跳过本节，不停止业务服务，也不创建 MySQL、`media` 或 `workspace` 备份，直接执行“七、在 Pre 机器登录镜像仓库”。

标准发布仅在首次部署且确认无历史数据时可以跳过备份。更新已有环境时执行：

```bash
sudo docker compose stop aigc-studio generation-worker video-worker publish-worker

BACKUP_TIME=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="/data/aigc_data/pre/backups/${BACKUP_TIME}"
sudo mkdir -p "${BACKUP_DIR}"
```

使用 MySQL 时：

```bash
sudo docker compose exec -T aigc-db sh -c \
  'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers "$MYSQL_DATABASE"' \
  | sudo tee "${BACKUP_DIR}/mysql.sql" >/dev/null
```

备份持久化文件：

```bash
sudo tar -C /data/aigc_data/pre \
  -czf "${BACKUP_DIR}/files.tar.gz" media workspace

sudo test -s "${BACKUP_DIR}/mysql.sql"
sudo test -s "${BACKUP_DIR}/files.tar.gz"
sudo ls -lh "${BACKUP_DIR}"
```

使用 SQLite 时，不执行 MySQL 备份及校验，改为：

```bash
sudo cp -a /data/aigc_data/pre/db.sqlite3 "${BACKUP_DIR}/db.sqlite3"
sudo test -s "${BACKUP_DIR}/db.sqlite3"
```

备份失败时停止发布并恢复旧服务：

```bash
sudo docker compose up -d
```

## 七、在 Pre 机器登录镜像仓库

使用固定配置中的镜像仓库凭据登录，禁止回显密码：

```bash
set +x
printf '%s' '<REGISTRY_PASSWORD>' | sudo docker login registry.cn-hangzhou.aliyuncs.com \
  --username '15620776103' --password-stdin
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

grep '^AIGC_IMAGE=' .env
sudo docker compose config --quiet
sudo docker compose pull
sudo docker compose up -d --remove-orphans
```

Compose 会执行 `python manage.py migrate --noinput`，迁移成功后再启动 Web 和各 Worker。

## 九、验证 Pre 发布结果

```bash
sudo docker compose ps -a
sudo docker compose logs --tail=100 migrate
sudo docker compose logs --tail=200 \
  aigc-studio generation-worker video-worker publish-worker

PRE_PORT=$(awk -F= '$1=="AIGC_HTTP_PORT" {print $2}' .env)
curl -fsS "http://127.0.0.1:${PRE_PORT}/" >/dev/null
```

`migrate` 应以状态码 `0` 退出。访问 `http://192.168.40.130:8080/`，验收本次发布影响的功能。

## 十、回滚

数据库向后兼容时，将 `.env` 中 `AIGC_IMAGE` 改回上一个不可变版本：

```bash
sudo docker compose config --quiet
sudo docker compose pull
sudo docker compose up -d --remove-orphans
sudo docker compose ps -a
```

不兼容数据库迁移不能只回滚镜像。恢复数据库或持久化文件前必须获得用户明确批准。

## 十一、发布报告

报告发布模式、构建机和目录、Pre 机器和目录、Git 分支与 commit、镜像地址与 digest、备份位置（快速发布标注“未备份数据”）、迁移结果、容器状态、健康检查、业务验收和回滚情况。禁止包含 SSH 密码、镜像仓库密码或其他密钥。
