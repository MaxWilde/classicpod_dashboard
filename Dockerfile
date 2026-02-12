FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf \
    automake \
    build-essential \
    ca-certificates \
    ffmpeg \
    git \
    libavcodec-dev \
    libavfilter-dev \
    libavformat-dev \
    libavutil-dev \
    libglib2.0-dev \
    libgpod-dev \
    libjson-c-dev \
    libsqlite3-dev \
    libswresample-dev \
    libswscale-dev \
    libtool \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/MaxWilde/gpod-utils /tmp/gpod-utils \
    && cd /tmp/gpod-utils \
    && autoreconf --install \
    && autoconf \
    && ./configure \
    && make -j"$(nproc)" \
    && make install \
    && command -v gpod-ls >/dev/null \
    && rm -rf /tmp/gpod-utils

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "180", "--graceful-timeout", "30", "app:app"]
