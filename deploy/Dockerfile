FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir gunicorn==23.0.0
COPY alias_server/ /app/alias_server/
USER 1000:1000
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "30", "--worker-tmp-dir", "/tmp", "alias_server.app:from_environment()"]
