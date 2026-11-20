FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY aifeed ./aifeed
RUN pip install --no-cache-dir .
ENV DB_PATH=/data/aifeed.db OUT_DIR=/data/out IMG_DIR=/data/img
CMD ["python", "-m", "aifeed"]
