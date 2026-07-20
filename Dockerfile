# SkinVision AI 后端 Dockerfile
# 构建: docker build -t skinvision-api .
# 运行: docker run -p 8000:8000 --env-file backend/.env -v sv-db:/app/backend/data skinvision-api
# 推荐: docker compose up --build -d
FROM python:3.11-slim

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    TF_CPP_MIN_LOG_LEVEL=3 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata curl && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ /app/backend/
COPY ml/ /app/ml/
COPY docs/ /app/docs/

RUN mkdir -p /app/backend/data

WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
