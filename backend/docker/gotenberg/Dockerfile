# Gotenberg（封装 LibreOffice 的无状态文档转换服务）+ 焊死中文字体。
# 不验证、直接装一批开源 CJK 字体，保证转出来的 PDF 中文不出方框。
FROM gotenberg/gotenberg:8

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk fonts-noto-cjk-extra \
        fonts-wqy-zenhei fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*
USER gotenberg
