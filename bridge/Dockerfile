FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple fastmcp httpx

COPY bridge.py /app/bridge.py

EXPOSE 8124
CMD ["python", "/app/bridge.py"]
