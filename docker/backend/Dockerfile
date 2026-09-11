FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY agent/requirements.txt /app/agent/requirements.txt
COPY agent/requirements.lock /app/agent/requirements.lock
RUN pip install -r /app/agent/requirements.txt -c /app/agent/requirements.lock
COPY agent/ /app/agent/
COPY app/ /app/app/
EXPOSE 5000
CMD ["python", "-m", "uvicorn", "app.app_main:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1"]
