# The base image is named here (build.yaml and BUILD_FROM are no longer used). Pinned to an
# exact release so rebuilds are reproducible; Python 3.12 is the version the tests run on.
FROM ghcr.io/home-assistant/base-python:3.12-alpine3.24-2026.08.0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:$PATH

# A virtual environment keeps the app's packages apart from the base image's own.
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

WORKDIR /opt/app
COPY app ./app
COPY entrypoint.py log_config.json ./

COPY run.sh /run.sh
RUN chmod a+x /run.sh

# No EXPOSE and no published ports: the app is reached only through Home Assistant ingress.
CMD ["/run.sh"]
