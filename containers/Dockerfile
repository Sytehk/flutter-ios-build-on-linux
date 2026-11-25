FROM ghcr.io/sweetpad-dev/sweetpad:0.1.29
RUN apt-get update && apt-get install -y --no-install-recommends ruby ruby-dev build-essential \
    && gem install cocoapods -v 1.16.2 --no-document \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends llvm rsync zip unzip \
    && ln -s "$(find /usr/lib/llvm-* -path '*/bin/llvm-lipo' -type f -print -quit)" /usr/local/bin/lipo \
    && lipo --version \
    && rm -rf /var/lib/apt/lists/*
