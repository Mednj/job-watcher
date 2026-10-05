FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt requirements.browser.txt ./
RUN pip install --no-cache-dir -r requirements.browser.txt
COPY app ./app
RUN useradd --create-home watcher && mkdir /app/data && chown watcher:watcher /app/data
USER watcher
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
