FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
RUN DJANGO_DEBUG=1 RENDER= DATABASE_URL= POSTGRES_DB= python manage.py collectstatic --noinput
RUN chmod +x /app/deploy/entrypoint.sh /app/deploy/start.sh
EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["/app/deploy/start.sh"]
