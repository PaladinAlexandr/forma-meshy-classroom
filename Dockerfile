FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
RUN useradd --create-home --uid 10001 studio && mkdir -p /app/media /app/staticfiles && chown -R studio:studio /app
COPY --chown=studio:studio . .
USER studio
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", "--access-logfile", "-"]
