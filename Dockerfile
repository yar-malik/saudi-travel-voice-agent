FROM python:3.12-slim

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Runs as a non-root user: this is meant to sit inside your network, and the
# first thing your security review will ask is whether it needs root. It does not.
RUN useradd --create-home --uid 10001 agent && chown -R agent:agent /app
USER agent

EXPOSE 5000
ENV PORT=5000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health').status==200 else 1)"

CMD ["python", "server.py"]
