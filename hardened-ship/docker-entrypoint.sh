#!/bin/sh
# ship 容器启动包装:
# 容器启动时 /home 已被绑定挂载到 5G 盘,这里确保共享 tmp 目录存在,
# 然后原样执行原始 CMD(python run.py)。
set -e
mkdir -p /home/tmp
chmod 1777 /home/tmp
exec "$@"
