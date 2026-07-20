# SkinVision AI 后端 Dockerfile(组员 3 · 一键起)
# 构建: docker build -t skinvision-api .
# 运行: docker run -p 8000:8000 --env-file backend/.env skinvision-api
FROM python:3.11-slim

# 时区 + 中文
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    TF_CPP_MIN_LOG_LEVEL=3 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

# 系统依赖(feedparser 等)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata curl && rm -rf /var/lib/apt/lists/*

# 先装依赖(利用缓存)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# 拷贝后端 + ml(模型/数据/预测CSV/outputs)
COPY backend/ /app/backend/
COPY ml/ /app/ml/

# Expo 种子 + 文档
COPY docs/ /app/docs/

WORKDIR /app/backend

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
