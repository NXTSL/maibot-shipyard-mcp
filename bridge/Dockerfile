FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    "fastmcp>=2.12.2,<3" \
    "httpx>=0.27,<1" \
    "uvicorn>=0.30,<1"

RUN useradd -r -u 10001 -m -d /app -s /usr/sbin/nologin appuser \
    && mkdir -p /app/state \
    && chown -R appuser:appuser /app

COPY --chown=appuser:appuser bridge.py /app/bridge.py

USER appuser

EXPOSE 8124
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('BRIDGE_PORT','8124'), timeout=3).read()"

CMD ["python", "/app/bridge.py"]
