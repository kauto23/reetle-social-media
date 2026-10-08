FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

# The GitHub token (for the private reetle-models repo) arrives as a BuildKit
# secret: it is mounted only for this step and never stored in the image or its
# history, unlike a build argument.
RUN --mount=type=secret,id=github_token \
    GITHUB_TOKEN="$(cat /run/secrets/github_token 2>/dev/null || true)" \
    && if [ -n "$GITHUB_TOKEN" ]; then git config --global url."https://${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"; fi \
    && pip install --no-cache-dir -r requirements.txt \
    && rm -f /root/.gitconfig

COPY . /app

ENV ENVIRONMENT=cloud

CMD ["python", "main.py"]
