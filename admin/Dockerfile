FROM python:3.12-alpine
WORKDIR /app
RUN apk add --no-cache ttf-dejavu && \
    pip install --no-cache-dir flask psycopg2-binary gunicorn fpdf2
COPY app.py .
COPY templates/ templates/
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "30", "app:app"]