FROM python:3.12-slim

# Создание непривилегированного пользователя appuser
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin -c "Docker app user" appuser

WORKDIR /app

# Копирование зависимостей и установка
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода проекта
COPY . .

# Переключение на непривилегированного пользователя
RUN chown -R appuser:appuser /app
USER appuser

# Настройка PYTHONPATH
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "app.main"]
