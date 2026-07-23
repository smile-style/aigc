# AI Comic Drama Generator

本项目是一个单用户 Django Web 工具，用于通过 OpenAI-compatible 大模型接口生成 AI 漫剧大纲、剧本和分镜 Prompt。

## 功能范围

- 按题材生成 6 个故事大纲候选。
- 选择一个大纲后生成 60 集分集剧本规划和第 1 集完整剧本样例。
- 基于第 1 集剧本生成 12 到 20 个分镜 Prompt。
- 使用 Django ORM 保存创作状态；本地默认 SQLite，Docker 部署默认 MySQL。

## 本地启动

1. 创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

2. 复制 `.env.example` 为 `.env`，并填写模型配置。默认 `DB_ENGINE=sqlite`，本地可以先不用 MySQL。

```dotenv
LLM_BASE_URL=https://api.example.com/v1
LLM_API_KEY=replace-with-your-key
LLM_MODEL=replace-with-model-name
DB_ENGINE=sqlite
```

3. 初始化数据库并启动 Django：

```powershell
python manage.py migrate
python manage.py runserver
```

4. 打开 `http://127.0.0.1:8000/`，按顺序完成：

- 选择题材并刷新大纲。
- 选择一个大纲并点击 AI 加工。
- 生成剧本。
- 生成分镜 Prompt。

## Docker 部署

Docker 部署文件位于 `deploy/`，镜像构建、生产环境和预发布环境分别放在 `build/`、`prod/` 和 `pre/`。

```bash
cp deploy/pre/.env.example deploy/pre/.env
touch deploy/pre/db.sqlite3
docker compose --env-file deploy/pre/.env \
  -f deploy/pre/docker-compose.yaml config
docker compose --env-file deploy/pre/.env \
  -f deploy/pre/docker-compose.yaml up -d
```

部署后访问：

```text
http://<server-ip-or-domain>:8081/
```

## 测试

```powershell
pytest -q
python manage.py check
```

## 数据存储

- Docker 部署：`prod/` 和 `pre/` 分别保存自己的 `db.sqlite3`、`media/` 与 `workspace/` 数据。
- 本地开发：未设置 `DB_ENGINE=mysql` 时使用 SQLite `db.sqlite3`。
- LLM 请求日志写入本地 `logs/llm.log`，Docker 环境写入对应运行目录的 `workspace/llm.log`。