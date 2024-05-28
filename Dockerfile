FROM python:3.12.3-slim-bullseye

WORKDIR /app
COPY . /app

RUN \
  pip install --no-cache-dir -r requirements.txt && \
  pip install --no-cache-dir my_requirements.txt

CMD ["python", "-m", "cloudbot"]
