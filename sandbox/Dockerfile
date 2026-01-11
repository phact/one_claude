FROM ubuntu:24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

# Install everything in a single layer
RUN apt-get update && apt-get install -y \
    tmux \
    tree \
    curl \
    git \
    vim \
    less \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/* /root/.npm

# Squash into single layer
FROM scratch
COPY --from=builder / /

ENV PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /workspace
CMD ["bash"]
