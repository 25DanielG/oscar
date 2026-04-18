FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY oscar/ oscar/

RUN pip install --no-cache-dir -e .

CMD ["oscar", "monitor"]
