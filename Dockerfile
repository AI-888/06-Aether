FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Install Node.js 20 for the WhatsApp bridge
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg git && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Install kubectl
RUN curl -fsSL "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl && \
    chmod +x /usr/local/bin/kubectl && \
    kubectl version --client

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY pyproject.toml README.md LICENSE ./
RUN mkdir -p nanobot bridge && touch nanobot/__init__.py && \
    uv pip install --system --no-cache . && \
    rm -rf nanobot bridge

# Copy the full source and install
COPY nanobot/ nanobot/
COPY bridge/ bridge/
RUN uv pip install --system --no-cache .

# 验证nanobot模块可以正常导入
RUN python -c "import nanobot.cli.commands; print('nanobot import success')"

RUN ls -l bridge
RUN ls -l nanobot

# Build the WhatsApp bridge
WORKDIR /app/bridge
RUN npm install && npm run build
WORKDIR /app

# Create config directory and copy local ~/.nanobot contents
# 注意：.nanobot 目录由 build.sh 在构建前从 ~/.nanobot 复制到构建上下文
RUN mkdir -p /root/.nanobot
COPY --chown=root:root .nanobot/ /root/.nanobot/
COPY --chown=root:root config-template.json /root/.nanobot/
RUN ls -l /root/.nanobot

# Gateway default port
EXPOSE 8001

# 使用Python模块直接运行，避免PATH环境变量问题
ENTRYPOINT ["python", "-m", "nanobot.cli.commands"]
# 默认运行webui命令
CMD ["webui"]