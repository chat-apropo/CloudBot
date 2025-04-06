FROM python:3.10.14-bullseye

WORKDIR /app

RUN \
  apt-get update && \
  apt-get install -y --no-install-recommends \
    enchant-2 \
    libenchant-2-2

COPY . /app
RUN \
  pip install --no-cache-dir -r requirements.txt && \
  pip install --no-cache-dir -r my_requirements.txt

CMD ["python", "-m", "cloudbot"]
