# 加固版 Shipyard ship 镜像
# 把沙盒的 /tmp 和 /var/tmp 重定向到 5G 盘上的 /home/tmp,
# 防止沙盒内 curl/dd 大文件写满宿主机大盘。
# 构建: docker build -t shipyard-ship-hardened .
# 注: 基础镜像 docker.1ms.run/... 是国内加速源,海外环境可换回 soulter/shipyard-ship:latest
FROM docker.1ms.run/soulter/shipyard-ship:latest

RUN rm -rf /tmp /var/tmp && ln -s /home/tmp /tmp && ln -s /home/tmp /var/tmp

COPY docker-entrypoint.sh /usr/local/bin/ship-entrypoint.sh
RUN chmod +x /usr/local/bin/ship-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/ship-entrypoint.sh"]
CMD ["python", "run.py"]
