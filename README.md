# AI Comic Drama Generator

本项目是一个本地单用户 Django Web 工具，用于通过 OpenAI-compatible 大模型接口生成 AI 漫剧大纲、剧本和分镜 Prompt。

## 功能范围

- 按题材生成 6 个故事大纲候选。
- 选择一个大纲后生成 60 集分集剧本大纲和第 1 集完整剧本样例。
- 基于第 1 集剧本生成 12 到 20 个分镜 Prompt。
- 使用本地 `workspace/*.json` 保存创作状态。

## 本地启动

1. 创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

2. 复制 `.env.example` 为 `.env`，并填写模型配置：

```dotenv
LLM_BASE_URL=https://api.example.com/v1
LLM_API_KEY=replace-with-your-key
LLM_MODEL=replace-with-model-name
```

3. 启动 Django：

```powershell
python manage.py runserver
```

4. 打开 `http://127.0.0.1:8000/`，按顺序完成：

- 选择题材并刷新大纲。
- 选择一个大纲并点击 AI 加工。
- 生成剧本。
- 生成分镜 Prompt。

## 测试

```powershell
pytest -q
python manage.py check
```

## 本地数据

生成过程会写入 `workspace/*.json`。这些文件用于保存本地创作状态，已在 `.gitignore` 中排除，不提交到 git。

