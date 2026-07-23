FROM python:3.11-slim

WORKDIR /app

# Install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot.py .

# Create data directory for queue DB
RUN mkdir -p /data

# Run as non-root
RUN useradd -m -u 1000 bot && chown -R bot:bot /app /data
USER bot

CMD ["python", "bot.py"]
